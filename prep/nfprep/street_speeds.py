"""Measured street speeds from TomTom's Traffic Flow service.

For every piece of the model's road network (highways and main roads first, then local streets), TomTom's free-flow
speed (typical speed on an empty road, from measured traffic) at that place. One TomTom reply
covers a whole TomTom segment, so pieces already covered by an earlier reply are skipped.
Saved as points (lat, lon, direction, km/h) in data/street_speeds_tomtom.json so they survive
rebuilds and network changes; the build gives each road piece the speed of the matching point
(within 20 m, same direction). Pieces TomTom has no data for keep the posted or class speed.
TomTom's free allowance is 2,500 requests a day: usage is counted per day in the data file and a run
stops at DAILY_CAP; a daily scheduled task resumes it until every piece is covered.

Stored with the owner's agreement (repository kept private).
Usage: python3 -m nfprep.street_speeds [max_requests]   (resumes where it stopped)
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

from .loader import DATA_DIR
from .road_times import group

ROOT = Path(__file__).resolve().parents[2]
OUT = DATA_DIR / "street_speeds_tomtom.json"
URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/18/json?point={lat},{lon}"
SNAP_M = 20
DAILY_CAP = 2300                     # under TomTom's 2,500 free requests a day, leaving room for checks
MAX_ANGLE = np.radians(30)


def _xy(p, la0):
    return np.array([p[1] * 111_320 * np.cos(np.radians(la0)), p[0] * 111_320])


def _angle(a, b):
    v = _xy(b, a[0]) - _xy(a, a[0])
    return float(np.arctan2(v[1], v[0]) % np.pi)


def _adiff(a, b):
    d = abs(a - b)
    return min(d, np.pi - d)


def _on_line(p, ang, line) -> bool:
    """Is point p (heading ang) on the polyline, within SNAP_M and the same direction?"""
    P = _xy(p, p[0])
    for a, b in zip(line[:-1], line[1:]):
        A, B = _xy(a, p[0]), _xy(b, p[0])
        AB = B - A
        if not AB.any():
            continue
        t = float(np.clip(np.dot(P - A, AB) / np.dot(AB, AB), 0, 1))
        if np.linalg.norm(A + t * AB - P) <= SNAP_M and _adiff(ang, float(np.arctan2(AB[1], AB[0]) % np.pi)) <= MAX_ANGLE:
            return True
    return False


def pieces(edges_csv: Path) -> list[dict]:
    seen, out = set(), []
    for e in csv.DictReader(open(edges_csv, encoding="utf-8")):
        key = tuple(sorted((e["from_node"], e["to_node"])))
        if key in seen:
            continue
        seen.add(key)
        pts = [tuple(map(float, p.split(","))) for p in e["polyline"].split(";")]
        k = len(pts) // 2
        mid = pts[k] if len(pts) % 2 else tuple((np.array(pts[k - 1]) + np.array(pts[k])) / 2)
        a, b = pts[max(k - 1, 0)], pts[min(k, len(pts) - 1)] if len(pts) % 2 == 0 else pts[min(k + 1, len(pts) - 1)]
        out.append({"mid": mid, "ang": _angle(a, b), "len": float(e["length_m"]), "name": e["name"], "group": group(e["road_class"]),
                    "pts": pts})
    return out


def collect(max_requests: int = DAILY_CAP, edges_csv: Path = ROOT / "build" / "city_2025" / "road_edges.csv") -> dict:
    doc = json.loads(OUT.read_text()) if OUT.exists() else {
        "_meta": {"what": __doc__.strip().split("\n\n")[0], "source": "TomTom Traffic Flow API v4 flowSegmentData, freeFlowSpeed",
                  "fields": "points: [lat, lon, direction in degrees 0-180, free-flow km/h, TomTom road class FRC]"},
        "points": [], "segments": [], "asked": 0}
    # pieces TomTom had nothing for (its reply was another road): never asked again
    missed = {(round(s["line"][0][0], 6), round(s["line"][0][1], 6)) for s in doc["segments"] if not s["kmh"]}
    missed |= {tuple(m) for m in doc.get("missed", [])}
    doc["segments"] = [s for s in doc["segments"] if s["kmh"]]
    doc["missed"] = sorted(missed)
    segs = [s["line"] for s in doc["segments"]]
    boxes = []
    pad = 0.0004                                               # ~40 m

    def box(line):
        la, lo = [q[0] for q in line], [q[1] for q in line]
        return (min(la) - pad, max(la) + pad, min(lo) - pad, max(lo) + pad)

    boxes = [box(l) for l in segs]

    def covered(p):
        la, lo = p["mid"]
        if (round(la, 6), round(lo, 6)) in missed:
            return True
        return any(b[0] <= la <= b[1] and b[2] <= lo <= b[3] and _on_line(p["mid"], p["ang"], segs[i])
                   for i, b in enumerate(boxes))

    # main roads first, longest first; then local streets
    rank = {"motorway": 0, "arterial": 1, "collector": 2, "local": 3}
    todo = sorted(pieces(edges_csv), key=lambda p: (rank[p["group"]], -p["len"]))
    today = __import__("datetime").date.today().isoformat()
    usage = doc.setdefault("usage_by_day", {})
    max_requests = min(max_requests, DAILY_CAP - usage.get(today, 0))
    if max_requests <= 0:
        print(f"today's allowance is used ({usage.get(today, 0)} requests); run again tomorrow")
        return doc
    asked = 0
    for p in todo:
        if covered(p):
            continue
        if asked >= max_requests:
            break
        req = urllib.request.Request(URL.format(lat=round(p["mid"][0], 6), lon=round(p["mid"][1], 6)))
        r = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    r = json.loads(resp.read())["flowSegmentData"]
                break
            except Exception:
                time.sleep(2 ** attempt)
        asked += 1
        usage[today] = usage.get(today, 0) + 1
        time.sleep(0.22)
        if not r:
            continue
        line = [(c["latitude"], c["longitude"]) for c in r["coordinates"]["coordinate"]]
        if not _on_line(p["mid"], p["ang"], line) or not r.get("freeFlowSpeed"):
            # TomTom snapped to another road: remember the piece as asked so it is not asked again
            m = (round(p["mid"][0], 6), round(p["mid"][1], 6))
            missed.add(m); doc["missed"].append(list(m))
            continue
        kmh = int(r["freeFlowSpeed"])
        # a point every ~100 m along TomTom's segment, with its local direction
        pts = []
        for a, b in zip(line[:-1], line[1:]):
            pts.append([round(a[0], 6), round(a[1], 6), round(np.degrees(_angle(a, b)), 1), kmh, r.get("frc")])
        doc["points"] += pts[:: max(1, len(pts) // max(1, int(sum(np.linalg.norm(_xy(b, a[0]) - _xy(a, a[0])) for a, b in zip(line[:-1], line[1:])) / 100)))]
        doc["segments"].append({"line": [list(map(lambda x: round(x, 6), q)) for q in line], "kmh": kmh})
        segs.append(line); boxes.append(box(line))
        if asked % 100 == 0:
            doc["asked"] += 100
            OUT.write_text(json.dumps(doc, separators=(",", ":")))
            print(f"asked {asked}, covered {len(segs)} TomTom segments", flush=True)
    doc["asked"] += asked % 100
    remaining = sum(1 for p in todo if not covered(p))
    doc["_meta"]["pieces_with_no_tomtom_data"] = len(missed)
    doc["_meta"]["pieces_not_yet_covered"] = remaining
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"asked {asked} this run; pieces still not covered: {remaining}")
    return doc


ROUTE_URL = "https://api.tomtom.com/maps/orbis/routing/routes/calculate"
ROUTE_MIN_M = 60
ROUTE_LEN_TOL = 0.15
ROUTE_DEPART = "2027-07-13T03:00:00-04:00"           # night: TomTom's typical free-flow speed on the street


def route_phase(max_requests: int = DAILY_CAP, edges_csv: Path = ROOT / "build" / "city_2025" / "road_edges.csv") -> dict:
    """Streets the flow service has no data for: TomTom's own typical speed from its routing engine.
    Route from one end of the piece to the other at night; if TomTom's route has the piece's length
    (within 15%), it followed the street, and length / time is TomTom's speed for it."""
    doc = json.loads(OUT.read_text())
    segs = [s["line"] for s in doc["segments"] if s["kmh"]]
    pad = 0.0004
    boxes = [(min(q[0] for q in l) - pad, max(q[0] for q in l) + pad, min(q[1] for q in l) - pad, max(q[1] for q in l) + pad) for l in segs]
    routed = {tuple(k) for k in doc.setdefault("routed_asked", [])}
    today = __import__("datetime").date.today().isoformat()
    usage = doc.setdefault("usage_by_day", {})
    max_requests = min(max_requests, DAILY_CAP - usage.get(today, 0))
    if max_requests <= 0:
        print(f"today's allowance is used ({usage.get(today, 0)} requests); run again tomorrow")
        return doc
    rank = {"motorway": 0, "arterial": 1, "collector": 2, "local": 3}
    todo = []
    for p in pieces(edges_csv):
        key = (round(p["mid"][0], 6), round(p["mid"][1], 6))
        if p["len"] < ROUTE_MIN_M or key in routed:
            continue
        la, lo = p["mid"]
        if any(b[0] <= la <= b[1] and b[2] <= lo <= b[3] and _on_line(p["mid"], p["ang"], segs[i]) for i, b in enumerate(boxes)):
            continue
        todo.append(p)
    todo.sort(key=lambda p: (rank[p["group"]], -p["len"]))
    asked, got = 0, 0
    for p in todo:
        if asked >= max_requests:
            break
        o, d = p["pts"][0], p["pts"][-1]
        body = {"routePlanningLocations": {"origin": {"type": "Point", "coordinates": [o[1], o[0]]},
                                           "destination": {"type": "Point", "coordinates": [d[1], d[0]]}},
                "departureDateTime": ROUTE_DEPART, "traffic": "historical"}
        req = urllib.request.Request(ROUTE_URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "TomTom-Api-Version": "3", "Attributes": "routes.summary"})
        r = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    r = json.loads(resp.read())["routes"][0]["summary"]
                break
            except Exception:
                time.sleep(2 ** attempt)
        asked += 1
        usage[today] = usage.get(today, 0) + 1
        key = [round(p["mid"][0], 6), round(p["mid"][1], 6)]
        doc["routed_asked"].append(key)
        time.sleep(0.22)
        if r and r.get("travelDurationInSeconds") and abs(r["lengthInMeters"] / p["len"] - 1) <= ROUTE_LEN_TOL:
            kmh = round(r["lengthInMeters"] / r["travelDurationInSeconds"] * 3.6, 1)
            doc["segments"].append({"line": [[round(q[0], 6), round(q[1], 6)] for q in p["pts"]], "kmh": kmh, "src": "route"})
            got += 1
        if asked % 100 == 0:
            OUT.write_text(json.dumps(doc, separators=(",", ":")))
            print(f"routed {asked}, matched {got}", flush=True)
    doc["_meta"]["route_phase"] = ("Streets without flow data: TomTom routing (Orbis v3, typical traffic at 3 am) from one end of the "
                                   "piece to the other; kept when TomTom's route length is within 15% of the piece (it followed the street).")
    doc["_meta"]["pieces_left_to_route"] = len(todo) - asked
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"routed {asked} this run, matched {got}; pieces left to route: {len(todo) - asked}")
    return doc


