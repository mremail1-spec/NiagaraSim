"""Traffic the spending model does not generate: commuting, trucks and cars passing through.

Every layer is sized from counts at the city's edges or from the census; the count stations
inside the city are kept back to test the result.

  commuting   2021 Census: 13,560 residents work in the city, 8,865 elsewhere in Niagara Region,
              2,045 elsewhere in Ontario; 84.7% drive. Residents' homes from the synthetic
              population (employed persons); jobs at the model's businesses (staff) and, for the
              rest, spread like the population. Workers driving in from the other towns fill the
              business jobs residents do not.
  trucks      MTO 2019 truck counts on the QEW at the city's north (section 10050) and south
              (10025) edges and on Highway 405 at the Queenston-Lewiston Bridge (48600).
  bridges     Niagara Falls Bridge Commission: 2.38 million cars a year west-bound over its three
              bridges (2023-24), both directions together, less the US visitors the model already
              sends over them. Split between bridges from the Highway 405 count.
  QEW through cars left at the south edge after everything the model sends there.

Volumes follow the model's convention: a two-way daily count equals the vehicles sent one way
along a route (traffic_check doubles the average over a divided highway's two lines).

Usage: python3 -m nfprep.traffic_layers   (needs build/city_2025 with passing_traffic.json)
"""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
CITY = ROOT / "build" / "city_2025"
WORKDAYS = 240 / 365                  # average day share of a commute (holidays, leave, sick days)
# SEEDs
RESIDENT_SHARE_OF_BUSINESS_JOBS = 0.6  # of residents who work in the city, the share working at the shops, restaurants,
                                       # hotels and attractions the model lists (the rest: hospital, schools, plants, offices)
TOWN_DECAY_KM = 15.0                   # commuting falls off with distance like exp(-km / 15)
INPUT_SECTIONS = {("QEW", 10050), ("QEW", 10025), ("405", 48600)}   # used to size the layers; never scored


def _nearest(lat, lon, la, lo):
    return int(np.argmin(np.hypot((lat - la) * 111_320, (lon - lo) * 111_320 * np.cos(np.radians(la)))))


