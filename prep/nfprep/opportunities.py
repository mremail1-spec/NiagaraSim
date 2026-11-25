"""Where a new business could work in Niagara Falls today, if anywhere.

For every kind of business a person could open (the game's build menu: restaurant, cafe, pharmacy,
souvenir shop...) at every spot where businesses already trade or space is listed for lease, the
engine forecasts a typical-size newcomer's first-year sales, taking customers from the businesses
around it. Businesses known to be closed are left out (a newcomer opens into today's city).

The yardstick is the competition: what the businesses of that kind already trading in the city earn
in the same scenario. An opportunity is a spot where a newcomer would clearly out-sell the median
one, in every scenario that fits the evidence (nfprep.ensemble), and where the industry's normal profit
margin survives the rent on that street (rent_surface.py).

Usage: python3 -m nfprep.opportunities [scenarios]   (default 5 scenarios plus the default)
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
CITY = ROOT / "build" / "city_2025" / "city.json"
NFSIM = str(ROOT / "engine/target/release/nfsim")
NOW = "209912"          # every known closure
BEAT = 1.3              # a newcomer must out-sell its yardstick by this much, in every scenario
# formats tested: a small one (the size of the smaller quarter of existing businesses of the kind) and a
# typical one (the median); each is judged against existing businesses of the same format and area
FORMATS = [("small", 25), ("typical", 50)]


def sites(city: dict) -> list[dict]:
    """Spots to test: space listed for lease (real vacancies) and every spot where businesses trade."""
    b = city["businesses"]
    pts = {r["address"].upper(): r for r in csv.DictReader(open(DATA_DIR / "address_points.csv"))}
    from .rent_surface import _key
    out, seen = [], set()
    lease = json.loads((DATA_DIR / "real_estate/niagara_falls_commercial_lease_2026-11.json").read_text())["listings"]
    for r in lease:
        if "industrial" in (r.get("use") or ""):
            continue
        k = _key(r["address"])
        p = pts.get(k) if k else None
        if p and k not in seen:
            seen.add(k)
            out.append({"lat": float(p["lat"]), "lon": float(p["lon"]), "where": r["address"], "for_lease": True})
    nodes = defaultdict(list)
    for i in range(len(b["node"])):
        if not b["external"][i] and not b["closed_from"][i]:
            nodes[b["node"][i]].append(i)
    # one spot per block of about 150 m where businesses trade (the spot with the most businesses)
    cells = {}
    for n, ids in nodes.items():
        i = ids[0]
        cell = (round(b["lat"][i] / 0.00135), round(b["lon"][i] / 0.00185))
        if cell not in cells or len(ids) > len(nodes[cells[cell]]):
            cells[cell] = n
    for n in cells.values():
        i = nodes[n][0]
        a = b["address"][i] or b["name"][i]
        out.append({"lat": b["lat"][i], "lon": b["lon"][i], "where": a, "for_lease": False, "node": n})
    return out


def forecast(city_path: Path, beta: float, site_list: list[dict], menu: list[dict]) -> np.ndarray:
    """[site, kind] -> (resident, tourist) yearly sales for a newcomer the size of the median existing
    business of its kind (so the comparison is about the spot, not about being bigger or smaller)."""
    rows = [[s["lat"], s["lon"], m["sim_category"], m["_size"], m["_fw"]] for s in site_list for m in menu]
    with tempfile.TemporaryDirectory() as d:
        sp, op = Path(d) / "sites.json", Path(d) / "out.json"
        sp.write_text(json.dumps(rows))
        subprocess.run([NFSIM, "forecast", str(city_path), "--beta", str(beta), "--closures-as-of", NOW,
                        "--sites", str(sp), "--out", str(op)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return np.array(json.loads(op.read_text()), float).reshape(len(site_list), len(menu), 2)


def main(n_scen: int = 5) -> dict:
    from .ensemble import apply as apply_knobs, BASE, RUNS, OUT
    from .rent_surface import RentSurface
    city = json.loads(CITY.read_text())
    menu = city["build_menu"]
    bz = city["businesses"]
    kinds = []
    for m in menu:
        same = [i for i in range(len(bz["node"])) if bz["sim_category"][i] == m["sim_category"]
                and not bz["external"][i] and not bz["closed_from"][i] and bz["revenue_prior"][i] > 0
                and bz["format_weight"][i] >= 0.05]          # not EV chargers, lockers and the like
        sz = [bz["revenue_prior"][i] for i in same]
        for fmt, pct in FORMATS:
            size = float(np.percentile(sz, pct)) if sz else m["sizes"]["typical"]
            # the newcomer pulls like existing businesses of its kind and about its size (a small grocery is a
            # convenience store, not a supermarket); checked in docs/forecast_consistency.json
            lo, hi = (np.percentile(sz, max(pct - 15, 0)), np.percentile(sz, min(pct + 15, 100))) if sz else (0, 0)
            band = [bz["format_weight"][i] for i in same if lo <= bz["revenue_prior"][i] <= hi]
            fw = float(np.median(band)) if band else 1.0
            kinds.append(dict(m, _size=size, _fw=fw, _format=fmt, _pct=pct))
    menu = kinds
    site_list = sites(city)
    cal = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())
    scen = [("default", CITY, cal["beta"])]
    runs = [json.loads(l) for l in open(RUNS)]
    tp = OUT / "traffic.jsonl"
    tested = [json.loads(l)["run"] for l in open(tp)] if tp.exists() else []
    pick = [runs[i] for i in tested if runs[i]["max_implausibility"] <= 1.0]
    pick.sort(key=lambda r: r["tourist_core_share"])
    pick = [pick[round(q * (len(pick) - 1))] for q in np.linspace(0, 1, n_scen)] if pick else []
    base = json.loads(BASE.read_text()) if BASE.exists() else city
    tmpdir = Path(tempfile.mkdtemp(dir=ROOT / "build"))
    for j, r in enumerate(pick):
        from .default_scenario import fit_brands
        p = tmpdir / f"city_{j}.json"
        beta = cal.get("beta_fit", cal["beta"]) * r["knobs"]["beta_mult"]
        c = apply_knobs(base, r["knobs"])
        fit_brands(c, beta, rounds=6)           # every scenario matches the chains' published sales too
        p.write_text(json.dumps(c, separators=(",", ":")))
        scen.append((f"scenario {j + 1}", p, beta))
    rs = RentSurface()
    rent_f = np.array([min(3.0, max(0.3, rs.at(s["lat"], s["lon"]) / rs.median)) for s in site_list])
    from multiprocessing import Pool
    with Pool(min(4, len(scen))) as pool:
        res = pool.starmap(forecast, [(p, beta, site_list, menu) for _, p, beta in scen])
    sales = np.stack([r.sum(axis=2) for r in res])   # [scen, site, kind]
    # the yardstick: what the businesses of that kind already in the city earn, in the same scenario
    b = city["businesses"]
    med = np.zeros((len(scen), len(menu)))
    med_core = np.zeros((len(scen), len(menu)))
    med_res = np.zeros((len(scen), len(menu)))
    counts = np.zeros(len(menu), int)
    # is each spot in the Tourist Core? (the nearest business's flag)
    blat, blon = np.array(b["lat"]), np.array(b["lon"])
    site_core = np.array([bool(b["core"][int(np.argmin((blat - s_["lat"]) ** 2 + ((blon - s_["lon"]) * 0.73) ** 2))]) for s_ in site_list])
    for si, (_, p, beta) in enumerate(scen):
        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "biz.csv"
            subprocess.run([NFSIM, "run", str(p), "--mode", "flow", "--days", "365", "--beta", str(beta), "--closures-as-of", NOW,
                            "--businesses", str(bp)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rev = {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(bp))}
        for k, m in enumerate(menu):
            ids = [i for i in range(len(b["node"])) if b["sim_category"][i] == m["sim_category"] and not b["external"][i] and not b["closed_from"][i]
                   and b["format_weight"][i] >= 0.05]          # the same businesses the newcomer was sized on
            counts[k] = len(ids)
            pct = m["_pct"]
            v = [rev.get(i, 0.0) for i in ids]
            med[si, k] = float(np.percentile(v, pct)) if ids else np.nan
            vc = [rev.get(i, 0.0) for i in ids if b["core"][i]]
            vr = [rev.get(i, 0.0) for i in ids if not b["core"][i]]
            # an area with fewer than 5 of the kind falls back to the whole city
            med_core[si, k] = float(np.percentile(vc, pct)) if len(vc) >= 5 else med[si, k]
            med_res[si, k] = float(np.percentile(vr, pct)) if len(vr) >= 5 else med[si, k]
    rel = sales / np.where(site_core[None, :, None], med_core[:, None, :], med_res[:, None, :])   # newcomer / same format, same area
    # profit margin a business of that kind keeps on this street: the industry's normal margin, less extra rent
    margin = np.array([[m["net_profit_pct"] - m.get("rent_pct", 0) * (rent_f[i] - 1) for m in menu] for i in range(len(site_list))])
    robust = (rel.min(axis=0) >= BEAT) & (margin > 0)
    found = []
    for i, k in zip(*np.where(robust)):
        found.append({"kind": menu[k]["label"], "format": menu[k]["_format"], "size": round(menu[k]["_size"]), "pull": round(menu[k]["_fw"], 2),
                      "area": "Tourist Core" if site_core[i] else "residential city", "where": site_list[i]["where"], "for_lease": site_list[i]["for_lease"],
                      "lat": round(site_list[i]["lat"], 5), "lon": round(site_list[i]["lon"], 5),
                      "vs_median_competitor_worst": round(float(rel[:, i, k].min()), 2), "vs_median_competitor_default": round(float(rel[0, i, k]), 2),
                      "forecast_sales_default": round(float(sales[0, i, k])),
                      "yardstick_default": round(float(med_core[0, k] if site_core[i] else med_res[0, k])),
                      "margin_after_street_rent_pct": round(float(margin[i, k]), 1), "rent_psf": round(rs.at(site_list[i]["lat"], site_list[i]["lon"]), 1)})
    found.sort(key=lambda x: -x["vs_median_competitor_worst"])
    # the nearest school to each spot (a spot within a student's walk can count on the lunch crowd)
    from .daytime import places as daytime_places, dist
    schools = [p_ for p_ in daytime_places() if p_["kind"] != "workplace"]
    for f in found:
        s_ = min(schools, key=lambda q: dist(f["lat"], f["lon"], q["lat"], q["lon"]))
        f["nearest_school"] = s_["name"]
        f["nearest_school_m"] = round(dist(f["lat"], f["lon"], s_["lat"], s_["lon"]))
    by_kind = {}
    for k, m in enumerate(menu):
        worst = rel.min(axis=0)[:, k]
        best = int(np.nanargmax(worst))
        by_kind[m["label"] + " (" + m["_format"] + ")"] = {"businesses_now": int(counts[k]), "median_sales_now_default": round(float(med[0, k])),
                               "spots_beating_median_in_every_scenario": int(robust[:, k].sum()), "spots_tested": len(site_list),
                               "best_spot": site_list[best]["where"], "best_spot_vs_median_worst_scenario": round(float(worst[best]), 2),
                               "best_spot_forecast_default": round(float(sales[0, best, k]))}
    # next to each school: the best spot within a student's walk for every kind, passing or not
    near_schools = {}
    worst = rel.min(axis=0)
    for q in schools:
        d_ = np.array([dist(s_["lat"], s_["lon"], q["lat"], q["lon"]) for s_ in site_list])
        idx = np.where(d_ <= 800)[0]
        if not len(idx):
            continue
        rows = []
        for k, m in enumerate(menu):
            i = int(idx[np.nanargmax(worst[idx, k])])
            rows.append({"kind": m["label"], "format": m["_format"], "where": site_list[i]["where"], "metres": round(float(d_[i])),
                         "vs_yardstick_worst": round(float(worst[i, k]), 2), "vs_yardstick_default": round(float(rel[0, i, k]), 2),
                         "forecast_sales_default": round(float(sales[0, i, k])), "passes": bool(robust[i, k])})
        rows.sort(key=lambda r: -r["vs_yardstick_worst"])
        near_schools[q["name"]] = {"kind": q["kind"], "students": q["people"], "spots_within_800m": int(len(idx)), "best_by_kind": rows}
    doc = {"near_schools": near_schools, "_meta": {"what": __doc__.strip().split("\n\n")[0], "scenarios": [s[0] for s in scen], "sites_tested": len(site_list),
                     "for_lease_sites": sum(s["for_lease"] for s in site_list), "kinds": len(menu),
                     "rule": f"a newcomer, small (25th percentile) or typical (median) for its kind, sells at least {BEAT}x what existing businesses of the same kind, format and area (Tourist Core or the residential city) sell, in every scenario, and keeps a profit margin after rent on that street",
                     "caution": "A single site's sales forecast can be off by up to about x3.6 either way (data/forecast_error.json); comparisons between spots and kinds are more reliable than the dollar figures."},
           "by_kind": by_kind, "opportunities": found, "opportunities_total": len(found)}
    (ROOT / "docs" / "opportunities.json").write_text(json.dumps(doc, indent=1))
    for p in tmpdir.glob("*.json"):
        p.unlink()
    tmpdir.rmdir()
    print(json.dumps({k: v for k, v in doc.items() if k != "opportunities"}, indent=1)[:4000])
    print("opportunities:", len(found))
    for f in found[:25]:
        print(f)
    return doc


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
