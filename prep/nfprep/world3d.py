"""Build the 3D city for the walk-around game: buildings, roads, sidewalks, water, parks and trees in local
metres, with each building's businesses, their model sales, and the model's traffic and foot traffic.

Writes web/world/world.json. Coordinates are decimetres on a local east/north grid around (LAT0, LON0),
delta-encoded per line or ring (first point absolute, then differences) to keep the file small.

Sources (all public):
- City of Niagara Falls open data: building footprints 2018 (outline, height), sidewalks, rivers, creeks,
  community parks, tree inventory (Terms of Use of the City of Niagara Falls). Government data.
- OpenStreetMap roads (ODbL): road lines, lanes, names, bridges.
- The model (web/city.json and the engine): businesses, yearly sales, tourist share, vehicles and walkers.

Building ids are stable: a hash of the footprint's centre to about a metre, so photos and models tied to a
building (data/building_photos.json, data/building_models.json) survive a rebuild.

Run from prep/: python3 -m nfprep.world3d
"""
import csv
import gzip
import hashlib
import json
import math
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = ROOT / "web" / "world"
NFSIM = ROOT / "engine/target/release/nfsim"
LAT0, LON0 = 43.09, -79.09          # local origin near the Rainbow Bridge
KX = 111320.0 * math.cos(math.radians(LAT0))
KY = 110540.0
FLOOR_M = 3.2
MIN_H = 3.5                          # a building of 60 m2 or more is at least one storey (some City heights are 0-2 m)
MIN_SHED_H = 2.4                     # sheds and garages
TOWER_LANDMARKS = {"Skylon Tower", "Casino Tower", "Rainbow Tower"}      # observation towers: drawn as landmarks, not footprints
NOT_A_BUILDING = {"Sky Screamer"}                         # an amusement ride
PODIUM_M2 = 3500.0                                        # above this a published tower stands on a podium
SLAB_M = (46.0, 22.0)                                     # typical hotel tower floor plate, length x depth

# road width in metres by OSM class when lanes are not tagged; lane width 3.5 m
ROAD_CLASS = {"motorway": 0, "trunk": 0, "motorway_link": 1, "trunk_link": 1, "primary": 2, "primary_link": 1,
              "secondary": 2, "secondary_link": 1, "tertiary": 3, "tertiary_link": 1, "unclassified": 4, "residential": 4}
DEFAULT_LANES = {"motorway": 4, "trunk": 4, "primary": 4, "secondary": 2, "tertiary": 2, "unclassified": 2, "residential": 2}
CONIFER = ("spruce", "pine", "fir", "cedar", "hemlock", "juniper", "yew", "arborvitae", "larch")


def xy(lat: float, lon: float) -> tuple[int, int]:
    """Decimetres east and north of the origin."""
    return round((lon - LON0) * KX * 10), round((lat - LAT0) * KY * 10)


def delta(points: list[tuple[int, int]]) -> list[int]:
    out, px, py = [], 0, 0
    for x, y in points:
        out += [x - px, y - py]
        px, py = x, y
    return out


def simplify(points, tol_dm=3):
    """Drop points closer than tol to the previous kept one (footprints carry many sub-metre jogs)."""
    if len(points) < 4:
        return points
    keep = [points[0]]
    for p in points[1:-1]:
        if abs(p[0] - keep[-1][0]) + abs(p[1] - keep[-1][1]) > tol_dm:
            keep.append(p)
    keep.append(points[-1])
    return keep


def ccw(ring):
    a = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))
    return ring if a > 0 else ring[::-1]


def point_in(ring, x, y):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def building_id(lat, lon, seen):
    h = hashlib.sha1(f"{lat:.5f},{lon:.5f}".encode()).hexdigest()[:10]
    i, out = 1, h
    while out in seen:
        i += 1; out = f"{h}{i}"
    seen.add(out)
    return out


def model_sales(city_path: Path, beta: float) -> dict[int, tuple[float, float]]:
    with tempfile.TemporaryDirectory() as d:
        bp = Path(d) / "b.csv"
        subprocess.run([str(NFSIM), "run", str(city_path), "--mode", "flow", "--days", "365", "--beta", str(beta),
                        "--closures-as-of", "209912", "--businesses", str(bp)], check=True, capture_output=True)
        return {int(r["business"]): (float(r["revenue"]), float(r["tourist_revenue"])) for r in csv.DictReader(open(bp))}


