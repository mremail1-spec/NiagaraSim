"""Bundle what the browser game needs into web/: the city file, the road shapes and the engine.

Usage: python3 -m nfprep.webexport [year] [beta]
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

from .population import BUILD_DIR

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
WASM = ROOT / "engine" / "target" / "wasm32-unknown-unknown" / "release" / "nfsim.wasm"
LON0, LAT0 = -79.20, 43.16          # origin of the integer road coordinates (1e-5 degrees)
CLASS = {"motorway": 2, "motorway_link": 2, "trunk": 2, "trunk_link": 2,
         "primary": 1, "primary_link": 1, "secondary": 1, "secondary_link": 1}


def main(year: int = 2025, beta: float = 0.0) -> None:
    src = BUILD_DIR / f"city_{year}"
    WEB.mkdir(exist_ok=True)
    seen, lines = set(), []
    with open(src / "road_edges.csv", encoding="utf-8") as f:
        for e in csv.DictReader(f):
            key = (min(e["from_node"], e["to_node"]), max(e["from_node"], e["to_node"]), e["length_m"])
            if key in seen:                      # two-way streets are stored once each way
                continue
            seen.add(key)
            flat = [CLASS.get(e["road_class"], 0)]
            for p in e["polyline"].split(";"):
                la, lo = p.split(",")
                flat += [round((float(lo) - LON0) * 1e5), round((LAT0 - float(la)) * 1e5)]
            lines.append(flat)
    (WEB / "roads.json").write_text(json.dumps({"lon0": LON0, "lat0": LAT0, "year": year, "beta": beta, "lines": lines},
                                               separators=(",", ":")))
    shutil.copy(src / "city.json", WEB / "city.json")
    if WASM.exists():
        shutil.copy(WASM, WEB / "nfsim.wasm")
    for name in ("roads.json", "city.json", "nfsim.wasm"):
        print(f"{name:12s} {(WEB / name).stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]) if a else 2025, float(a[1]) if len(a) > 1 else 0.0)
