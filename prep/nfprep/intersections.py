"""Delay at traffic signals and stop signs (OpenStreetMap, data/sources/osm_traffic_controls.json).

Each road edge that ends at a controlled intersection gets extra minutes:
  - traffic signal: SIGNAL_MIN on every approach (average control delay; Highway Capacity Manual
    level of service C is 20-35 s per vehicle);
  - all-way stop: STOP_MIN on every approach;
  - other stop signs: STOP_MIN on the minor approaches only (residential, service, unclassified,
    tertiary roads), since the main road does not stop.
A control is tied to the nearest road node within SNAP_M; signals mapped on each approach of one
intersection fold into the same node.

Not applied in the build: with these delays the fit to counted traffic got worse (regional roads
correlation 0.65 -> 0.42 with 25 s at signals, 0.59 with 8 s; both with residential streets at
35 km/h). Without capacity and signal coordination in the model, delays push trips onto side streets.

Usage: python3 -m nfprep.intersections <city.json> <road_edges.csv>   (edits minutes in place)
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

SIGNAL_MIN = 25 / 60      # SEED: HCM level of service C midpoint
STOP_MIN = 10 / 60        # SEED: HCM two-way / all-way stop, light traffic
SNAP_M = 40
MINOR = {"residential", "service", "unclassified", "tertiary", "living_street", "tertiary_link"}


# Where the controls come from: "osm" (OpenStreetMap), or "city+osm": the City of Niagara Falls Traffic Engineering
# intersections (data/city_open_data/world/intersections.geojson; government data: every City and Regional
# intersection inside the city with its control) plus OpenStreetMap's signals, since the City's file lists only the
# lights it runs itself, not the Region's.
SOURCE = "osm"
CITY_CONTROLS = Path(__file__).resolve().parents[2] / "data" / "city_open_data" / "world" / "intersections.geojson"
CITY_KIND = {"Traffic Light": "signal", "All -way stop": "all", "Stop Sign": "stop"}


def _city_elements():
    out = []
    for f in json.loads(CITY_CONTROLS.read_text())["features"]:
        k = CITY_KIND.get(f["properties"].get("ControlType"))
        if k and f["geometry"]:
            lon_, lat_ = f["geometry"]["coordinates"][:2]
            tags = {"highway": "traffic_signals"} if k == "signal" else {"highway": "stop", **({"stop": "all"} if k == "all" else {})}
            out.append({"lat": lat_, "lon": lon_, "tags": tags})
    return out


def controls(lat: np.ndarray, lon: np.ndarray, junction: np.ndarray, source: str | None = None) -> tuple[set, set, set]:
    """Controls tied to real intersections only (3 or more streets meet): signals mapped on each
    approach a few metres back fold into one intersection, so a crossing is delayed once."""
    source = source or SOURCE
    els = json.loads((DATA_DIR / "sources" / "osm_traffic_controls.json").read_text())["elements"]
    if source == "city+osm":
        els = [e for e in els if e["tags"].get("highway") == "traffic_signals"] + _city_elements()
    sig, allway, stop = set(), set(), set()
    for e in els:
        d = np.hypot((lat - e["lat"]) * 111_320, (lon - e["lon"]) * 111_320 * np.cos(np.radians(e["lat"])))
        d[~junction] = np.inf
        i = int(d.argmin())
        if d[i] > SNAP_M:
            continue
        t = e["tags"]
        if t.get("highway") == "traffic_signals":
            sig.add(i)
        elif t.get("stop") == "all":
            allway.add(i)
        else:
            stop.add(i)
    return sig, allway - sig, stop - sig - allway


def apply(city_path: Path, edges_csv: Path) -> dict:
    city = json.loads(Path(city_path).read_text())
    lat, lon = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    ed0 = city["edges"]
    neighbours = [set() for _ in lat]
    for a, b in zip(ed0["from"], ed0["to"]):
        neighbours[a].add(b); neighbours[b].add(a)
    junction = np.array([len(x) >= 3 for x in neighbours])
    sig, allway, stop = controls(lat, lon, junction)
    rows = list(csv.DictReader(open(edges_csv)))
    ed = city["edges"]
    n = {"signal": 0, "all_way_stop": 0, "minor_stop": 0}
    for i, r in enumerate(rows):
        to = ed["to"][i]
        assert int(r["to_node"]) == to
        if to in sig:
            ed["minutes"][i] = round(ed["minutes"][i] + SIGNAL_MIN, 4); n["signal"] += 1
        elif to in allway:
            ed["minutes"][i] = round(ed["minutes"][i] + STOP_MIN, 4); n["all_way_stop"] += 1
        elif to in stop and r["road_class"] in MINOR:
            ed["minutes"][i] = round(ed["minutes"][i] + STOP_MIN, 4); n["minor_stop"] += 1
    city.setdefault("meta", {})["intersection_delay"] = {"signal_nodes": len(sig), "all_way_stop_nodes": len(allway),
                                                         "stop_nodes": len(stop), "edges_delayed": n}
    Path(city_path).write_text(json.dumps(city, separators=(",", ":")))
    return city["meta"]["intersection_delay"]


if __name__ == "__main__":
    print(apply(Path(sys.argv[1]), Path(sys.argv[2])))
