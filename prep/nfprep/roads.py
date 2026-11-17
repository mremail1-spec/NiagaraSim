"""Phase 3b: the road network, from OpenStreetMap.

Ways are cut at intersections into edges with a length, a speed and the full
polyline (so the map can draw cars following the real road shape). Only roads
inside the city are kept, and only the largest connected piece, so every home
and business can reach every other.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .loader import DATA_DIR, Pack
from .population import points_in_ring

ROADS_FILE = DATA_DIR / "sources" / "osm_roads_overpass.json"
# Speed when the map has no posted limit: Ontario's statutory 50 km/h in built-up areas,
# 100 on the QEW/420 motorways, 60 on ramps (SEED for ramps).
DEFAULT_KMH = {"motorway": 100, "trunk": 80, "motorway_link": 60, "trunk_link": 60}
FALLBACK_KMH = 50
# SEED: effective free-flow speed on residential and service streets with no posted limit (parked cars,
# turns, calming). With 35 instead of 50, regional roads' correlation with counts rose 0.62 -> 0.65
# (2019 counts) and 0.65 -> 0.66 (2025), highways 0.75 -> 0.76.
LOCAL_KMH = 35
LOCAL_CLASSES = {"residential", "living_street", "service", "unclassified"}
CITY_BUFFER_M = 150      # keep roads that run along the city boundary


_REGION = None
# Off: with posted limits from the Region's file (442 edges, mostly rural and arterial roads faster),
# the fit to counted traffic got worse (regional roads correlation 0.62 -> 0.61, highways 0.75 -> 0.70;
# with the QEW at a 110 km/h operating speed as well, 0.57 and 0.70). The model has no signal delay or
# congestion, so higher posted speeds pull trips onto roads that are slower in practice. Turn on with
# congestion in place.
USE_REGION_SPEEDS = False


def _region_speed(name: str) -> float | None:
    """Posted limit from Niagara Region's road file, when one value covers most of the street."""
    global _REGION
    if _REGION is None:
        p = DATA_DIR / "region_road_speeds.json"
        _REGION = {k.lower(): v for k, v in json.loads(p.read_text())["streets"].items()} if p.exists() else {}
    v = _REGION.get(name.lower().strip())
    return float(v["speed_kmh"]) if v and v["share_of_length"] >= 0.8 else None


def _speed(tags: dict) -> float:
    raw = str(tags.get("maxspeed", "")).strip()
    try:
        return float(raw.split(" ")[0]) * (1.609 if "mph" in raw else 1.0)
    except ValueError:
        region = _region_speed(tags.get("name", "")) if USE_REGION_SPEEDS else None
        if region:
            return region
        if tags.get("highway") in LOCAL_CLASSES:
            return LOCAL_KMH
        return DEFAULT_KMH.get(tags.get("highway", ""), FALLBACK_KMH)


def _len_m(a: tuple, b: tuple) -> float:
    k = 111_320.0
    return math.hypot((b[0] - a[0]) * k, (b[1] - a[1]) * k * math.cos(math.radians(a[0])))