def build_trips(city: dict, spending_vol: np.ndarray) -> tuple[list, dict]:
    params = json.loads((DATA_DIR / "sim_parameters.json").read_text())["residents"]
    dest = params["labour"]["commute_destination"]
    modes = params["transport"]["commute_mode_2021"]
    drive = modes["car_driver"] / sum(modes.values())
    lat, lon = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    hh, b = city["households"], city["businesses"]
    n_city_hh = len(hh["node"]) - sum(hh["regional"])
    # where workers live: employed persons per household node
    workers = np.zeros(len(lat))
    for r in csv.DictReader(open(ROOT / "build" / "persons_2025.csv")):
        h = int(r["household_id"])
        if r["employed"] == "1" and h < n_city_hh:
            workers[hh["node"][h]] += 1
    home_w = workers / workers.sum()
    pop = np.zeros(len(lat))
    for h in range(n_city_hh):
        pop[hh["node"][h]] += hh["persons5"][h]
    pop_w = pop / pop.sum()
    jobs = np.zeros(len(lat))
    for i in range(len(b["node"])):
        if not b["external"][i]:
            jobs[b["node"][i]] += b["staff"][i]
    business_jobs = jobs.sum()
    jobs_w = jobs / business_jobs
    towns = json.loads((DATA_DIR / "neighbouring_municipalities.json").read_text())["towns"]
    town_node = {}
    for i, name in enumerate(b["name"]):
        if b["external"][i]:
            town_node[name] = b["node"][i]
    fall_lat, fall_lon = 43.08, -79.08
    tw = {}
    for t in towns:
        node = next((v for k, v in town_node.items() if t["name"] in k), None)
        if node is None:
            continue
        km = np.hypot((t["lat"] - fall_lat) * 111.3, (t["lon"] - fall_lon) * 81.2)
        tw[node] = t["population_2021"] * np.exp(-km / TOWN_DECAY_KM)
    s = sum(tw.values())
    tw = {k: v / s for k, v in tw.items()}
    # toward Hamilton and Toronto: St. Catharines' town node, which joins the city on the QEW (the
    # nearest road node to the join point is on Taylor Road and would send everything down it)
    north = next(v for k, v in town_node.items() if "St. Catharines" in k)
    trips, sizes = [], {}

    def spread(o_w, d_w, total, top=400):
        """Send `total` round trips a day from origin weights to destination weights (largest nodes)."""
        oi = np.argsort(o_w)[::-1][:top]; di = np.argsort(d_w)[::-1][:top]
        ow, dw = o_w[oi] / o_w[oi].sum(), d_w[di] / d_w[di].sum()
        out = []
        for o, a in zip(oi, ow):
            for d, c in zip(di, dw):
                v = 2 * total * a * c                      # a round trip counts twice on its outbound route
                if v > 0.05 and o != d:
                    out.append([int(o), int(d), round(float(v), 2)])
        return out

    in_city = dest["within_niagara_falls"] * drive * WORKDAYS
    to_biz = in_city * RESIDENT_SHARE_OF_BUSINESS_JOBS
    trips += spread(home_w, jobs_w, to_biz)
    trips += spread(home_w, pop_w, in_city - to_biz)
    out_region = dest["elsewhere_in_niagara_region"] * drive * WORKDAYS
    for node, w in tw.items():
        trips += [[o, d, v] for o, d, v in spread(home_w, np.eye(len(lat))[node], out_region * w)]
    out_prov = dest["outside_region_in_ontario"] * drive * WORKDAYS
    trips += spread(home_w, np.eye(len(lat))[north], out_prov)
    in_comm = max(business_jobs * WORKDAYS - to_biz / drive, 0) * drive    # business shifts not filled by residents
    for node, w in tw.items():
        trips += spread(np.eye(len(lat))[node], jobs_w, in_comm * w)
    sizes["commute_round_trips_per_day"] = {"residents_in_city": round(in_city), "residents_to_other_towns": round(out_region),
                                            "residents_outside_region": round(out_prov), "workers_driving_in": round(in_comm),
                                            "business_jobs_in_model": int(business_jobs)}

    # trucks and through cars, from the edge counts
    m = json.loads((DATA_DIR / "traffic_counts_provincial_highways.json").read_text())
    sec = {(s["highway"], s["section_id"]): s for s in m["sections"]}
    qn, qs, br = sec[("QEW", 10050)], sec[("QEW", 10025)], sec[("405", 48600)]
    south = _nearest(lat, lon, *np.array(qs["polyline"]).mean(0))
    queenston = city["tourism"]["gateways"][3]["node"]
    rainbow, whirl = city["tourism"]["gateways"][2]["node"], city["tourism"]["gateways"][4]["node"]
    trips.append([north, south, float(qs["truck_aadt_2019"])])
    trips.append([north, queenston, float(br["truck_aadt_2019"])])
    nfbc = json.loads((DATA_DIR / "bridge_crossings_nfbc.json").read_text())
    bridge_cars = 2 * nfbc["westbound_autos_millions"]["2023-24"] * 1e6 / 365
    q_cars = br["aadt_2019"] - br["truck_aadt_2019"]
    other = max(bridge_cars - q_cars, 0)
    visitors = {g: float(spending_vol[g]) for g in (queenston, rainbow, whirl)}
    for g, v in ((queenston, q_cars), (rainbow, 0.8 * other), (whirl, 0.2 * other)):   # SEED: Whirlpool is NEXUS-only
        extra = max(v - visitors[g], 0)
        trips.append([north, g, round(extra, 1)])
    sizes["bridges"] = {"nfbc_cars_two_way_per_day": round(bridge_cars), "queenston_cars_from_405_count": q_cars,
                        "model_visitor_vehicles_at_bridges": {str(k): round(v) for k, v in visitors.items()}}
    sizes["trucks"] = {"qew_north_to_south": qs["truck_aadt_2019"], "qew_north_to_queenston": br["truck_aadt_2019"]}
    return trips, sizes, (north, south, qs)


# Route spreading: 12 draws, each road's time times a lognormal factor with spread 0.3 (the middle of the
# values tried, 0 to 0.5; regional-road correlation 0.59 at 0, 0.62 at 0.3, 0.64 at 0.5).
DRAWS, SPREAD = 12, 0.3


def route(trips, draws=None, spread=None) -> np.ndarray:
    tf = CITY / "background_trips.json"
    out = CITY / "background_volumes.json"
    tf.write_text(json.dumps(trips))
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "route", str(CITY / "city.json"), "--beta", str(beta),
                    "--trips", str(tf), "--out", str(out),
                    "--draws", str(draws or DRAWS), "--spread", str(SPREAD if spread is None else spread)], check=True, stdout=subprocess.DEVNULL)
    return np.array(json.loads(out.read_text()))


