"""Model travel times against TomTom's typical-traffic travel times.

40 trips across Niagara Falls (10 home areas, each to 4 destinations), asked of TomTom's Routing API
(Orbis v3, traffic "historical") for future departure times: a Tuesday and a Saturday in July and in
November. TomTom answers from its record of typical speeds for that weekday and hour, so this is the
usual trip time, not a live reading. The same trips are timed on the model's road network:
  - as the engine has them: the route a driver chooses (route cost, prep/nfprep/road_times.py),
    timed in real minutes (road speeds and waits at lights and stop signs fitted to TomTom).

TomTom's replies are kept in build/ only (not committed: their terms limit storing results); docs/
gets the comparison figures. Needs the TomTom key saved as a network secret for api.tomtom.com
(header TomTom-Api-Key, injected by the session proxy).

Usage: python3 -m nfprep.travel_times_check            (asks TomTom only for trips not yet cached)
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import heapq


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "build" / "tomtom_travel_times.json"
URL = "https://api.tomtom.com/maps/orbis/routing/routes/calculate"

HOMES = {
    "Chippawa": (43.0585, -79.0525), "Montrose at Lundy's Lane": (43.0890, -79.1232),
    "Mount Carmel": (43.1170, -79.1180), "Stamford (Portage at Thorold Stone)": (43.1220, -79.0890),
    "Dorchester at Morrison": (43.1035, -79.0925), "McLeod at Dorchester": (43.0705, -79.1000),
    "Garner at Lundy's Lane": (43.0875, -79.1500), "Drummond at Thorold Stone": (43.1190, -79.0985),
    "Downtown (Victoria at Bridge)": (43.1095, -79.0700), "Valley Way at Drummond": (43.0960, -79.0950),
}
PLACES = {
    "Clifton Hill (top)": (43.091992, -79.07622), "Falls Parking Lot A": (43.07644, -79.07996),
    "Main at Ferry": (43.0895, -79.092734), "Queen at Erie": (43.106638, -79.063449),
    "Fallsview Casino": (43.0800, -79.0830), "Lundy's Lane at the QEW": (43.0890, -79.1080),
    "McLeod Road shops": (43.0705, -79.1150), "South Niagara hospital site": (43.0545, -79.1215),
    "Rainbow Bridge": (43.0905, -79.0680), "Niagara Square": (43.0705, -79.1125),
}
# typical-traffic departures (local time; EDT in July, EST in November)
TIMES = [("2027-07-13T03:00:00-04:00", "Tue Jul 3am"), ("2027-07-13T08:00:00-04:00", "Tue Jul 8am"),
         ("2027-07-13T12:00:00-04:00", "Tue Jul noon"), ("2027-07-13T17:00:00-04:00", "Tue Jul 5pm"),
         ("2027-07-13T20:00:00-04:00", "Tue Jul 8pm"), ("2027-07-17T12:00:00-04:00", "Sat Jul noon"),
         ("2027-07-17T17:00:00-04:00", "Sat Jul 5pm"), ("2027-07-17T20:00:00-04:00", "Sat Jul 8pm"),
         ("2027-11-16T03:00:00-05:00", "Tue Nov 3am"), ("2027-11-16T08:00:00-05:00", "Tue Nov 8am"),
         ("2027-11-16T12:00:00-05:00", "Tue Nov noon"), ("2027-11-16T17:00:00-05:00", "Tue Nov 5pm"),
         ("2027-11-20T13:00:00-05:00", "Sat Nov 1pm")]
NO_SNAP = {"motorway", "motorway_link", "trunk_link"}


def pairs():
    h, p = list(HOMES), list(PLACES)
    return [(h[i], p[(i + k) % len(p)]) for i in range(len(h)) for k in (0, 2, 4, 7)]


def tomtom(o, d, when):
    body = {"routePlanningLocations": {"origin": {"type": "Point", "coordinates": [o[1], o[0]]},
                                       "destination": {"type": "Point", "coordinates": [d[1], d[0]]}},
            "departureDateTime": when, "traffic": "historical"}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "TomTom-Api-Version": "3",
                                          "Attributes": "routes.summary"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                s = json.loads(r.read())["routes"][0]["summary"]
            return {"s": s["travelDurationInSeconds"], "m": s["lengthInMeters"]}
        except Exception as e:                                       # rate limit or network: back off
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def main(city_path: Path = ROOT / "build" / "city_2025" / "city.json") -> dict:
    city = json.loads(city_path.read_text())
    lat, lon = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    ed = city["edges"]
    a, b = np.array(ed["from"]), np.array(ed["to"])
    minutes = np.array(ed["minutes"], dtype=float)
    length = np.array(ed["km"], dtype=float) * 1000
    rows_csv = list(csv.DictReader(open(city_path.with_name("road_edges.csv"))))
    # city.json lists the same edges in the same order, plus a few connectors appended at the end
    assert all(int(r["from_node"]) == f and int(r["to_node"]) == t for r, f, t in zip(rows_csv, a, b))
    cls = [r["road_class"] for r in rows_csv] + ["connector"] * (len(a) - len(rows_csv))
    n = len(lat)

    # snap each place to the nearest node with an ordinary (non-motorway) road leaving it
    ok = np.zeros(n, bool)
    for i in range(len(a)):
        if cls[i] not in NO_SNAP:
            ok[a[i]] = True
    def snap(p):
        d = np.hypot((lat - p[0]) * 111_320, (lon - p[1]) * 111_320 * np.cos(np.radians(p[0])))
        d[~ok] = np.inf
        return int(d.argmin())
    where = {k: snap(v) for k, v in {**HOMES, **PLACES}.items()}

    # model times as the engine has them: route chosen on route cost, timed in real minutes
    cost = np.array(ed.get("route_cost") or ed["minutes"], dtype=float)
    origins = sorted({where[h] for h in HOMES})
    out_edges = [[] for _ in range(n)]
    for i, x in enumerate(a):
        out_edges[x].append(i)
    def times():
        res = {}
        for o in origins:
            best = np.full(n, np.inf); mins = np.full(n, np.inf); pred = np.full(n, -1); best[o] = 0.0; mins[o] = 0.0
            q = [(0.0, o)]
            while q:
                dv, v = heapq.heappop(q)
                if dv > best[v]:
                    continue
                for i in out_edges[v]:
                    nd = dv + cost[i]
                    if nd < best[b[i]]:
                        best[b[i]] = nd; mins[b[i]] = mins[v] + minutes[i]; pred[b[i]] = v
                        heapq.heappush(q, (nd, int(b[i])))
            res[o] = (mins, pred)
        return res
    base = times()
    edge_len = {(int(x), int(y)): float(l) for x, y, l in zip(a, b, length)}

    def path_m(tree, o, d):
        pred, m, cur = tree[o][1], 0.0, d
        while cur != o and cur >= 0:
            p = int(pred[cur]); m += edge_len.get((p, cur), 0.0); cur = p
        return m

    # TomTom, cached
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    asked = 0
    for h, p in pairs():
        o, d = where[h], where[p]
        for when, _ in TIMES:
            key = f"{o}>{d}@{when}"
            if key not in cache:
                cache[key] = tomtom((lat[o], lon[o]), (lat[d], lon[d]), when)
                asked += 1
                time.sleep(0.25)
                if asked % 50 == 0:
                    CACHE.write_text(json.dumps(cache))
    CACHE.write_text(json.dumps(cache))

    rows = []
    for h, p in pairs():
        o, d = where[h], where[p]
        tt = {label: cache[f"{o}>{d}@{when}"] for when, label in TIMES}
        rows.append({"from": h, "to": p, "model_min": round(float(base[o][0][d]), 2),
                     "model_km": round(path_m(base, o, d) / 1000, 2),
                     "tomtom_km": round(tt["Tue Jul 3am"]["m"] / 1000, 2),
                     "tomtom_min": {k: round(v["s"] / 60, 2) for k, v in tt.items()}})

    def stats(model_key, label):
        m = np.array([r[model_key] for r in rows]); t = np.array([r["tomtom_min"][label] for r in rows])
        lr = np.log(m / t)
        return {"median_ratio_model_to_tomtom": round(float(np.exp(np.median(lr))), 3),
                "log_sd": round(float(lr.std()), 3), "correlation": round(float(np.corrcoef(m, t)[0, 1]), 3),
                "mean_abs_error_min": round(float(np.abs(m - t).mean()), 2)}

    labels = [l for _, l in TIMES]
    summary = {"trips": len(rows), "requests_this_run": asked,
               "as_built": {l: stats("model_min", l) for l in labels},
               "distance_ratio_model_to_tomtom": round(float(np.median([r["model_km"] / r["tomtom_km"] for r in rows])), 3)}
    night = np.array([r["tomtom_min"]["Tue Jul 3am"] for r in rows])
    summary["tomtom_slowdown_vs_3am"] = {l: round(float(np.median(np.array([r["tomtom_min"][l] for r in rows]) / night)), 3)
                                         for l in labels}
    for r in rows:
        r["model_over_tomtom_km"] = round(r["model_km"] / r.pop("tomtom_km"), 2)
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0],
                     "note": "TomTom minutes per trip are not stored here; only how the model compares."},
           "summary": summary,
           "trips": [{k: v for k, v in r.items() if k != "tomtom_min"} |
                     {"model_over_tomtom_tue_jul_noon": round(r["model_min"] / r["tomtom_min"]["Tue Jul noon"], 2)} for r in rows]}
    (ROOT / "docs" / "travel_times_check.json").write_text(json.dumps(doc, indent=1))
    print(f"asked TomTom {asked} times; distance ratio {summary['distance_ratio_model_to_tomtom']}")
    for l in labels:
        s0 = summary["as_built"][l]
        print(f"{l:13s} slowdown x{summary['tomtom_slowdown_vs_3am'][l]:.2f}  model ratio {s0['median_ratio_model_to_tomtom']} "
              f"r {s0['correlation']}")
    return doc


if __name__ == "__main__":
    main()
