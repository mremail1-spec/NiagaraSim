"""Real driving minutes and route choice on the road network.

Two numbers per road segment:
  - minutes: the real driving time, from posted or class speeds, scaled by road type and with
    waits at traffic lights and stop signs. The factors are fitted to TomTom's typical weekday
    daytime trip times (python3 -m nfprep.road_times fit; data/road_time_factors.json).
  - route_cost: what a driver weighs when choosing a route: the minutes, with main roads
    preferred (side streets and collectors weigh more than their minutes). The preference is
    fitted to the traffic counts (python3 -m nfprep.road_times search).
The engine chooses routes on route_cost and times the trip in minutes along the chosen route.

Usage: python3 -m nfprep.road_times fit      (needs build/tomtom_travel_times.json from travel_times_check)
       python3 -m nfprep.road_times search   (tries route preferences against the counts; slow)
"""
from __future__ import annotations

import csv
import heapq
import json
import sys
from pathlib import Path

import numpy as np

from . import intersections
from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
FACTORS = DATA_DIR / "road_time_factors.json"
GROUPS = ("motorway", "arterial", "collector", "local")
DAYTIME = ("Tue Jul 8am", "Tue Jul noon", "Tue Jul 5pm", "Tue Jul 8pm")
COLS = ("measured", "unmeasured", "signals", "stops")


def group(road_class: str) -> str:
    if road_class.startswith(("motorway", "trunk")):
        return "motorway"
    if road_class.startswith(("primary", "secondary")):
        return "arterial"
    if road_class.startswith("tertiary"):
        return "collector"
    return "local"


def _network(city: dict, edges_csv: Path):
    lat, lon = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    ed = city["edges"]
    a, b = np.array(ed["from"]), np.array(ed["to"])
    rows = list(csv.DictReader(open(edges_csv)))
    assert all(int(r["from_node"]) == f and int(r["to_node"]) == t for r, f, t in zip(rows, a, b))
    cls = [r["road_class"] for r in rows] + ["connector"] * (len(a) - len(rows))
    base = np.array([float(r["minutes"]) for r in rows] + list(ed["minutes"][len(rows):]))
    # measured: TomTom free-flow speed on the street, wherever TomTom covers it
    from .street_speeds import speeds_for
    kmh = speeds_for([{"pts": [tuple(map(float, p.split(","))) for p in r["polyline"].split(";")]} for r in rows]) if rows else []
    measured = np.zeros(len(a), bool)
    for i, (r, v) in enumerate(zip(rows, kmh)):
        if v and v > 5:
            base[i] = float(r["length_m"]) / 1000 / v * 60
            measured[i] = True
    nb = [set() for _ in lat]
    for x, y in zip(a, b):
        nb[x].add(y); nb[y].add(x)
    sig, allway, stop = intersections.controls(lat, lon, np.array([len(s) >= 3 for s in nb]))
    wait = np.array(["signal" if t in sig else "stop" if (t in allway or (t in stop and c in intersections.MINOR)) else ""
                     for t, c in zip(b, cls)])
    return lat, lon, a, b, cls, base, wait, measured