def build_roads(pack: Pack, path: Path = ROADS_FILE) -> tuple[list[dict], list[dict]]:
    """Return (nodes, edges). Edges are directed: a two-way street yields one edge each way."""
    ways = [e for e in json.loads(path.read_text())["elements"] if e["type"] == "way"]
    # The download carries each way's coordinates but not node ids; ways that meet share an
    # identical coordinate, so the coordinate itself identifies the node.
    coord: dict[tuple, tuple[float, float]] = {}
    for w in ways:
        w["nodes"] = [(round(g["lat"], 7), round(g["lon"], 7)) for g in w["geometry"]]
        for nid in w["nodes"]:
            coord[nid] = nid

    # city mask: inside any census tract, or within the buffer of the city's bounding polygons
    ids = list(coord)
    lat = np.array([i[0] for i in ids])
    lon = np.array([i[1] for i in ids])
    inside = np.zeros(len(ids), bool)
    for f in pack.geo["census_tracts_boundaries.geojson"]["features"]:
        ring = np.asarray(f["geometry"]["coordinates"][0], float)
        inside |= points_in_ring(lon, lat, ring)
        # buffer: near any boundary vertex
        step = max(1, len(ring) // 400)
        for vx, vy in ring[::step, :2]:
            inside |= np.hypot((lat - vy) * 111_320, (lon - vx) * 111_320 * math.cos(math.radians(vy))) < CITY_BUFFER_M
    in_city = {i for i, ok in zip(ids, inside) if ok}

    ways = [w for w in ways if sum(n in in_city for n in w["nodes"]) >= max(2, len(w["nodes"]) // 2)]
    use = Counter(n for w in ways for n in w["nodes"])
    junction = {n for w in ways for n in (w["nodes"][0], w["nodes"][-1])} | {n for n, c in use.items() if c > 1}

    raw_edges = []
    for w in ways:
        tags = w["tags"]
        kmh = _speed(tags)
        oneway = tags.get("oneway") in ("yes", "-1") or tags.get("highway") == "motorway" \
            or tags.get("junction") == "roundabout"
        start = 0
        for i in range(1, len(w["nodes"])):
            if w["nodes"][i] in junction:
                seg = w["nodes"][start:i + 1]
                pts = [coord[n] for n in seg]
                length = sum(_len_m(pts[k], pts[k + 1]) for k in range(len(pts) - 1))
                if seg[0] != seg[-1] and length > 0:
                    fwd = (seg[0], seg[-1], pts)
                    if tags.get("oneway") == "-1":
                        fwd = (seg[-1], seg[0], pts[::-1])
                    raw_edges.append((*fwd, length, kmh, tags.get("highway"), tags.get("name", "")))
                    if not oneway:
                        raw_edges.append((fwd[1], fwd[0], fwd[2][::-1], length, kmh, tags.get("highway"), tags.get("name", "")))
                start = i

    # largest connected piece (treating roads as two-way for connectivity)
    adj = defaultdict(set)
    for a, b, *_ in raw_edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, best = set(), set()
    for s in adj:
        if s in seen:
            continue
        comp, stack = {s}, [s]
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in comp:
                    comp.add(nb)
                    stack.append(nb)
        seen |= comp
        if len(comp) > len(best):
            best = comp
    # keep only nodes that can be driven both to and from the busiest junction, so that
    # one-way stubs cut off at the city edge cannot strand anyone
    hub = max(best, key=lambda n: len(adj[n]))
    fwd_adj, back_adj = defaultdict(list), defaultdict(list)
    for a, b, *_ in raw_edges:
        fwd_adj[a].append(b)
        back_adj[b].append(a)

    def reach(graph):
        got, stack = {hub}, [hub]
        while stack:
            for nb in graph[stack.pop()]:
                if nb not in got:
                    got.add(nb)
                    stack.append(nb)
        return got
    best = reach(fwd_adj) & reach(back_adj)
    index = {n: i for i, n in enumerate(sorted(best))}
    nodes = [{"node_id": i, "lat": round(coord[n][0], 6), "lon": round(coord[n][1], 6)} for n, i in index.items()]
    edges = []
    for a, b, pts, length, kmh, cls, name in raw_edges:
        if a in index and b in index:
            edges.append({"edge_id": len(edges), "from_node": index[a], "to_node": index[b],
                          "length_m": round(length, 1), "speed_kmh": round(kmh, 1),
                          "minutes": round(length / 1000 / kmh * 60, 4), "road_class": cls, "name": name,
                          "polyline": ";".join(f"{la:.6f},{lo:.6f}" for la, lo in pts)})
    return nodes, edges


def snap(nodes: list[dict], lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest road node for each point, and the distance to it in metres."""
    nlat = np.array([n["lat"] for n in nodes])
    nlon = np.array([n["lon"] for n in nodes])
    kx = 111_320 * math.cos(math.radians(float(nlat.mean())))
    out_i = np.zeros(len(lats), int)
    out_d = np.zeros(len(lats))
    for s in range(0, len(lats), 2000):
        d = np.hypot((lats[s:s + 2000, None] - nlat[None, :]) * 111_320, (lons[s:s + 2000, None] - nlon[None, :]) * kx)
        out_i[s:s + 2000] = d.argmin(axis=1)
        out_d[s:s + 2000] = d.min(axis=1)
    return out_i, out_d


def strongly_reachable(nodes: list[dict], edges: list[dict], start: int = 0) -> tuple[int, int]:
    """How many nodes can be driven to from `start`, and from how many `start` can be reached (one-ways respected)."""
    def reach(pairs):
        adj = defaultdict(list)
        for a, b in pairs:
            adj[a].append(b)
        seen, stack = {start}, [start]
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return len(seen)
    return (reach((e["from_node"], e["to_node"]) for e in edges),
            reach((e["to_node"], e["from_node"]) for e in edges))
