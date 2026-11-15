"""Build the city for one year: population, businesses and roads, into build/city_<year>/.

Usage: python3 -m nfprep.build [year] [seed]
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import numpy as np

from .budgets import CATEGORY_NAMES, assign_budgets, export_city
from .businesses import build_businesses
from .loader import DATA_DIR, load_pack
from .population import BUILD_DIR, fingerprint, generate, summarise, write_csv
from .roads import build_roads, snap, strongly_reachable


def load_full_pack():
    pack = load_pack()
    earn = json.loads((DATA_DIR / "avg_weekly_earnings_sectors_ontario_2025.json").read_text())
    pack.params["avg_weekly_earnings_sectors"] = {k: v["avg_weekly_earnings"] for k, v in earn["sectors"].items()}
    return pack


def build_city(year: int = 2025, seed: int = 1, pack=None) -> dict:
    pack = pack or load_full_pack()
    households, persons = generate(pack, year=year, seed=seed)
    businesses = build_businesses(pack, seed=seed)
    nodes, edges = build_roads(pack)

    for rows in (households, businesses):
        idx, dist = snap(nodes, np.array([r["lat"] for r in rows]), np.array([r["lon"] for r in rows]))
        for r, i, d in zip(rows, idx, dist):
            r["road_node"] = int(i)
            r["road_snap_m"] = round(float(d), 1)

    budgets = assign_budgets(pack, households, year)
    out, back = strongly_reachable(nodes, edges)
    report = summarise(households, persons)
    report.update({
        "year": year, "seed": seed, "fingerprint": fingerprint(households, persons),
        "businesses": len(businesses),
        "businesses_by_source": dict(Counter(b["source"].split(":")[0] for b in businesses)),
        "employers_by_naics": dict(sorted(Counter(b["naics"] for b in businesses if b["employer"]).items())),
        "road_nodes": len(nodes), "road_edges": len(edges),
        "road_km": round(sum(e["length_m"] for e in edges) / 1000, 1),
        "road_nodes_drivable_from_node_0": out, "road_nodes_that_can_drive_to_node_0": back,
        "home_snap_m_median": float(np.median([h["road_snap_m"] for h in households])),
        "home_snap_m_p99": float(np.percentile([h["road_snap_m"] for h in households], 99)),
        "business_snap_m_max": max(b["road_snap_m"] for b in businesses),
    })
    report["resident_spending_by_category_musd"] = {c: round(float(budgets[:, i].sum()) / 1e6, 1)
                                                    for i, c in enumerate(CATEGORY_NAMES)}
    report["resident_spending_total_musd"] = round(float(budgets.sum()) / 1e6, 1)
    report["households_by_ontario_quintile"] = dict(sorted(Counter(h["income_quintile_ontario"] for h in households).items()))
    return {"households": households, "persons": persons, "businesses": businesses,
            "road_nodes": nodes, "road_edges": edges, "report": report, "budgets": budgets, "pack": pack}


def main(year: int = 2025, seed: int = 1) -> None:
    city = build_city(year, seed)
    out = BUILD_DIR / f"city_{year}"
    for name in ("households", "persons", "businesses", "road_nodes", "road_edges"):
        write_csv(out / f"{name}.csv", city[name])
    export_city(city["pack"], city, city["budgets"], year, out / "city.json")
    (out / "report.json").write_text(json.dumps(city["report"], indent=1))
    for k, v in city["report"].items():
        print(f"{k:40s} {v}")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
