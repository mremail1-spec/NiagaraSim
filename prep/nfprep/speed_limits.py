"""The legal limit next to the real speed on every road piece.

- Real speed: TomTom's measured free-flow speed (data/street_speeds_tomtom.json; third-party, measured): what
  drivers do when the road is clear. Not rush-hour speed.
- Legal limit: the posted speed limit from the City of Niagara Falls Traffic Engineering road segments
  (data/city_open_data/world/road_segments.geojson; government data). City and Regional roads inside the city;
  the QEW and Highway 405 are provincial and not in it (their limits are 100 km/h).

For each road type the median ratio real / limit says how drivers treat the limit on clear roads. Where TomTom has
no speed for a piece, its time can be taken as limit x that ratio (speed_where_unmeasured), so an unmeasured
street gets its own legal limit, corrected by how drivers really behave on streets of its kind, instead of a
guess by road class. Measured speed always wins where it exists (trust order: measured third-party first).

Writes data/street_speed_vs_limit.json (holds TomTom-derived speeds: keep the repository private).
Run from prep/: python3 -m nfprep.speed_limits
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .loader import DATA_DIR
from .street_speeds import _angle, _on_line, speeds_for

ROOT = Path(__file__).resolve().parents[2]
SEGMENTS = ROOT / "data" / "city_open_data" / "world" / "road_segments.geojson"
OUT = DATA_DIR / "street_speed_vs_limit.json"
EDGES = ROOT / "build" / "city_2025" / "road_edges.csv"
PROVINCIAL_KMH = 100          # QEW and Highway 405: posted 100 km/h (Ontario), not in the City's file


def _city_segments():
    out = []
    for f in json.loads(SEGMENTS.read_text())["features"]:
        g, p = f["geometry"], f["properties"]
        lim = p.get("PostSpLim")
        if not g or not lim:
            continue
        lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for line in lines:
            out.append(([[q[1], q[0]] for q in line], int(lim), p.get("NumLanes"), (p.get("StreetName") or "").strip()))
    return out


def limits_for(edges: list[dict]) -> list[tuple[int | None, int | None]]:
    """(posted km/h, lanes) from the City's segments for each road piece (dicts with 'pts' [(lat, lon)...] and
    'class'), or (None, None) where the City has no segment there."""
    segs = _city_segments()
    grid = defaultdict(set)
    for i, (line, *_r) in enumerate(segs):
        for q in line:
            grid[(round(q[0], 2), round(q[1], 2))].add(i)
    out = []
    for e in edges:
        pts = e["pts"]
        if e.get("class", "").startswith("motorway"):
            out.append((PROVINCIAL_KMH, None))
            continue
        if len(pts) < 2:
            out.append((None, None))
            continue
        k = len(pts) // 2
        mid, ang = pts[k], _angle(pts[max(k - 1, 0)], pts[min(k + 1, len(pts) - 1)])
        cand = set()
        for dla in (-0.01, 0, 0.01):
            for dlo in (-0.01, 0, 0.01):
                cand |= grid.get((round(mid[0] + dla, 2), round(mid[1] + dlo, 2)), set())
        hit = [segs[i] for i in cand if _on_line(mid, ang, segs[i][0])]
        if not hit:
            out.append((None, None))
            continue
        lims = [h[1] for h in hit]
        lanes = [h[2] for h in hit if h[2]]
        out.append((int(np.median(lims)), int(np.median(lanes)) if lanes else None))
    return out


def main(edges_csv: Path = EDGES) -> dict:
    from .road_times import group
    rows = list(csv.DictReader(open(edges_csv)))
    edges = [{"pts": [tuple(map(float, p.split(","))) for p in r["polyline"].split(";")], "class": r["road_class"]} for r in rows]
    real_src = speeds_for(edges, with_source=True)
    real = [v for v, _ in real_src]
    src = [k for _, k in real_src]
    lim = limits_for(edges)
    by_group = defaultdict(lambda: {"km": 0.0, "km_limit": 0.0, "km_both": 0.0, "ratios": [], "w": [], "over_km": 0.0,
                                    "route_ratios": [], "route_w": []})
    streets = defaultdict(lambda: {"km": 0.0, "real_x_km": 0.0, "limit_x_km": 0.0})
    for r, v, k, (L, _lanes) in zip(rows, real, src, lim):
        g = group(r["road_class"])
        km = float(r["length_m"]) / 1000 / 2            # two-way streets appear once each way
        G = by_group[g]
        G["km"] += km
        if L:
            G["km_limit"] += km
        if L and v and v > 5 and k == "route":          # route times include turns and stops: kept apart
            G["route_ratios"].append(v / L); G["route_w"].append(km)
            continue
        if L and v and v > 5:
            G["km_both"] += km
            G["ratios"].append(v / L); G["w"].append(km)
            G["over_km"] += km if v > L + 2 else 0.0
            if r["name"]:
                s = streets[(r["name"], g)]
                s["km"] += km; s["real_x_km"] += v * km; s["limit_x_km"] += L * km
    def wmed(ratios, w):
        ratios, w = np.array(ratios), np.array(w)
        if not len(ratios):
            return None
        order = np.argsort(ratios)
        cw = np.cumsum(w[order]) / w.sum()
        return float(ratios[order][np.searchsorted(cw, 0.5)])

    summary = {}
    for g, G in by_group.items():
        med = wmed(G["ratios"], G["w"])
        rmed = wmed(G["route_ratios"], G["route_w"])
        summary[g] = {"km": round(G["km"], 1), "km_with_city_limit": round(G["km_limit"], 1), "km_with_both": round(G["km_both"], 1),
                      "real_over_limit_median": round(med, 3) if med else None,
                      "km_route_only": round(sum(G["route_w"]), 1),
                      "route_over_limit_median": round(rmed, 3) if rmed else None,
                      "share_faster_than_limit": round(G["over_km"] / G["km_both"], 3) if G["km_both"] else None}
    table = []
    for (name, g), s in streets.items():
        if s["km"] >= 0.5:
            real_k, lim_k = s["real_x_km"] / s["km"], s["limit_x_km"] / s["km"]
            table.append({"street": name, "type": g, "km": round(s["km"], 2), "real_kmh": round(real_k), "limit_kmh": round(lim_k),
                          "real_over_limit": round(real_k / lim_k, 2)})
    table.sort(key=lambda t: -t["real_over_limit"])
    pieces = {r["edge_id"]: [round(v, 1) if v else None, L, k] for r, v, k, (L, _l) in zip(rows, real, src, lim)}
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0],
                     "real": "TomTom measured free-flow speed (third-party, measured; clear roads, not rush hour). Pieces TomTom has no flow for were timed with TomTom routing end to end (route): those times include turns and stops, so they are kept apart (route_over_limit_median) and left out of real_over_limit_median and the street table",
                     "pieces": "edge id -> [TomTom km/h, posted km/h, flow or route]",
                     "limit": "City of Niagara Falls posted speed limits (government data); QEW/405 at the provincial 100 km/h",
                     "speed_where_unmeasured": "limit x real_over_limit_median of the road type"},
           "by_type": summary, "streets": table, "pieces": pieces}
    OUT.write_text(json.dumps(doc, indent=1))
    return doc


if __name__ == "__main__":
    d = main()
    for g, s in d["by_type"].items():
        print(g, s)
    t = d["streets"]
    print("fastest vs limit:", [(x["street"], x["real_kmh"], x["limit_kmh"]) for x in t[:8]])
    print("slowest vs limit:", [(x["street"], x["real_kmh"], x["limit_kmh"]) for x in t[-8:]])
