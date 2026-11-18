"""Model road speeds against TomTom's measured free-flow speeds, street by street.

Samples road segments across the city (more on main roads), asks TomTom's Traffic Flow service
for the segment at each midpoint, and compares TomTom's free-flow speed (typical speed on an
empty road, from measured traffic) with the model's speed on that segment: the base speed
(posted limit or road-class default) and the real speed after the road-type factor fitted to
TomTom trip times (data/road_time_factors.json). Waits at lights and stop signs are not in a
segment's free-flow speed, so they are left out here.

TomTom's replies stay in build/ (their terms limit storing results); docs/ gets the comparison.
Usage: python3 -m nfprep.street_speeds_check
"""
from __future__ import annotations

import csv
import json
import random
import time
import urllib.request
from pathlib import Path

import numpy as np

from .loader import DATA_DIR
from .road_times import group

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "build" / "tomtom_flow_segments.json"
URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/12/json?point={lat},{lon}"
PER_GROUP = {"motorway": 40, "arterial": 120, "collector": 80, "local": 60}
SNAP_M = 20


def flow(lat, lon):
    req = urllib.request.Request(URL.format(lat=round(lat, 6), lon=round(lon, 6)))
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())["flowSegmentData"]
            return {"free": d["freeFlowSpeed"], "conf": d.get("confidence"), "frc": d.get("frc"),
                    "pts": [(c["latitude"], c["longitude"]) for c in d["coordinates"]["coordinate"]]}
        except Exception:
            if attempt == 3:
                return None
            time.sleep(2 ** attempt)


def main(city_dir: Path = ROOT / "build" / "city_2025") -> dict:
    factors = json.loads((DATA_DIR / "road_time_factors.json").read_text())["speed"]
    seen, edges = set(), []
    for e in csv.DictReader(open(city_dir / "road_edges.csv", encoding="utf-8")):
        key = tuple(sorted((e["from_node"], e["to_node"])))
        if key in seen or float(e["length_m"]) < 150:
            continue
        seen.add(key)
        pts = [tuple(map(float, p.split(","))) for p in e["polyline"].split(";")]
        edges.append({"id": e["edge_id"], "name": e["name"], "class": e["road_class"], "group": group(e["road_class"]),
                      "kmh": float(e["speed_kmh"]), "mid": pts[len(pts) // 2], "pts": pts})
    rng = random.Random(1)
    sample = []
    for g, n in PER_GROUP.items():
        pool = [e for e in edges if e["group"] == g]
        sample += rng.sample(pool, min(n, len(pool)))

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    asked = 0
    for e in sample:
        if e["id"] not in cache:
            cache[e["id"]] = flow(*e["mid"])
            asked += 1
            time.sleep(0.25)
    CACHE.write_text(json.dumps(cache))

    def xy(p, la0):
        return np.array([p[1] * 111_320 * np.cos(np.radians(la0)), p[0] * 111_320])

    def nearest(p, line):
        """Distance (m) from p to a polyline and the direction (radians, 0..pi) of the closest piece."""
        la0 = p[0]
        P = xy(p, la0)
        best, ang = np.inf, 0.0
        for a, b in zip(line[:-1], line[1:]):
            A, B = xy(a, la0), xy(b, la0)
            AB = B - A
            t = 0.0 if not AB.any() else float(np.clip(np.dot(P - A, AB) / np.dot(AB, AB), 0, 1))
            d = float(np.linalg.norm(A + t * AB - P))
            if d < best:
                best, ang = d, float(np.arctan2(AB[1], AB[0]) % np.pi)
        return best, ang

    rows = []
    for e in sample:
        r = cache.get(e["id"])
        if not r or not r["pts"] or not r["free"]:
            continue
        # TomTom's segment must be the model's street: within SNAP_M of its midpoint, same direction
        d, ang_t = nearest(e["mid"], r["pts"])
        k = len(e["pts"]) // 2
        a, b = e["pts"][max(k - 1, 0)], e["pts"][min(k + 1, len(e["pts"]) - 1)]
        v = xy(b, a[0]) - xy(a, a[0])
        ang_m = float(np.arctan2(v[1], v[0]) % np.pi)
        diff = abs(ang_m - ang_t)
        diff = min(diff, np.pi - diff)
        if d > SNAP_M or diff > np.radians(30):
            continue
        real = e["kmh"] / factors.get(e["group"], 1.0)
        rows.append({"group": e["group"], "class": e["class"], "name": e["name"], "model_base_kmh": e["kmh"],
                     "model_real_kmh": round(real, 1), "ratio_base": e["kmh"] / r["free"], "ratio_real": real / r["free"],
                     "free": r["free"]})
    summary = {}
    for g in PER_GROUP:
        x = [r for r in rows if r["group"] == g]
        if not x:
            continue
        summary[g] = {"segments": len(x),
                      "median_model_base_over_tomtom": round(float(np.median([r["ratio_base"] for r in x])), 3),
                      "median_model_real_over_tomtom": round(float(np.median([r["ratio_real"] for r in x])), 3),
                      "share_within_15pct_real": round(float(np.mean([abs(np.log(r["ratio_real"])) < np.log(1.15) for r in x])), 2),
                      "correlation_real": round(float(np.corrcoef([r["model_real_kmh"] for r in x], [r["free"] for r in x])[0, 1]), 3)
                      if len(x) > 2 else None}
    # where the gap is: by the model's base speed (posted limit or class default)
    for g in PER_GROUP:
        for lo, hi in [(0, 45), (45, 55), (55, 65), (65, 200)]:
            y = [r for r in rows if r["group"] == g and lo <= r["model_base_kmh"] < hi]
            if y:
                summary.setdefault(g, {}).setdefault("by_model_base_kmh", {})[f"{lo}-{hi}"] = {
                    "segments": len(y), "median_model_base_over_tomtom": round(float(np.median([r["ratio_base"] for r in y])), 2)}
    # worst streets by name (the model's speeds, not TomTom's, are listed)
    by_name = {}
    for r in rows:
        if r["name"]:
            by_name.setdefault(r["name"], []).append(r["ratio_real"])
    worst = sorted(((n, float(np.median(v)), len(v)) for n, v in by_name.items() if len(v) >= 2), key=lambda t: abs(np.log(t[1])), reverse=True)[:12]
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "requests_this_run": asked, "matched_segments": len(rows),
                     "note": "Ratios only; TomTom speeds are not stored here."},
           "by_road_type": summary,
           "streets_furthest_off": [{"street": n, "model_over_tomtom": round(v, 2), "segments": k} for n, v, k in worst]}
    (ROOT / "docs" / "street_speeds_check.json").write_text(json.dumps(doc, indent=1))
    print(json.dumps(doc, indent=1))
    return doc


if __name__ == "__main__":
    main()
