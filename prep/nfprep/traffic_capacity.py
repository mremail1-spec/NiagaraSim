"""Congestion: busy roads slow down as they fill, and traffic spreads to other routes.

Each road edge gets a daily capacity from its lanes (Niagara Region's road file where a street has
one lane count over most of its length, otherwise by road class) times a daily capacity per lane, and
a travel time from the Bureau of Public Roads curve t = t0 (1 + 0.15 (V/C)^4), V the simulated
vehicles a day on it (all layers). Edge times are averaged over iterations (method of successive
averages); the spending trips and the other layers are re-routed each time.

Usage: python3 -m nfprep.traffic_capacity [iterations]
"""
from __future__ import annotations

import collections
import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
CITY = ROOT / "build" / "city_2025"
# SEED: two-way lanes by class where the Region's file has no count, and vehicles a day per lane
LANES = {"motorway": 4, "trunk": 4, "primary": 4, "secondary": 2, "tertiary": 2}
PER_LANE_DAY = {"motorway": 18000, "motorway_link": 12000, "trunk": 12000, "trunk_link": 9000, "primary": 9000,
                "secondary": 8000, "tertiary": 7000}
LOCAL_PER_LANE_DAY = 5000


def region_lanes() -> dict:
    f = DATA_DIR / "sources" / "region" / "niagara_region_roads_opendata.csv"
    by = collections.defaultdict(collections.Counter)
    for x in csv.DictReader(open(f, encoding="utf-8-sig")):
        if "Niagara Falls" in (x["Left Municipality"], x["Right Municipality"]) and x["Lanes"] not in ("", "0"):
            by[x["Full Street Name"].strip().lower()][int(x["Lanes"])] += float(x["Shape__Length"] or 0)
    out = {}
    for n, c in by.items():
        lanes, length = c.most_common(1)[0]
        if length >= 0.8 * sum(c.values()):
            out[n] = lanes
    return out


def capacities(rows) -> np.ndarray:
    rl = region_lanes()
    cap = []
    for r in rows:
        cls = r["road_class"]
        lanes = rl.get(r["name"].lower()) or LANES.get(cls.replace("_link", ""), 2)
        cap.append(lanes * PER_LANE_DAY.get(cls, LOCAL_PER_LANE_DAY))
    return np.array(cap, float)


def run(iterations: int = 3) -> dict:
    from . import traffic_layers
    city_path = CITY / "city.json"
    rows = list(csv.DictReader(open(CITY / "road_edges.csv")))
    city = json.loads(city_path.read_text())
    n_edges = len(rows)
    t0 = np.array(city["edges"]["minutes"][:n_edges], float)
    base_path = CITY / "city_free_flow_minutes.json"
    if base_path.exists():
        t0 = np.array(json.loads(base_path.read_text()), float)
    else:
        base_path.write_text(json.dumps(t0.tolist()))
    cap = capacities(rows)
    fr = np.array([int(r["from_node"]) for r in rows]); to = np.array([int(r["to_node"]) for r in rows])
    t = t0.copy()
    history = []
    for k in range(1, iterations + 1):
        c = json.loads(city_path.read_text())
        c["edges"]["minutes"][:n_edges] = [round(float(v), 4) for v in t]
        city_path.write_text(json.dumps(c, separators=(",", ":")))
        subprocess.run(["python3", "-m", "nfprep.passby", str(city_path)], cwd=ROOT / "prep", check=True, stdout=subprocess.DEVNULL)
        res = traffic_layers.main_quiet()
        vol = np.array(json.loads(city_path.read_text())["nodes"]["passing"], float)
        v = (vol[fr] + vol[to]) / 2
        bpr = t0 * (1 + 0.15 * (v / cap) ** 4)
        t = t + (bpr - t) / (k + 1)                     # successive averages
        over = float((v > cap).mean())
        history.append({"iteration": k, **res, "edges_over_capacity_pct": round(100 * over, 2),
                        "max_slowdown": round(float((bpr / t0).max()), 2)})
        print(history[-1], flush=True)
    return {"history": history}


if __name__ == "__main__":
    out = run(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
    (ROOT / "docs" / "traffic_capacity_check.json").write_text(json.dumps(out, indent=1))