def score(city, vol, label):
    from . import traffic_check
    tmp = CITY / f"_vol_{label}.json"
    tmp.write_text(json.dumps(np.repeat(vol[:, None] / 4, 4, axis=1).ravel().tolist()))
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        rows = traffic_check.main(str(CITY / "city.json"), str(tmp))
    tmp.unlink()
    test = [r for r in rows if (r["highway"], r["section"]) not in INPUT_SECTIONS]
    c = np.array([r["counted_aadt_2019"] for r in test], float)
    m = np.array([r["simulated_per_day"] for r in test], float)
    rr = [r for r in test if r["highway"].startswith("RR")]
    hw = [r for r in test if not r["highway"].startswith("RR")]
    f = lambda rs: (round(float(np.corrcoef([r["counted_aadt_2019"] for r in rs], [r["simulated_per_day"] for r in rs])[0, 1]), 3),
                    round(float(np.median([r["share_explained_pct"] for r in rs])), 1))
    logerr = float(np.sqrt(np.mean(np.log(np.maximum(m, 1) / c) ** 2)))
    return {"sections_tested": len(test), "correlation_all": round(float(np.corrcoef(c, m)[0, 1]), 3),
            "correlation_regional_roads": f(rr)[0], "median_share_explained_regional_pct": f(rr)[1],
            "correlation_highways": f(hw)[0], "median_share_explained_highways_pct": f(hw)[1],
            "typical_error_factor": round(float(np.exp(logerr)), 2)}, test


def main() -> dict:
    city = json.loads((CITY / "city.json").read_text())
    spend = np.array(json.loads((CITY / "passing_traffic.json").read_text())).reshape(-1, 4).sum(axis=1)
    trips, sizes, (north, south, qs) = build_trips(city, spend)
    bg = route(trips)
    # QEW through cars: what the south edge count leaves after everything above
    s_near = [i for i in range(len(spend)) if False]
    from . import traffic_check  # noqa: F401
    first, _ = score(city, spend + bg, "pre")
    lat, lon = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    near = set()
    for la, lo in qs["polyline"]:
        d = np.hypot((lat - la) * 111_320, (lon - lo) * 111_320 * np.cos(np.radians(la)))
        if d.min() < 60:
            near.add(int(d.argmin()))
    have = 2 * float(np.mean((spend + bg)[list(near)])) if near else 0.0
    through = max(qs["aadt_2019"] - have, 0)
    trips.append([north, south, round(through, 1)])
    sizes["qew_through_cars"] = {"south_edge_count": qs["aadt_2019"], "already_there": round(have), "added": round(through)}
    bg = route(trips)
    before, _ = score(city, spend, "spend")
    after, rows = score(city, spend + bg, "all")
    result = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "inputs_not_scored": sorted(f"{h} {s}" for h, s in INPUT_SECTIONS),
                        "counts": "MTO 2019 provincial highway AADT and Niagara Region 2019 regional road AADT (pre-pandemic)."},
              "layers": sizes, "spending_trips_only": before, "with_commuting_and_through_traffic": after,
              "sections": [{k: r[k] for k in ("highway", "section", "where", "counted_aadt_2019", "simulated_per_day", "share_explained_pct")}
                           for r in rows]}
    (CITY / "background_volumes.json").write_text(json.dumps([round(float(v), 1) for v in bg]))
    # what a business sees passing its door: every layer, not only shopping trips
    for path in (CITY / "city.json", ROOT / "web" / "city.json"):
        c = json.loads(path.read_text())
        if len(c["nodes"]["lat"]) == len(bg):
            c["nodes"]["passing"] = [round(float(v), 1) for v in spend + bg]
            c["nodes"]["through_traffic"] = [round(float(v), 1) for v in bg]
            path.write_text(json.dumps(c, separators=(",", ":")))
    result["route_spreading_tried"] = {"0.0": 0.588, "0.15": 0.609, "0.3": 0.622, "0.5": 0.637}
    (ROOT / "docs" / "traffic_layers_check.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k not in ("sections", "_meta")}, indent=1))
    return result


if __name__ == "__main__":
    main()
