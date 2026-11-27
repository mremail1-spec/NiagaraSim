"""Street furniture for the 3D city from the City of Niagara Falls open data (government; Terms of Use of the City):
street light poles, the signs at stop-, all-way-stop- and yield-controlled intersections, City traffic signals,
hydrants and public art.

Everything is placed in the game's frame (decimetres east and north of the world origin, like world.json):
- lights: [x, y, height dm, arm angle (deg, game frame), material]: the arm reaches toward the nearest road.
  Pole heights in the City data are mostly in feet (25-50); values of 20 or more are read as feet.
- signs: [x, y, facing (deg, game frame), kind]: one sign on the right of each controlled approach, facing the
  traffic coming in. For a two-way stop the stop signs go on the smaller road (higher road class number, or the
  road not named first by the City); an all-way stop gets one on every approach.
- signals: [x, y, half width dm, [approach angles]] for the City's traffic lights.
- hydrants: [x, y]
- From Mapillary's detected objects (CC BY-SA 4.0), what the City lacks: poles, cans, benches, boxes, msigns
  (see mapillary_objects).
Outdoor public art goes to the index (few, seen from anywhere): {name, type (plaque, monument, cairn, sculpture), lat, lon, h};
the City's indoor pieces (paintings, prints, photographs) are left out.
"""
import json
import math
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "city_open_data" / "world"
MATERIAL = {"CONCRETE": 0, "WOOD": 1, "STEEL": 2, "ALUMINIUM": 2, "EXPOSED AGGREGATE BLACK": 3}
OUTDOOR_ART = {"Plaque": "plaque", "Monument": "monument", "Cairn": "cairn", "Sculpture": "sculpture"}
SIGN_KIND = {"Stop Sign": 0, "All -way stop": 1, "YIELD Sign": 2}