def speeds_for(edges: list[dict]) -> list[float | None]:
    """TomTom free-flow km/h for each road piece (dicts with polyline points), or None."""
    if not OUT.exists():
        return [None] * len(edges)
    doc = json.loads(OUT.read_text())
    segs = [(s["line"], s["kmh"]) for s in doc["segments"] if s["kmh"]]
    # index segments by a coarse grid for speed
    grid = {}
    for i, (line, _) in enumerate(segs):
        for q in line:
            grid.setdefault((round(q[0], 2), round(q[1], 2)), set()).add(i)
    out = []
    for e in edges:
        pts = e["pts"]
        if len(pts) < 2:
            out.append(None)
            continue
        k = len(pts) // 2
        mid = pts[k]
        ang = _angle(pts[max(k - 1, 0)], pts[min(k + 1, len(pts) - 1)])
        cand = set()
        for dla in (-0.01, 0, 0.01):
            for dlo in (-0.01, 0, 0.01):
                cand |= grid.get((round(mid[0] + dla, 2), round(mid[1] + dlo, 2)), set())
        hit = [segs[i][1] for i in cand if _on_line(mid, ang, segs[i][0])]
        out.append(float(np.median(hit)) if hit else None)
    return out


if __name__ == "__main__":
    n = int(sys.argv[2]) if len(sys.argv) > 2 else DAILY_CAP
    {"flow": collect, "route": route_phase}[sys.argv[1] if len(sys.argv) > 1 else "flow"](n)
