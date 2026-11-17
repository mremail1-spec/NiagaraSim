"""Foot traffic against the City's own choice of where to sweep sidewalks.

The City of Niagara Falls sweeps the sidewalks where people walk most (open data: Niagara Falls
Maintenance Sidewalk Sweeper Route, 8 routes, 56 km). Road nodes within 40 m of a route are
"swept"; the check is whether the model puts more people on foot there (ranking score, and how
many of the model's 100 busiest walking nodes are on a route). Writes docs/foot_traffic_check.json.

Usage: python3 -m nfprep.foot_check
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def main() -> dict:
    routes = json.loads((ROOT / "data/city_open_data/Niagara_Falls_Maintenance_Sidewalk_Sweeper_Route_WGS_1984.geojson").read_text())
    c = json.loads((ROOT / "build/city_2025/city.json").read_text())
    lat, lon = np.array(c["nodes"]["lat"]), np.array(c["nodes"]["lon"])
    walk = np.array(c["nodes"]["walkers"], float)
    on = np.zeros(len(lat), bool)
    for f in routes["features"]:
        g = f["geometry"]
        for line in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
            for lo, la in line:
                on |= np.hypot((lat - la) * 111_320, (lon - lo) * 111_320 * np.cos(np.radians(la))) < 40
    a, b = walk[on], walk[~on]
    auc = float((np.sum(a[:, None] > b[None, :]) + 0.5 * np.sum(a[:, None] == b[None, :])) / (len(a) * len(b)))
    top = np.argsort(-walk)[:100]
    out = {"_meta": {"what": __doc__.strip().split("\n\n")[0]}, "nodes_on_routes": int(on.sum()), "nodes_elsewhere": int((~on).sum()),
           "median_walkers_on_routes": float(np.median(a)), "median_walkers_elsewhere": float(np.median(b)),
           "ranking_score": round(auc, 3), "top_100_walking_nodes_on_routes": int(on[top].sum()),
           "share_of_nodes_on_routes": round(float(on.mean()), 3)}
    (ROOT / "docs" / "foot_traffic_check.json").write_text(json.dumps(out, indent=1))
    print(out)
    return out


if __name__ == "__main__":
    main()
