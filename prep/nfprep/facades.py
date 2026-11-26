"""Bake wall photos and building models into the 3D city.

Reads data/building_specs.json (3D models described from the owner's photos), data/building_photos.json
(photos in data/photos/) and data/building_models.json (models in data/models/),
straightens each photo onto its wall with a perspective transform, and writes web/world/tex/*.jpg,
web/world/models/*.glb and web/world/appearance.json, which the game loads.

Only photos the owner took or openly licensed ones (Mapillary, CC BY-SA 4.0) are accepted. Google Street View
images are refused: Google's terms forbid making textures or 3D models from them.

Run from prep/: python3 -m nfprep.facades
"""
import json
import math
import shutil
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
WORLD = ROOT / "web" / "world"
ALLOWED = {"owner", "mapillary"}
REFUSED = {"google_street_view": "Google's Maps Platform terms forbid making textures or 3D models from Street View images",
           "google": "Google's Maps Platform terms forbid making textures or 3D models from Google imagery"}
PX_PER_M = 64
MAX_PX = 1024


def undelta(d):
    out, x, y = [], 0, 0
    for i in range(0, len(d), 2):
        x += d[i]; y += d[i + 1]
        out.append((x / 10, y / 10))       # metres east, north
    return out


def homography(q):
    """Unit square (u right, v down) -> photo pixels, for corners TL, TR, BR, BL."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = q
    dx1, dx2, dx3 = x1 - x2, x3 - x2, x0 - x1 + x2 - x3
    dy1, dy2, dy3 = y1 - y2, y3 - y2, y0 - y1 + y2 - y3
    g = h = 0.0
    if abs(dx3) > 1e-9 or abs(dy3) > 1e-9:
        den = dx1 * dy2 - dx2 * dy1
        g, h = (dx3 * dy2 - dx2 * dy3) / den, (dx1 * dy3 - dx3 * dy1) / den
    return (x1 - x0 + g * x1, x3 - x0 + h * x3, x0, y1 - y0 + g * y1, y3 - y0 + h * y3, y0, g, h)


def find_wall(buildings, entry, origin):
    """The wall the entry names, or the wall nearest its recorded middle if the outlines changed."""
    b = buildings.get(entry["building"])
    k = entry.get("wall")
    if b is not None and k is not None and k < len(b) and "wall_mid" not in entry:
        return entry["building"], k
    if "wall_mid" not in entry:
        return (entry["building"], k) if b is not None else (None, None)
    kx = 111320.0 * math.cos(math.radians(origin["lat"]))
    mx, my = (entry["wall_mid"][1] - origin["lon"]) * kx, (entry["wall_mid"][0] - origin["lat"]) * 110540.0
    best, bd = (None, None), 3.0
    cands = [entry["building"]] if b is not None else list(buildings)
    for bid in cands:
        ring = buildings[bid]
        if abs(ring[0][0] - mx) > 400 or abs(ring[0][1] - my) > 400:
            continue
        for i in range(len(ring)):
            (ax, ay), (bx, by) = ring[i], ring[(i + 1) % len(ring)]
            d = math.hypot((ax + bx) / 2 - mx, (ay + by) / 2 - my)
            if d < bd:
                best, bd = (bid, i), d
    if best[0] is None and b is not None and k is not None and k < len(b):
        return entry["building"], k
    return best


def main():
    world = json.loads((WORLD / "world.json").read_text())
    origin = world["_meta"]["origin"]
    buildings = {b["id"]: undelta(b["r"]) for b in world["buildings"]}
    heights = {b["id"]: b["h"] for b in world["buildings"]}
    photos = json.loads((DATA / "building_photos.json").read_text())["photos"]
    models = json.loads((DATA / "building_models.json").read_text())["models"]
    tex_dir, model_dir = WORLD / "tex", WORLD / "models"
    shutil.rmtree(tex_dir, ignore_errors=True); shutil.rmtree(model_dir, ignore_errors=True)
    out, credits, skipped = {}, [], []
    for e in photos:
        src = e.get("source", "")
        if src in REFUSED or src not in ALLOWED:
            skipped.append(f"{e.get('file')}: source '{src}' not allowed ({REFUSED.get(src, 'use owner or mapillary')})")
            continue
        bid, k = find_wall(buildings, e, origin)
        if bid is None:
            skipped.append(f"{e.get('file')}: wall not found")
            continue
        ring = buildings[bid]
        (ax, ay), (bx, by) = ring[k], ring[(k + 1) % len(ring)]
        wall_w = math.hypot(bx - ax, by - ay)
        v0, v1 = float(e.get("bottom_m", 0)), min(heights[bid], float(e.get("top_m") or heights[bid]))
        wall_h = max(0.5, v1 - v0)
        img = ImageOps.exif_transpose(Image.open(DATA / "photos" / e["file"])).convert("RGB")
        q = [(u * img.width, v * img.height) for u, v in e["corners"]]
        ppm = min(PX_PER_M, MAX_PX / max(wall_w, wall_h))
        W, H = max(16, round(wall_w * ppm)), max(16, round(wall_h * ppm))
        a, b_, c, d, e_, f, g, h = homography(q)
        coeffs = (a / W, b_ / H, c, d / W, e_ / H, f, g / W, h / H)
        tex = img.transform((W, H), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)
        tex_dir.mkdir(parents=True, exist_ok=True)
        name = f"{bid}_w{k}.jpg"
        tex.save(tex_dir / name, "JPEG", quality=85)          # a fresh file: no GPS or camera details
        out.setdefault(bid, {"walls": {}})["walls"][str(k)] = {"tex": f"tex/{name}", "bottom_m": v0, "top_m": v1}
        if src == "mapillary":
            credits.append({"building": bid, "wall": k, "credit": e.get("credit", ""), "image_id": e.get("image_id", ""), "licence": "CC BY-SA 4.0"})
    for m in models:
        if m["building"] not in buildings:
            skipped.append(f"{m.get('file')}: building {m['building']} not found")
            continue
        model_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(DATA / "models" / m["file"], model_dir / m["file"])
        out.setdefault(m["building"], {"walls": {}})["model"] = {"src": f"models/{m['file']}", "scale": m.get("scale", 1),
                                                                 "rotate_deg": m.get("rotate_deg", 0), "offset": m.get("offset_m", [0, 0, 0])}
    for sp in json.loads((DATA / "building_specs.json").read_text())["buildings"]:
        if sp["building"] not in buildings:
            skipped.append(f"spec {sp.get('name')}: building {sp['building']} not found")
            continue
        out.setdefault(sp["building"], {"walls": {}})["spec"] = {k: v for k, v in sp.items() if k not in ("building", "from")}
    doc = {"_meta": {"what": "Wall photos and models for the 3D city (built by prep/nfprep/facades.py)", "credits": credits}, "buildings": out}
    (WORLD / "appearance.json").write_text(json.dumps(doc, indent=1))
    print(f"appearance.json: {sum('spec' in v for v in out.values())} buildings from specs, {sum(len(v['walls']) for v in out.values())} photo walls, {sum('model' in v for v in out.values())} models")
    for s in skipped:
        print("skipped:", s)


if __name__ == "__main__":
    main()
