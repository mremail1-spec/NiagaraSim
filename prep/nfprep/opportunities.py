"""Where a new business could work in Niagara Falls today, if anywhere.

For every kind of business a person could open (the game's build menu: restaurant, cafe, pharmacy,
souvenir shop...) at every spot where businesses already trade or space is listed for lease, the
engine forecasts a typical-size newcomer's first-year sales, taking customers from the businesses
around it. Businesses known to be closed are left out (a newcomer opens into today's city).

Sales become profit with the industry's cost structure (Statistics Canada benchmarks, as in the game)
and rent on that street (rent_surface.py). An opportunity must hold in every scenario that fits the
evidence (nfprep.ensemble), not just the default: sales at least those of a typical business of its
kind and a profit in all of them.

Also reported: what residents spend in each neighbouring town (money that leaves the city).

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
    for n, ids in nodes.items():
        i = ids[0]
        a = b["address"][i] or b["name"][i]
        out.append({"lat": b["lat"][i], "lon": b["lon"][i], "where": a, "for_lease": False, "node": n})
    return out


def forecast(city_path: Path, beta: float, site_list: list[dict], menu: list[dict]) -> np.ndarray:
    """[site, kind] -> (resident, tourist) yearly sales for a typical-size newcomer."""
    rows = [[s["lat"], s["lon"], m["sim_category"], m["sizes"]["typical"]] for s in site_list for m in menu]
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
    sales = np.stack([forecast(p, beta, site_list, menu).sum(axis=2) for _, p, beta in scen])   # [scen, site, kind]
    margin = np.array([1 - (m["cost_of_sales_pct"] + m["wages_pct"]) / 100 for m in menu])
    size = np.array([m["sizes"]["typical"] for m in menu])
    base_fixed = np.array([(100 - m["cost_of_sales_pct"] - m["wages_pct"] - m["net_profit_pct"]) / 100 for m in menu])
    rent_pct = np.array([m.get("rent_pct", 0) / 100 for m in menu])
    fixed = (base_fixed[None, :] + rent_pct[None, :] * (rent_f[:, None] - 1)) * size[None, :]      # [site, kind]
    prof = sales * margin[None, None, :] - fixed[None, :, :]
    typical = np.array([m["sizes"]["typical"] for m in menu])
    ratio = sales / typical[None, None, :]
    robust = (ratio.min(axis=0) >= 1.0) & (prof.min(axis=0) > 0)              # holds in every scenario
    found = []
    for i, k in zip(*np.where(robust)):
        found.append({"kind": menu[k]["label"], "where": site_list[i]["where"], "for_lease": site_list[i]["for_lease"],
                      "lat": round(site_list[i]["lat"], 5), "lon": round(site_list[i]["lon"], 5),
                      "sales_vs_typical_min": round(float(ratio[:, i, k].min()), 2), "sales_vs_typical_default": round(float(ratio[0, i, k]), 2),
                      "profit_min": round(float(prof[:, i, k].min())), "profit_default": round(float(prof[0, i, k])),
                      "rent_psf": round(rs.at(site_list[i]["lat"], site_list[i]["lon"]), 1)})
    found.sort(key=lambda x: -x["profit_min"])
    # per kind: how many spots work, and the best one
    by_kind = {}
    for k, m in enumerate(menu):
        ok = robust[:, k]
        best = int(np.argmax(prof.min(axis=0)[:, k]))
        by_kind[m["label"]] = {"spots_that_work_in_every_scenario": int(ok.sum()), "spots_tested": len(site_list),
                               "best_spot": site_list[best]["where"], "best_profit_worst_scenario": round(float(prof.min(axis=0)[best, k])),
                               "median_sales_vs_typical": round(float(np.median(ratio[0, :, k])), 2)}
    # money residents spend in the neighbouring towns, by kind (default scenario)
    with tempfile.TemporaryDirectory() as d:
        bp = Path(d) / "biz.csv"
        subprocess.run([NFSIM, "run", str(CITY), "--mode", "flow", "--days", "365", "--beta", str(cal["beta"]), "--closures-as-of", NOW,
                        "--businesses", str(bp)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        b = city["businesses"]
        leak = defaultdict(float)
        for row in csv.DictReader(open(bp)):
            i = int(row["business"])
            if i < len(b["external"]) and b["external"][i]:
                leak[b["name"][i]] += float(row["revenue"])
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "scenarios": [s[0] for s in scen], "sites_tested": len(site_list),
                     "for_lease_sites": sum(s["for_lease"] for s in site_list), "kinds": len(menu),
                     "rule": "sales at least a typical business of its kind, and a profit after rent on that street, in every scenario"},
           "by_kind": by_kind, "opportunities": found[:200], "opportunities_total": len(found),
           "residents_spending_in_neighbouring_towns_musd": {k: round(v / 1e6, 1) for k, v in sorted(leak.items(), key=lambda z: -z[1])}}
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