def in_core(lat, lon, core_rings):
    x, y = lon, lat
    return any(point_in(r, x, y) for r in core_rings)


def parcels_for(blds):
    """Each building's property parcel (City of Niagara Falls open data, government): the lot its middle stands on.
    Returns (address, lot area m², parcel id) per building, or None."""
    path = DATA / "city_open_data/world/parcels.geojson"
    if not path.exists():
        return [None] * len(blds)
    lots, grid = [], defaultdict(list)
    for f in json.loads(path.read_text())["features"]:
        g, pr = f["geometry"], f["properties"]
        if not g:
            continue
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            ring = [xy(la, lo) for lo, la in poly[0]]
            k = len(lots)
            lots.append((ring, pr))
            xs, ys = [q[0] for q in ring], [q[1] for q in ring]
            for cx in range(min(xs) // 500, max(xs) // 500 + 1):
                for cy in range(min(ys) // 500, max(ys) // 500 + 1):
                    grid[(cx, cy)].append(k)
    out = []
    for bl in blds:
        ring = bl["ring"]
        mx, my = sum(q[0] for q in ring) / len(ring), sum(q[1] for q in ring) / len(ring)
        probes = [(mx, my)] + [(mx + (x - mx) * 0.7, my + (y - my) * 0.7) for x, y in ring]
        votes = defaultdict(int)
        for n, (x, y) in enumerate(probes):
            for k in grid.get((int(x) // 500, int(y) // 500), []):
                if point_in(lots[k][0], x, y):
                    votes[k] += 3 if n == 0 else 1
        if not votes:
            out.append(None)
            continue
        pr = lots[max(votes, key=votes.get)][1]
        addr = (pr.get("ADDRESS") or "").strip()
        out.append((addr, pr.get("AREA_SQM"), pr.get("pID")))
    return out


def road_speeds(way_geo):
    """(posted limit km/h, TomTom clear-road km/h, City lanes, TomTom source "flow" or "route") per OSM way,
    cached in build/world_road_speeds_v2.json by way id (the matching takes minutes)."""
    from nfprep.speed_limits import limits_for
    from nfprep.street_speeds import speeds_for
    cache_path = ROOT / "build" / "world_road_speeds_v2.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [(wid, pts, hw) for wid, pts, hw, _ in way_geo if str(wid) not in cache]
    if todo:
        edges = [{"pts": pts, "class": hw} for _, pts, hw in todo]
        real = speeds_for(edges, with_source=True)
        lim = limits_for(edges)
        for (wid, _, _), (v, src), (L, lanes) in zip(todo, real, lim):
            cache[str(wid)] = [L, round(v) if v else None, lanes, src]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    return [cache[str(wid)] for wid, *_ in way_geo]


def main():
    city_path = ROOT / "web/city.json"
    city = json.loads(city_path.read_text())
    cal = json.loads((DATA / "calibrated_multipliers.json").read_text())
    sales = model_sales(city_path, cal.get("beta_fit", cal["beta"]))
    b = city["businesses"]
    kinds = city["sim_categories"]
    tour = city["tourism"]
    nb = len(b["name"])

    # --- buildings
    raw = json.loads(gzip.open(DATA / "city_open_data/building_footprints_2018.json.gz").read())["buildings"]
    seen, blds = set(), []
    grid = defaultdict(list)                     # 50 m cells -> building indices
    for r in raw:
        ring = [xy(la, lo) for lo, la in r["ring"]]
        if ring[0] == ring[-1]:
            ring = ring[:-1]
        ring = simplify(ring)
        if len(ring) < 3:
            continue
        ring = ccw(ring)
        i = len(blds)
        h, floor_h = float(r["h"] or 0.0), (MIN_H if (r["a"] or 0) >= 60 else MIN_SHED_H)
        blds.append({"id": building_id(r["lat"], r["lon"], seen), "ring": ring, "h": max(floor_h, h), "a": r["a"] or 0.0,
                     "lat": r["lat"], "lon": r["lon"], "biz": [], "hsrc": "city" if h >= floor_h else "min"})
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        for cx in range(min(xs) // 500, max(xs) // 500 + 1):
            for cy in range(min(ys) // 500, max(ys) // 500 + 1):
                grid[(cx, cy)].append(i)

    # businesses -> the building they stand in, else the nearest within 30 m
    unplaced = []
    for k in range(nb):
        if b["external"][k]:
            continue
        x, y = xy(b["lat"][k], b["lon"][k])
        cand = grid.get((x // 500, y // 500), [])
        hit = next((i for i in cand if point_in(blds[i]["ring"], x, y)), None)
        if hit is None:
            best, bd = None, 300 ** 2
            for cx in (x // 500 - 1, x // 500, x // 500 + 1):
                for cy in (y // 500 - 1, y // 500, y // 500 + 1):
                    for i in grid.get((cx, cy), []):
                        for p in blds[i]["ring"]:
                            d = (p[0] - x) ** 2 + (p[1] - y) ** 2
                            if d < bd:
                                best, bd = i, d
            hit = best
        if hit is None:
            unplaced.append(k)
        else:
            blds[hit]["biz"].append(k)

    # towers: the City's 2018 heights stop near 41 m; use published heights (data/building_heights_wikipedia.json).
    # A small footprint is raised whole; on a big podium the tower is added as a slab of a typical hotel floor plate,
    # turned to the podium's longest wall (an approximate outline until a photo or model replaces it).
    towers = json.loads((DATA / "building_heights_wikipedia.json").read_text())["towers"]
    raised = slabs = 0
    for t in towers:
        if t["name"] in TOWER_LANDMARKS or t["name"] in NOT_A_BUILDING:
            continue
        x, y = xy(t["lat"], t["lon"])
        cand = [i for cx in (x // 500 - 1, x // 500, x // 500 + 1) for cy in (y // 500 - 1, y // 500, y // 500 + 1) for i in grid.get((cx, cy), [])]
        hit = next((i for i in cand if point_in(blds[i]["ring"], x, y)), None)
        if hit is None:
            continue
        pod = blds[hit]
        if pod["a"] <= PODIUM_M2:
            if t["height_m"] > pod["h"]:
                pod["h"], pod["hsrc"], pod["tname"] = t["height_m"], "published", t["name"]
                raised += 1
            continue
        r = pod["ring"]
        k = max(range(len(r)), key=lambda i: (r[(i + 1) % len(r)][0] - r[i][0]) ** 2 + (r[(i + 1) % len(r)][1] - r[i][1]) ** 2)
        ang = math.atan2(r[(k + 1) % len(r)][1] - r[k][1], r[(k + 1) % len(r)][0] - r[k][0])
        ux, uy, vx, vy = math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang)
        hl, hw = SLAB_M[0] * 5, SLAB_M[1] * 5            # half sizes in decimetres
        ring = ccw([(round(x + sx * hl * ux + sy * hw * vx), round(y + sx * hl * uy + sy * hw * vy))
                    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
        blds.append({"id": building_id(t["lat"], t["lon"], seen), "ring": ring, "h": t["height_m"], "a": SLAB_M[0] * SLAB_M[1],
                     "lat": t["lat"], "lon": t["lon"], "biz": [], "hsrc": "published-slab", "tname": t["name"]})
        slabs += 1
    raised += slabs

    # --- roads (OSM), with the model's vehicles per day from the nearest road node
    nodes = city["nodes"]
    ngrid = defaultdict(list)
    nxy = [xy(la, lo) for la, lo in zip(nodes["lat"], nodes["lon"])]
    for i, (x, y) in enumerate(nxy):
        ngrid[(x // 2000, y // 2000)].append(i)

    def nearest_node(x, y):
        best, bd = None, (300 * 10) ** 2
        for cx in (x // 2000 - 1, x // 2000, x // 2000 + 1):
            for cy in (y // 2000 - 1, y // 2000, y // 2000 + 1):
                for i in ngrid.get((cx, cy), []):
                    d = (nxy[i][0] - x) ** 2 + (nxy[i][1] - y) ** 2
                    if d < bd:
                        best, bd = i, d
        return best

    roads, way_geo = [], []
    for e in json.loads((DATA / "sources/osm_roads_overpass.json").read_text())["elements"]:
        t = e.get("tags", {})
        hw = t.get("highway")
        if hw not in ROAD_CLASS or "geometry" not in e:
            continue
        pts = [xy(p["lat"], p["lon"]) for p in e["geometry"]]
        try:
            lanes = int(str(t.get("lanes", "")).split(";")[0])
        except ValueError:
            lanes = DEFAULT_LANES.get(hw.replace("_link", ""), 1 if hw.endswith("_link") else 2)
        width = max(5.0, lanes * 3.5 + (1.0 if hw in ("residential", "unclassified") else 0.0))
        mid = pts[len(pts) // 2]
        n = nearest_node(*mid)
        way_geo.append((e.get("id"), [(p["lat"], p["lon"]) for p in e["geometry"]], hw, "lanes" in t))
        roads.append({"c": ROAD_CLASS[hw], "w": round(width, 1), "n": t.get("name") or t.get("ref") or "",
                      "o": 1 if t.get("oneway") in ("yes", "1") or hw.startswith("motorway") else 0,
                      "br": 1 if t.get("bridge") not in (None, "no") else 0,
                      "v": round(nodes["passing"][n]) if n is not None and nodes.get("passing") else 0,
                      "p": delta(pts)})

    # each road's legal limit (City, government data) and real clear-road speed (TomTom, measured), and the City's
    # lane count where OpenStreetMap has none (road width)
    for r, (lim, real, lanes, src), (_, _, hw, has_lanes) in zip(roads, road_speeds(way_geo), way_geo):
        if lim:
            r["lim"] = lim
        if real:
            r["rs"] = real
            if src == "route":
                r["rsr"] = 1                       # from TomTom route times: block to block, with turns and stops
        if lanes and not has_lanes and not hw.endswith("_link"):
            r["w"] = round(max(5.0, lanes * 3.5 + (1.0 if hw in ("residential", "unclassified") else 0.0)), 1)

    # --- sidewalks (City), with the model's walkers per day
    walks = []
    sw = json.loads((DATA / "city_open_data/Niagara_Falls_Sidewalks_WGS_1984.geojson").read_text())
    for f in sw["features"]:
        g = f["geometry"]
        if not g:
            continue
        lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for line in lines:
            pts = [xy(la, lo) for lo, la in line]
            if len(pts) < 2:
                continue
            n = nearest_node(*pts[len(pts) // 2])
            walks.append({"wk": round(nodes["walkers"][n]) if n is not None and nodes.get("walkers") else 0, "p": delta(pts)})

    # --- water and land in the river (City rivers layer: Theme Water or Land for islands), creeks, parks
    def polys(path, keep=lambda p: True):
        out = []
        for f in json.loads(path.read_text())["features"]:
            g = f["geometry"]
            if not g or not keep(f["properties"]):
                continue
            for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
                rings = [delta(simplify([xy(la, lo) for lo, la in ring], 10)) for ring in poly]
                out.append({"r": rings, "n": f["properties"].get("DESCRIP") or f["properties"].get("Name") or "",
                            "land": 1 if f["properties"].get("Theme") == "Land" else 0})
        return out

    W = DATA / "city_open_data/world"
    water = polys(W / "rivers.geojson")
    parks = polys(W / "parks.geojson")
    creeks = []
    for f in json.loads((W / "creeks.geojson").read_text())["features"]:
        g = f["geometry"]
        if not g:
            continue
        for line in ([g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]):
            creeks.append(delta(simplify([xy(la, lo) for lo, la in line], 10)))

    # --- trees (City inventory): position, height class, conifer or broadleaf
    trees = []
    for f in json.loads((W / "trees.geojson").read_text())["features"]:
        p = f["properties"]
        if p.get("Lat") is None:
            continue
        x, y = xy(p["Lat"], p["Lon"])
        hr = str(p.get("HeightRange") or "05-10 m")
        try:
            lo_, hi_ = [int(s) for s in hr.replace(" m", "").replace("+", "").split("-")]
            h = (lo_ + hi_) / 2
        except ValueError:
            h = 7.0
        sp = str(p.get("TreeSpecies") or "").lower()
        trees += [x, y, round(max(3, h)), 1 if any(c in sp for c in CONIFER) else 0]

    # --- parking lots (estimated): in front of each plaza or store, paved from the shopfronts to the road.
    # The City's parking layer has only its own 28 lots; this fills the private lots the owner's photos show.
    lots = []
    # stores face the main road they sit on (arterials and collectors), so pave toward the nearest one
    main_segs = defaultdict(list)
    for r in roads:
        if r["c"] > 3:
            continue
        pts, x, y = [], 0, 0
        for i in range(0, len(r["p"]), 2):
            x += r["p"][i]; y += r["p"][i + 1]; pts.append((x / 10, y / 10))
        for pa, pb in zip(pts, pts[1:]):
            main_segs[(int(pa[0] // 100), int(pa[1] // 100))].append((pa, pb))

    def nearest_main_road(x, y, max_d=250.0):
        best, bd = None, max_d
        for gx in range(int(x // 100) - 3, int(x // 100) + 4):
            for gy in range(int(y // 100) - 3, int(y // 100) + 4):
                for (ax, ay), (bx, by) in main_segs.get((gx, gy), []):
                    ex, ey = bx - ax, by - ay
                    L2 = ex * ex + ey * ey
                    t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((x - ax) * ex + (y - ay) * ey) / L2))
                    qx, qy = ax + ex * t, ay + ey * t
                    d = math.hypot(qx - x, qy - y)
                    if d < bd:
                        best, bd = (qx, qy), d
        return best

    for bl in blds:
        if not bl["biz"] or bl["a"] < 500 or bl["hsrc"] == "published-slab":
            continue
        ring = [(x / 10, y / 10) for x, y in bl["ring"]]
        best = None
        for i in range(len(ring)):
            (ax, ay), (bx, by) = ring[i], ring[(i + 1) % len(ring)]
            L = math.hypot(bx - ax, by - ay)
            if L < 1:
                continue
            ux, uy = (bx - ax) / L, (by - ay) / L
            s = [p[0] * ux + p[1] * uy for p in ring]; t = [p[0] * -uy + p[1] * ux for p in ring]
            area = (max(s) - min(s)) * (max(t) - min(t))
            if best is None or area < best[0]:
                best = (area, ux, uy, min(s), max(s), min(t), max(t))
        _, ux, uy, s0, s1, t0, t1 = best
        vx, vy = -uy, ux
        cx, cy = ux * (s0 + s1) / 2 + vx * (t0 + t1) / 2, uy * (s0 + s1) / 2 + vy * (t0 + t1) / 2
        hit = nearest_main_road(cx, cy)
        if hit is None:
            n = nearest_node(round(cx * 10), round(cy * 10))
            if n is None:
                continue
            hit = (nxy[n][0] / 10, nxy[n][1] / 10)
        rx, ry = hit
        dx, dy = rx - cx, ry - cy
        # the side of the rectangle facing the road
        sides = [(ux, uy, (s1 - s0) / 2, (t1 - t0) / 2), (-ux, -uy, (s1 - s0) / 2, (t1 - t0) / 2), (vx, vy, (t1 - t0) / 2, (s1 - s0) / 2), (-vx, -vy, (t1 - t0) / 2, (s1 - s0) / 2)]
        fx, fy, half_d, half_w = max(sides, key=lambda q: q[0] * dx + q[1] * dy)
        dist = dx * fx + dy * fy - half_d - 8.0             # stop short of the road and its sidewalk
        depth = max(10.0, min(110.0, dist))
        if dist < 6:
            continue
        px, py = -fy, fx
        w = half_w + 6.0
        base_x, base_y = cx + fx * (half_d + 1.0), cy + fy * (half_d + 1.0)
        quad = [(base_x - px * w, base_y - py * w), (base_x + px * w, base_y + py * w),
                (base_x + px * w + fx * depth, base_y + py * w + fy * depth), (base_x - px * w + fx * depth, base_y - py * w + fy * depth)]
        lots.append({"r": delta([(round(x * 10), round(y * 10)) for x, y in quad]), "a": round(math.atan2(fy, fx), 3)})

    # --- businesses the game can show
    biz = []
    for k in range(nb):
        if b["external"][k]:
            continue
        rev, trev = sales.get(k, (0.0, 0.0))
        biz.append({"i": k, "n": b["name"][k], "a": b["address"][k], "k": kinds[b["sim_category"][k]],
                    "s": round(rev), "t": round(trev / rev, 2) if rev > 0 else 0, "st": b["staff"][k],
                    "o": b["open"][k], "c": b["close"][k], "x": xy(b["lat"][k], b["lon"][k])})
    pos = {r["i"]: j for j, r in enumerate(biz)}

    # property parcels: the address and lot size of the lot each building stands on
    from nfprep.businesses import nice_case
    for bl, pc in zip(blds, parcels_for(blds)):
        if pc:
            bl["parcel"] = {"a": nice_case(pc[0]), "m2": round(pc[1] or 0), "p": pc[2]}
    on_lot = sum(1 for bl in blds if bl.get("parcel"))
    from nfprep.lots import parcel_facts
    where = {}
    for bl in blds:
        if bl.get("parcel") and bl["parcel"]["p"] not in where:
            where[bl["parcel"]["p"]] = (bl["lon"], bl["lat"])
    facts = parcel_facts(where)
    world = {
        "_meta": {
            "what": "Niagara Falls in 3D for the walk-around game: decimetres east/north of the origin, each line or ring "
                    "delta-encoded (first point absolute).",
            "origin": {"lat": LAT0, "lon": LON0},
            "sources": [
                "City of Niagara Falls open data (government): building footprints 2018, sidewalks, rivers, creeks, parks, tree inventory, property parcels (pa address, pm lot m², pp parcel id) and, per parcel id under parcels, zoning, Official Plan, heritage, condominiums, City property, building permits 2015-2023, planning applications",
                "OpenStreetMap contributors (ODbL): roads",
                "The simulation: businesses, yearly sales, tourist share, vehicles and walkers per day",
                "Estimated: parking lots in front of stores and plazas, from the outline and the nearest road",
            ],
            "heights": f"City 2018 heights; {raised} towers set to their published heights (Wikipedia list of tallest "
                       f"buildings, citing The Skyscraper Center), marked hs='published', or added as a {SLAB_M[0]:.0f} x {SLAB_M[1]:.0f} m slab "
                       f"on a large podium (hs='published-slab', approximate outline)",
        },
        "buildings": [{"id": bl["id"], "h": round(bl["h"], 1), "r": delta(bl["ring"]),
                       **({"b": [pos[k] for k in bl["biz"] if k in pos]} if bl["biz"] else {}),
                       **({"hs": bl["hsrc"]} if bl["hsrc"] != "city" else {}), **({"tn": bl["tname"]} if bl.get("tname") else {}),
                       **({"pa": bl["parcel"]["a"], "pm": bl["parcel"]["m2"], "pp": bl["parcel"]["p"]} if bl.get("parcel") else {})} for bl in blds],
        "parcels": {str(k): v for k, v in facts.items()},
        "roads": roads, "walks": walks, "lots": lots, "water": water, "parks": parks, "creeks": creeks, "trees": trees, "biz": biz,
        "landmarks": [{"kind": "tower", "name": t["name"], "x": xy(t["lat"], t["lon"]), "h": t["height_m"],
                       "style": "carillon" if t["name"] == "Rainbow Tower" else "pod"}
                      for t in towers if t["name"] in TOWER_LANDMARKS],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    old = OUT / "world.json"                     # keep the ground from nfprep.terrain, which is slow to rebuild
    if old.exists():
        prev = json.loads(old.read_text())
        if "terrain" in prev:
            world["terrain"] = prev["terrain"]
            world["_meta"]["sources"] += [n for n in prev["_meta"]["sources"] if n not in world["_meta"]["sources"]
                                          and not n.startswith("City of Niagara Falls open data")]
    (OUT / "world.json").write_text(json.dumps(world, separators=(",", ":")))
    size = (OUT / "world.json").stat().st_size
    print(f"world.json {size / 1e6:.1f} MB: {len(blds)} buildings ({raised} towers at published height), {len(roads)} roads, "
          f"{len(walks)} sidewalks, {len(lots)} parking lots (estimated), {len(water)} water/land, {len(parks)} parks, {len(trees) // 4} trees, "
          f"{len(biz)} businesses ({len(unplaced)} without a building); {on_lot} buildings on a property parcel, {len(facts)} lots with City facts")


if __name__ == "__main__":
    main()
