"""Compare simulated road volumes with Ontario's counted volumes on the provincial highways.

Usage: python3 -m nfprep.traffic_check <city.json> <node_volumes.json>
The simulation only generates shopping, dining and visitor trips. Commuting, school runs,
trucks and traffic passing through are not simulated, so it should explain part of each count.
"""
from __future__ import annotations

import json
import sys

import numpy as np

from .loader import DATA_DIR


def main(city_path: str, volumes_path: str) -> list[dict]:
    city = json.loads(open(city_path).read())
    by_source = np.array(json.loads(open(volumes_path).read())).reshape(-1, 4)
    vol = by_source.sum(axis=1)
    lat, lon = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    counts = json.loads((DATA_DIR / "traffic_counts_provincial_highways.json").read_text())
    regional = json.loads((DATA_DIR / "traffic_counts_regional_roads.json").read_text())
    for s in regional["sections"]:
        counts["sections"].append({"highway": "RR" + s["regional_road"].lstrip("0"), "section_id": int(s["station"]),
                                   "aadt_2019": s["aadt"], "truck_aadt_2019": None, "polyline": s["polyline"],
                                   "where": s["from"] + " to " + s["to"], "two_way_on_one_line": True})
    rows = []
    for s in counts["sections"]:
        near = set()
        for la, lo in s["polyline"]:
            d = np.hypot((lat - la) * 111_320, (lon - lo) * 111_320 * np.cos(np.radians(la)))
            if d.min() < 60:
                near.add(int(d.argmin()))
        if len(near) < 3:
            continue                                    # section lies outside the simulated road network
        # Each direction of a divided highway is its own line of nodes, and a round trip is counted
        # twice on its outbound route, so the two-way volume is twice the average over both lines.
        # a two-way city street is one line of nodes carrying both directions
        model = (1 if s.get("two_way_on_one_line") else 2) * float(np.mean(vol[list(near)]))
        mix = by_source[list(near)].sum(axis=0)
        mix = [round(100 * float(x) / max(float(mix.sum()), 1)) for x in mix]
        rows.append({"highway": s["highway"], "section": s["section_id"], "counted_aadt_2019": s["aadt_2019"],
                     "counted_trucks_2019": s["truck_aadt_2019"], "where": s.get("where", ""),
                     "simulated_per_day": round(model), "share_explained_pct": round(100 * model / s["aadt_2019"], 1),
                     "nodes_matched": len(near),
                     "simulated_mix_pct": {"city_residents": mix[0], "town_shoppers": mix[1], "visitors_getting_around": mix[2], "visitors_arriving": mix[3]}})
    print(f"{'highway':8s}{'section':>9s}{'counted':>10s}{'simulated':>11s}{'explained':>11s}")
    for r in rows:
        m = r["simulated_mix_pct"]
        if r["highway"].startswith("RR"):
            continue
        print(f"{r['highway']:8s}{r['section']:>9d}{r['counted_aadt_2019']:>10,d}{r['simulated_per_day']:>11,d}{r['share_explained_pct']:>10.0f}%"
              f"   residents {m['city_residents']}% towns {m['town_shoppers']}% visitors around {m['visitors_getting_around']}% arriving {m['visitors_arriving']}%")
    rr = [r for r in rows if r["highway"].startswith("RR")]
    if rr:
        share = np.array([r["share_explained_pct"] for r in rr])
        c = np.array([r["counted_aadt_2019"] for r in rr], float)
        m = np.array([r["simulated_per_day"] for r in rr], float)
        print(f"\nregional roads: {len(rr)} sections; simulated share of counted traffic median {np.median(share):.0f}%, "
              f"middle half {np.percentile(share, 25):.0f}-{np.percentile(share, 75):.0f}%; "
              f"correlation of simulated with counted volumes {np.corrcoef(c, m)[0, 1]:.2f}")
        print("highest and lowest shares explained:")
        for r in sorted(rr, key=lambda r: -r["share_explained_pct"])[:4] + sorted(rr, key=lambda r: r["share_explained_pct"])[:4]:
            print(f"  {r['highway']:6s} {r['where'][:52]:52s} counted {r['counted_aadt_2019']:>6,d}  simulated {r['simulated_per_day']:>6,d}  {r['share_explained_pct']:>4.0f}%")
    return rows


if __name__ == "__main__":
    out = main(sys.argv[1], sys.argv[2])
    if len(sys.argv) > 3:
        open(sys.argv[3], "w").write(json.dumps(out, indent=1))