def apply(city_path: Path, edges_csv: Path, preference: dict | None = None) -> dict:
    """Set real minutes and route cost on the city's edges (from the base minutes in road_edges.csv)."""
    f = json.loads(FACTORS.read_text())
    pref = preference or f["route_preference"]
    city = json.loads(Path(city_path).read_text())
    _, _, a, b, cls, base, wait, measured = _network(city, edges_csv)
    minutes = base * np.where(measured, f["speed"]["measured"], f["speed"]["unmeasured"])
    minutes[np.array([c == "connector" for c in cls])] = base[np.array([c == "connector" for c in cls])]
    minutes += np.where(wait == "signal", f["seconds_per_signal"] / 60, 0) + np.where(wait == "stop", f["seconds_per_stop"] / 60, 0)
    g = [group(c) if c != "connector" else "" for c in cls]
    cost = np.array([m * pref.get(k, 1.0) for m, k in zip(minutes, g)])
    city["edges"]["minutes"] = [round(float(x), 4) for x in minutes]
    city["edges"]["route_cost"] = [round(float(x), 4) for x in cost]
    city.setdefault("meta", {})["road_times"] = {"speed": f["speed"], "seconds_per_signal": f["seconds_per_signal"],
                                                 "seconds_per_stop": f["seconds_per_stop"], "route_preference": pref,
                                                 "pieces_with_measured_speed": int(measured.sum() // 2)}
    Path(city_path).write_text(json.dumps(city, separators=(",", ":")))
    return city["meta"]["road_times"]


def fit(city_path: Path = ROOT / "build" / "city_2025" / "city.json") -> dict:
    """Least squares of TomTom's weekday daytime minutes on the model route's base minutes by road
    type plus its number of traffic lights and stop signs (routes as TomTom and the model agree, 1%)."""
    from . import travel_times_check as T
    city = json.loads(city_path.read_text())
    lat, lon, a, b, cls, base, wait, measured = _network(city, city_path.with_name("road_edges.csv"))
    n = len(lat)
    ok = np.zeros(n, bool)
    for i in range(len(a)):
        if cls[i] not in T.NO_SNAP:
            ok[a[i]] = True
    def snap(p):
        d = np.hypot((lat - p[0]) * 111_320, (lon - p[1]) * 111_320 * np.cos(np.radians(p[0])))
        d[~ok] = np.inf
        return int(d.argmin())
    where = {k: snap(v) for k, v in {**T.HOMES, **T.PLACES}.items()}
    out = [[] for _ in range(n)]
    for i, x in enumerate(a):
        out[x].append(i)
    cache = json.loads(T.CACHE.read_text())
    X, y = [], []
    for h, p in T.pairs():
        o, d = where[h], where[p]
        dist, via = np.full(n, np.inf), np.full(n, -1)
        dist[o] = 0.0
        q = [(0.0, o)]
        while q:
            dv, v = heapq.heappop(q)
            if dv > dist[v]:
                continue
            for i in out[v]:
                if dv + base[i] < dist[b[i]]:
                    dist[b[i]] = dv + base[i]; via[b[i]] = i; heapq.heappush(q, (dist[b[i]], int(b[i])))
        row = dict.fromkeys(COLS, 0.0)
        cur = d
        while cur != o:
            i = via[cur]
            if cls[i] != "connector":
                row["measured" if measured[i] else "unmeasured"] += base[i]
            row["signals"] += wait[i] == "signal"
            row["stops"] += wait[i] == "stop"
            cur = a[i]
        X.append([row[k] for k in COLS])
        when = {l: w for w, l in T.TIMES}
        y.append(np.mean([cache[f"{o}>{d}@{when[l]}"]["s"] / 60 for l in DAYTIME]))
    X, y = np.array(X), np.array(y)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    coef = np.maximum(coef, 0.0)
    # when the test trips hardly use unmeasured roads (once TomTom covers nearly every street), their
    # factor cannot be estimated: they take the measured roads' factor
    if X[:, 1].sum() < 0.05 * X[:, :2].sum():
        Xc = np.column_stack([X[:, 0] + X[:, 1], X[:, 2], X[:, 3]])
        c2, *_ = np.linalg.lstsq(Xc, y, rcond=None)
        c2 = np.maximum(c2, 0.0)
        coef = np.array([c2[0], c2[0], c2[1], c2[2]])
    pred = X @ coef
    old = json.loads(FACTORS.read_text()) if FACTORS.exists() else {}
    doc = {"_meta": {"what": "Real driving time on the model's roads: each piece's minutes at TomTom's measured free-flow speed "
                             "(data/street_speeds_tomtom.json) or, where TomTom has none (local streets), at the posted or class speed; "
                             "times a daytime factor for each of the two, plus seconds per traffic light and per stop sign. Fitted to TomTom typical weekday daytime trip times "
                             "(8 am, noon, 5 pm, 8 pm), 40 trips; written by python3 -m nfprep.road_times fit. route_preference: "
                             "how much more than its minutes a driver weighs each road type when choosing a route, fitted to the "
                             "traffic counts (python3 -m nfprep.road_times search).",
                     "trips": len(y), "fit_correlation": round(float(np.corrcoef(pred, y)[0, 1]), 3),
                     "fit_median_ratio": round(float(np.median(pred / y)), 3)},
           "speed": {"measured": round(float(coef[0]), 3), "unmeasured": round(float(coef[1]), 3)},
           "seconds_per_signal": round(float(coef[2] * 60), 1), "seconds_per_stop": round(float(coef[3] * 60), 1),
           "route_preference": old.get("route_preference", {"motorway": 1.0, "arterial": 1.0, "collector": 1.0, "local": 1.0})}
    FACTORS.write_text(json.dumps(doc, indent=1))
    print(json.dumps(doc, indent=1))
    return doc


def search() -> dict:
    """Route preference against the counts: each try rebuilds passing traffic and every traffic layer."""
    import contextlib
    import io
    import shutil
    from . import passby, traffic_layers
    city_dir = ROOT / "build" / "city_2025"
    keep = ["city.json", "passing_traffic.json", "background_volumes.json", "traffic_year.json", "node_volumes.json"]
    backup = city_dir / "road_times_backup"
    backup.mkdir(exist_ok=True)
    for k in keep:
        shutil.copy(city_dir / k, backup / k)
    tries = [{"motorway": 1.0, "arterial": a, "collector": c, "local": l} for a, c, l in
             [(1.0, 1.0, 1.0), (0.78, 1.0, 1.0), (1.0, 1.2, 1.5), (1.0, 1.4, 2.0), (0.78, 1.0, 1.5),
              (1.0, 1.6, 3.0), (0.78, 1.2, 2.0), (1.0, 2.0, 4.0)]]
    results = []
    for p in tries:
        for k in keep:
            shutil.copy(backup / k, city_dir / k)
        apply(city_dir / "city.json", city_dir / "road_edges.csv", p)
        with contextlib.redirect_stdout(io.StringIO()):
            passby.apply(city_dir / "city.json")
        r = traffic_layers.main_quiet()
        results.append({"preference": p, **r})
        print(p, r, flush=True)
        (ROOT / "docs" / "route_preference_search.json").write_text(json.dumps(results, indent=1))
    for k in keep:
        shutil.copy(backup / k, city_dir / k)
    shutil.rmtree(backup)
    (ROOT / "docs" / "route_preference_search.json").write_text(json.dumps(results, indent=1))
    return {"results": results}


if __name__ == "__main__":
    {"fit": fit, "search": search}[sys.argv[1]]()
