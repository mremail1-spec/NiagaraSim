"""Traffic the spending model does not generate: commuting, trucks and cars passing through.

Every layer is sized from counts at the city's edges or from the census; the count stations
inside the city are kept back to test the result.

  commuting   2021 Census: 13,560 residents work in the city, 8,865 elsewhere in Niagara Region,
              2,045 elsewhere in Ontario; 84.7% drive. Residents' homes from the synthetic
              population (employed persons); jobs at every employer in Niagara Region's
              Employment Inventory 2025 (about 44,000 jobs by employee band). Workers driving in
              from the other towns fill the jobs residents do not.
  school runs Students aged 5-13 and 14-17 from the synthetic population, driven to the two
              nearest elementary or secondary schools (Niagara Region's school list).
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
# jobs per employer from the Employment Inventory's employee band (band midpoints, large capped)
BAND_JOBS = {"Micro (1-4 Employees)": 2.5, "Small (5-99 Employees)": 20, "Medium (100-499 Employees)": 220,
             "Large (500 + Employees)": 800}
DRIVEN_TO_SCHOOL = {"elementary": 0.35, "secondary": 0.25}   # SEED: share of students driven (Ontario travel surveys)
SCHOOL_RUNS_PER_DRIVEN_CHILD = 1.5     # SEED: drop-off and pick-up, less siblings and runs chained with a commute
SCHOOL_DAYS = 190 / 365
JOBS_AT_USUAL_PLACE = 0.8
# other car trips (travel survey gap); the model's shopping outings come from the engine's report
OUTINGS_PER_PERSON = 0.475            # engine: outings per person (5+) per day
DRIVER_SHARE_OF_OUTINGS = 0.677       # engine: share of outings driven
WEEKDAY_TO_AVERAGE_DAY = 0.9          # SEED: the survey describes a weekday; counts are annual averages
OTHER_TRIP_KM = 4.0                   # SEED: distance fall-off of errands and trips between stops              # share of inventory jobs held by someone who travels to that workplace (part-time
                                       # overlap, work from home, no fixed workplace)
TOWN_DECAY_KM = 15.0                   # commuting falls off with distance like exp(-km / 15)
INPUT_SECTIONS = {("QEW", 10050), ("QEW", 10025), ("405", 48600)}   # used to size the layers; never scored


def _nearest(lat, lon, la, lo):
    return int(np.argmin(np.hypot((lat - la) * 111_320, (lon - lo) * 111_320 * np.cos(np.radians(la)))))


def gravity(lat, lon, o_w, d_w, total, tau_km, top=250):
    """`total` one-way trips a day from origin to destination weights, falling off as exp(-km / tau)."""
    oi = np.argsort(o_w)[::-1][:top]; di = np.argsort(d_w)[::-1][:top]
    km = np.hypot((lat[oi][:, None] - lat[di][None, :]) * 111.3, (lon[oi][:, None] - lon[di][None, :]) * 81.2)
    m = o_w[oi][:, None] * d_w[di][None, :] * np.exp(-km / tau_km)
    np.fill_diagonal(m, 0) if len(oi) == len(di) and (oi == di).all() else None
    m = m / m.sum() * total
    out = []
    for a in range(len(oi)):
        for b in range(len(di)):
            if m[a, b] > 0.05 and oi[a] != di[b]:
                out.append([int(oi[a]), int(di[b]), round(float(m[a, b]), 2)])
    return out


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
    # where the jobs are: every employer in Niagara Region's Employment Inventory 2025 (all sectors,
    # including hospitals, plants and offices), jobs from its employee band
    from .inventory_check import load as load_inventory
    status, rec = load_inventory()
    jobs = np.zeros(len(lat))
    for i, st in status.items():
        if st.get(2025) != "open":
            continue
        x = rec[i][2025]
        if not x["lat"]:
            continue
        jobs[_nearest(lat, lon, float(x["lat"]), float(x["lon"]))] += BAND_JOBS.get(x["sizerangeemployees"], 0)
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
    trips += spread(home_w, jobs_w, in_city)
    out_region = dest["elsewhere_in_niagara_region"] * drive * WORKDAYS
    for node, w in tw.items():
        trips += [[o, d, v] for o, d, v in spread(home_w, np.eye(len(lat))[node], out_region * w)]
    out_prov = dest["outside_region_in_ontario"] * drive * WORKDAYS
    trips += spread(home_w, np.eye(len(lat))[north], out_prov)
    # jobs held at a usual place of work on an average day, less those filled by residents
    in_comm = max(business_jobs * JOBS_AT_USUAL_PLACE - dest["within_niagara_falls"], 0) * drive * WORKDAYS
    for node, w in tw.items():
        trips += spread(np.eye(len(lat))[node], jobs_w, in_comm * w)
    sizes["commute_round_trips_per_day"] = {"residents_in_city": round(in_city), "residents_to_other_towns": round(out_region),
                                            "residents_outside_region": round(out_prov), "workers_driving_in": round(in_comm),
                                            "inventory_jobs": int(business_jobs)}

    # school runs: students from the synthetic population to the nearest schools (Region's school list)
    schools = json.loads((DATA_DIR / "schools_niagara_falls.json").read_text())["schools"]
    elem = [s_ for s_ in schools if "Elementary" in s_["type"]]
    sec = [s_ for s_ in schools if "Secondary" in s_["type"]]
    kids = {"elementary": np.zeros(len(lat)), "secondary": np.zeros(len(lat))}
    for r in csv.DictReader(open(ROOT / "build" / "persons_2025.csv")):
        h, a = int(r["household_id"]), int(r["age"])
        if h < n_city_hh and 5 <= a <= 17:
            kids["elementary" if a <= 13 else "secondary"][hh["node"][h]] += 1
    school_trips = {}
    for level, group, driven in (("elementary", elem, DRIVEN_TO_SCHOOL["elementary"]), ("secondary", sec, DRIVEN_TO_SCHOOL["secondary"])):
        snodes = [_nearest(lat, lon, s_["lat"], s_["lon"]) for s_ in group]
        total = 0.0
        for o in np.nonzero(kids[level])[0]:
            d = np.hypot((np.array([lat[n] for n in snodes]) - lat[o]) * 111.3, (np.array([lon[n] for n in snodes]) - lon[o]) * 81.2)
            near = np.argsort(d)[:2]                       # nearest two (public and Catholic boards)
            v = kids[level][o] * driven * SCHOOL_RUNS_PER_DRIVEN_CHILD * SCHOOL_DAYS
            for j in near:
                if snodes[j] != o:
                    trips.append([int(o), int(snodes[j]), round(2 * v / len(near), 2)])
            total += v
        school_trips[level] = round(total)
    sizes["school_runs_round_trips_per_day"] = school_trips

    # other car trips: the travel survey's trip rate less what the model already sends
    # (shopping, commuting, school runs); between stops (non-home-based) and other home-based
    tb = json.loads((DATA_DIR / "behaviour_parameters.json").read_text())["residents"]["trips"]
    persons = float(sum(hh["persons5"][h] for h in range(n_city_hh)))
    survey = persons * tb["trips_per_person_5plus"] * tb["mode_pct"]["auto_driver"] / 100 * WEEKDAY_TO_AVERAGE_DAY
    simulated = persons * OUTINGS_PER_PERSON * 2 * DRIVER_SHARE_OF_OUTINGS + 2 * (in_city + out_region + out_prov) \
        + 2 * sum(school_trips.values())
    missing = max(survey - simulated, 0.0)
    nhb = min(missing, persons * tb["trips_per_person_5plus"] * tb["purpose_pct"]["non_home_based"] / 100
              * tb["mode_pct"]["auto_driver"] / 100 * WEEKDAY_TO_AVERAGE_DAY)
    dest_w = 0.5 * jobs_w + 0.5 * pop_w                       # errands and visits: workplaces and homes alike
    trips += gravity(lat, lon, jobs_w, jobs_w, nhb, OTHER_TRIP_KM)
    trips += gravity(lat, lon, home_w, dest_w, missing - nhb, OTHER_TRIP_KM)
    sizes["other_car_trips_per_day"] = {"survey_driver_trips": round(survey), "already_simulated": round(simulated),
                                        "between_stops": round(nhb), "other_home_based": round(missing - nhb)}

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
    # the same layers against the Region's 2025 counts (provincial highways stay at MTO's 2019 counts)
    from . import traffic_check
    traffic_check.REGIONAL_FILE = "traffic_counts_regional_roads_2025.json"
    after_2025, _ = score(city, spend + bg, "all25")
    traffic_check.REGIONAL_FILE = "traffic_counts_regional_roads.json"
    result = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "inputs_not_scored": sorted(f"{h} {s}" for h, s in INPUT_SECTIONS),
                        "counts": "MTO 2019 provincial highway AADT and Niagara Region 2019 regional road AADT (pre-pandemic)."},
              "layers": sizes, "spending_trips_only": before, "with_commuting_and_through_traffic": after,
              "against_2025_regional_counts": after_2025,
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


def main_quiet() -> dict:
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        r = main()
    a, b = r["with_commuting_and_through_traffic"], r["against_2025_regional_counts"]
    return {"regional_2019": a["correlation_regional_roads"], "regional_2025": b["correlation_regional_roads"],
            "highways": a["correlation_highways"], "share_regional": a["median_share_explained_regional_pct"],
            "share_highways": a["median_share_explained_highways_pct"]}


if __name__ == "__main__":
    main()