def _length_m(v):
    """'20 in', '5 ft', '1.2 m' or a number (metres) -> metres, else None."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) or None
    m = re.match(r"\s*([\d.]+)\s*(in|inch|inches|ft|feet|'|\"|m|cm)?", str(v).lower())
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2) or "m"
    return n * {"in": 0.0254, "inch": 0.0254, "inches": 0.0254, '"': 0.0254, "ft": 0.3048, "feet": 0.3048, "'": 0.3048, "cm": 0.01}.get(u, 1.0)


def _points(name):
    path = SRC / f"{name}.geojson"
    if not path.exists():
        return []
    out = []
    for f in json.loads(path.read_text())["features"]:
        g = f["geometry"]
        if g and g["type"] == "Point":
            out.append((g["coordinates"][0], g["coordinates"][1], f["properties"]))
    return out


def _undelta(d):
    out, x, y = [], 0, 0
    for i in range(0, len(d), 2):
        x += d[i]; y += d[i + 1]
        out.append((x, y))
    return out


class _Roads:
    """Nearest-road lookups over world.json roads (decimetres)."""

    def __init__(self, roads):
        self.roads = [(r, _undelta(r["p"])) for r in roads]
        self.grid = defaultdict(list)
        for ri, (_, pts) in enumerate(self.roads):
            for si, ((ax, ay), (bx, by)) in enumerate(zip(pts, pts[1:])):
                for gx in range(int(min(ax, bx) // 500), int(max(ax, bx) // 500) + 1):
                    for gy in range(int(min(ay, by) // 500), int(max(ay, by) // 500) + 1):
                        self.grid[(gx, gy)].append((ri, si))

    def near(self, x, y, r_dm):
        seen, out = set(), []
        for gx in range(int((x - r_dm) // 500), int((x + r_dm) // 500) + 1):
            for gy in range(int((y - r_dm) // 500), int((y + r_dm) // 500) + 1):
                for ri, si in self.grid.get((gx, gy), []):
                    if (ri, si) in seen:
                        continue
                    seen.add((ri, si))
                    (ax, ay), (bx, by) = self.roads[ri][1][si], self.roads[ri][1][si + 1]
                    L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1
                    t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / L2))
                    px, py = ax + t * (bx - ax), ay + t * (by - ay)
                    d = math.hypot(px - x, py - y)
                    if d <= r_dm:
                        out.append((d, ri, si, t, px, py))
        return sorted(out)

    def arms(self, x, y, r_dm=150):
        """The roads leaving an intersection: (angle in the game frame, road), one per direction."""
        arms = []
        for d, ri, si, t, px, py in self.near(x, y, r_dm):
            r, pts = self.roads[ri]
            for k, step in ((si + 1, 1), (si, -1)):       # forward from the segment's end, back from its start
                qx, qy = pts[k]
                acc = math.hypot(qx - px, qy - py)
                while acc < 60 and 0 <= k + step < len(pts):  # about 6 m out along the road
                    k += step
                    acc += math.hypot(pts[k][0] - qx, pts[k][1] - qy)
                    qx, qy = pts[k]
                if acc < 20:
                    continue
                ang = math.atan2(qx - x, -(qy - y))          # game frame: x east, z south; angle from +z
                if all(abs((ang - a + math.pi) % (2 * math.pi) - math.pi) > 0.45 for a, _ in arms):
                    arms.append((ang, r))
        return arms


def build(world, xy):
    """Returns {"lights": [...], "signs": [...], "signals": [...], "hydrants": [...]} (all in decimetres) and the art list."""
    R = _Roads(world["roads"])
    lights, signs, signals, hydrants = [], [], [], []

    for lon, lat, p in _points("street_lights"):
        x, y = xy(lat, lon)
        h = p.get("PoleHeight") or 0
        h = h * 0.3048 if h >= 20 else h
        h = min(16.0, max(5.0, h)) if h else 9.0
        hit = R.near(x, y, 400)
        if hit:
            _, _, _, _, px, py = hit[0]
            ang = math.degrees(math.atan2(py - y, px - x))   # arm (+x of the model) toward the road: atan2(-dz, dx), dz = -(py - y)
        else:
            ang = 0.0
        lights.append([x, y, round(h * 10), round(ang), MATERIAL.get((p.get("Material") or "").upper(), 0)])

    for lon, lat, p in _points("intersections"):
        kind = p.get("ControlType")
        if kind not in SIGN_KIND and kind != "Traffic Light":
            continue
        x, y = xy(lat, lon)
        arms = R.arms(x, y)
        if len(arms) < 2:
            continue
        if kind == "Traffic Light":
            w = max(r["w"] for _, r in arms)
            signals.append([x, y, round(w * 5), [round(math.degrees(a)) for a, _ in arms]])
            continue
        if kind == "All -way stop":
            controlled = arms
        else:
            worst = max(r["c"] for _, r in arms)
            controlled = [(a, r) for a, r in arms if r["c"] == worst]
            if len(controlled) == len(arms):              # same class: the road the City names first is the through road
                first = (p.get("Street1") or "").strip().lower()
                named = [(a, r) for a, r in arms if first and first.split()[0] in (r.get("n") or "").lower()]
                if named and len(named) < len(arms):
                    controlled = [(a, r) for a, r in arms if (a, r) not in named]
        for a, r in controlled:
            ux, uz = math.sin(a), math.cos(a)             # out along the arm (game frame)
            back = r["w"] / 2 + 3.0
            rx, rz = uz, -ux                              # right of a driver coming in (heading -u: right of (hx, hz) is (-hz, hx))
            side = r["w"] / 2 + 1.2
            gx, gz = ux * back + rx * side, uz * back + rz * side
            signs.append([round(x + gx * 10), round(y - gz * 10), round(math.degrees(a)), SIGN_KIND[kind]])

    for lon, lat, p in _points("hydrants"):
        x, y = xy(lat, lon)
        hydrants.append([x, y])

    art = []
    for lon, lat, p in _points("public_art"):
        name = (p.get("Name") or "").strip()
        sub = (p.get("SubType") or "").strip()
        if not name or sub not in OUTDOOR_ART:                 # paintings, prints and photos hang indoors
            continue
        h = _length_m(p.get("Height"))
        art.append({"name": name, "type": OUTDOOR_ART[sub],
                    "material": (p.get("Material") or "").strip(), "lat": round(lat, 6), "lon": round(lon, 6),
                    "h": round(min(8.0, max(0.8, h)), 2) if h else None})
    furn = {"lights": lights, "signs": signs, "signals": signals, "hydrants": hydrants}
    furn.update(mapillary_objects(R, xy, lights))
    return furn, art


# Mapillary's detected objects (map features, Mapillary contributors, CC BY-SA 4.0): only what the City's data
# lacks. Street lights, hydrants, traffic lights and stop signs come from the City.
MLY_FILE = ROOT / "build" / "mapillary" / "features_city.json"
MLY_SIGN = {"regulatory": 0, "warning": 1, "information": 2, "complementary": 3}


def mapillary_objects(R: "_Roads", xy, lights) -> dict:
    """utility poles [x, y], trash cans [x, y], benches and junction boxes [x, y, facing deg], traffic signs
    [x, y, facing deg, kind 0 regulatory / 1 warning / 2 information / 3 chevron]. Each is pushed off the roadway
    onto the curb when its detected position falls in the road; benches, boxes and signs face the nearest road,
    signs the traffic coming on their side."""
    if not MLY_FILE.exists():
        return {}
    feats = json.loads(MLY_FILE.read_text())
    wood = [(x, y) for x, y, _h, _a, mat in lights if mat == 1]
    grid = defaultdict(list)
    for x, y in wood:
        grid[(int(x // 100), int(y // 100))].append((x, y))

    def near_wood(x, y):
        return any(math.hypot(x - a, y - b) < 30 for gx in (-1, 0, 1) for gy in (-1, 0, 1)
                   for a, b in grid.get((int(x // 100) + gx, int(y // 100) + gy), []))

    out = {"poles": [], "cans": [], "benches": [], "boxes": [], "msigns": []}
    for f in feats:
        v = f.get("object_value", "")
        lon, lat = f["geometry"]["coordinates"]
        x, y = xy(lat, lon)
        hit = R.near(x, y, 250)
        ang = 0
        if hit:
            d, ri, si, t, px, py = hit[0]
            r, pts = R.roads[ri]
            half = r["w"] * 5 + 10                       # half width + 1 m, in decimetres
            if d < half:                                 # detected in the roadway: onto the curb
                ox, oy = (x - px, y - py) if d > 1 else (0.0, 10.0)
                L = math.hypot(ox, oy) or 1
                x, y = round(px + ox / L * half), round(py + oy / L * half)
            # facing the road (game frame: x east, z south; angle from +z)
            ang = round(math.degrees(math.atan2(px - x, -(py - y))))
            if v.startswith(tuple(MLY_SIGN)):            # a sign faces the traffic that has it on its right
                (ax, ay), (bx, by) = pts[si], pts[si + 1]
                ux, uz = (bx - ax), -(by - ay)
                L = math.hypot(ux, uz) or 1
                ux, uz = ux / L, uz / L
                sx, sz = (x - px), -(y - py)
                h = (ux, uz) if (-uz * sx + ux * sz) > 0 else (-ux, -uz)    # heading with the sign on its right
                ang = round(math.degrees(math.atan2(-h[0], -h[1])))           # face the oncoming driver
        if v in ("object--support--utility-pole", "object--support--pole"):
            if not near_wood(x, y):
                out["poles"].append([x, y])
        elif v == "object--trash-can":
            out["cans"].append([x, y])
        elif v == "object--bench":
            out["benches"].append([x, y, ang])
        elif v == "object--junction-box":
            out["boxes"].append([x, y, ang])
        elif v.startswith(tuple(MLY_SIGN)) and not v.startswith("regulatory--stop"):
            out["msigns"].append([x, y, ang, MLY_SIGN[v.split("--")[0]]])
    # one object is often detected several times a few metres apart: merge (poles within 12 m, the rest 3 m)
    for kind, r in (("poles", 120), ("cans", 30), ("benches", 30), ("boxes", 30), ("msigns", 30)):
        out[kind] = _merge(out[kind], r)
    return out


def _merge(items: list, r_dm: float) -> list:
    """Greedy clustering: each item joins the first kept item within r_dm (same kind for signs), which moves to the
    cluster's mean position."""
    kept, grid = [], defaultdict(list)
    for it in items:
        gx, gy = int(it[0] // r_dm), int(it[1] // r_dm)
        hit = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), []):
                    k = kept[j]
                    if math.hypot(k[0][0] - it[0], k[0][1] - it[1]) < r_dm and (len(it) < 4 or k[0][3] == it[3]):
                        hit = j
                        break
                if hit is not None:
                    break
            if hit is not None:
                break
        if hit is None:
            grid[(gx, gy)].append(len(kept))
            kept.append([list(it), 1])
        else:
            k = kept[hit]
            n = k[1]
            k[0][0] = round((k[0][0] * n + it[0]) / (n + 1)); k[0][1] = round((k[0][1] * n + it[1]) / (n + 1))
            k[1] = n + 1
    return [k[0] for k in kept]
