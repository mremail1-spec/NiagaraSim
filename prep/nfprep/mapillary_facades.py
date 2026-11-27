"""Facade photos for the 3D city from Mapillary 360-degree street photos (Mapillary contributors, CC BY-SA 4.0).

For a wall of a building, every 360 photo taken 8-45 m in front of it is a candidate. Mapillary gives each photo
its computed position and orientation (structure from motion). A world point X maps into the camera as
p = R (X - C), R from the photo's rotation vector, C its position in local metres east/north/up; on the 360
(equirectangular) image, longitude atan2(p.x, p.z) and latitude atan2(-p.y, |p.xz|) give the pixel. The wall is
sampled on a grid (metres along it, metres up from the ground, the ground taken 2.2 m below the camera, the height
of a camera on a car roof) and written as a straight-on picture of the wall: data/photos/mapillary/<building>_w<k>.jpg,
listed in data/building_photos.json for nfprep.facades, which puts it on the wall in the game with the credit.

The best photo for a wall: square on to it, close (about 15-25 m), recent, not in winter. Walls longer than the
photo sees well are stitched from up to three photos, each column taken from the photo that sees it most squarely.
Things between the camera and the wall (cars, poles, trees) end up painted on the wall: check each result by eye.

Run from prep/: python3 -m nfprep.mapillary_facades find <bbox>     (walls with 360 photos in front)
               python3 -m nfprep.mapillary_facades <building id>:<wall> [...]
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "build" / "mapillary"
PHOTOS = ROOT / "data" / "photos"
LIST = ROOT / "data" / "building_photos.json"
UA = {"User-Agent": "nfsim-prep/0.1"}
CAM_H = 2.2           # metres: camera above the ground (car roof)
PX_PER_M = 40
MAX_OUT = 45          # metres in front of the wall a photo may be (80 for walls set back behind parking)


def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120))


def _bytes(url):
    for a in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180).read()
        except Exception:
            if a == 2:
                raise
            time.sleep(3)


def frame():
    from nfprep.world3d import LAT0, LON0
    kx = 111320.0 * math.cos(math.radians(LAT0))
    return (lambda lat, lon: ((lon - LON0) * kx, (lat - LAT0) * 110540.0)), kx


def wall_of(bid: str, k: int):
    W = json.loads((ROOT / "web" / "world" / "world.json").read_text())
    b = next(x for x in W["buildings"] if x["id"] == bid)
    r, pts, X, Y = b["r"], [], 0, 0
    for i in range(0, len(r), 2):
        X += r[i]; Y += r[i + 1]
        pts.append((X / 10, Y / 10))
    A, B = np.array(pts[k]), np.array(pts[(k + 1) % len(pts)])
    specs = {s["building"]: s for s in json.loads((ROOT / "data" / "building_specs.json").read_text())["buildings"]}
    h = specs.get(bid, {}).get("eave_m") or b["h"]
    return A, B, float(h), b


def candidates(A, B, n_out):
    """360 photos in front of the wall, with their computed pose."""
    to_xy, _ = frame()
    from nfprep.world3d import LAT0, LON0
    mid = (A + B) / 2
    L = np.linalg.norm(B - A)
    pad = (L / 2 + 45) / 110540.0
    lat_c, lon_c = LAT0 + mid[1] / 110540.0, LON0 + mid[0] / (111320.0 * math.cos(math.radians(LAT0)))
    padx = pad / math.cos(math.radians(LAT0))
    F = "id,is_pano,captured_at,computed_geometry,computed_rotation,computed_compass_angle,thumb_original_url,width,height"
    d = _get(f"https://graph.mapillary.com/images?fields={F}&bbox={lon_c - padx},{lat_c - pad},{lon_c + padx},{lat_c + pad}&limit=2000")["data"]
    out = []
    u = (B - A) / L
    for p in d:
        if not p.get("is_pano") or not p.get("computed_geometry") or not p.get("computed_rotation"):
            continue
        lon, lat = p["computed_geometry"]["coordinates"]
        C = np.array(to_xy(lat, lon))
        rel = C - A
        along, out_d = float(rel @ u), float(rel @ n_out)
        if not 8 <= out_d <= MAX_OUT or not -15 <= along <= L + 15:
            continue
        month = int(time.strftime("%m", time.gmtime(p["captured_at"] / 1000)))
        score = abs(out_d - min(20, MAX_OUT)) / 10 + abs(along - L / 2) / max(L, 1) + (1.0 if month in (12, 1, 2, 3) else 0) \
            - (p["captured_at"] / 1000 - 1.6e9) / (3.15e7 * 4)           # newer is better
        out.append((score, C, p))
    out.sort(key=lambda t: t[0])
    return out


def render(A, B, h, cams, ppm=PX_PER_M):
    """The wall as seen straight on: columns along A->B, rows from the top (h) down to the ground."""
    from scipy.ndimage import map_coordinates
    from scipy.spatial.transform import Rotation
    from PIL import Image
    L = float(np.linalg.norm(B - A))
    u = (B - A) / L
    Wp, Hp = max(16, int(L * ppm)), max(16, int(h * ppm))
    s = (np.arange(Wp) + 0.5) / ppm
    z = h - (np.arange(Hp) + 0.5) / ppm
    S, Z = np.meshgrid(s, z)
    X = A[0] + u[0] * S
    Y = A[1] + u[1] * S
    best = np.full(S.shape, -np.inf)
    out = np.zeros(S.shape + (3,), dtype=np.float32)
    used = []
    for C, p, img in cams:
        R = Rotation.from_rotvec(p["computed_rotation"]).as_matrix()
        d = np.stack([X - C[0], Y - C[1], Z - CAM_H], axis=-1)          # ground under the camera at 0
        q = d @ R.T
        lon = np.arctan2(q[..., 0], q[..., 2])
        lat = np.arctan2(-q[..., 1], np.hypot(q[..., 0], q[..., 2]))
        H_, W_ = img.shape[:2]
        px = (0.5 + lon / (2 * np.pi)) * W_
        py = (0.5 - lat / np.pi) * H_
        # how squarely and how close this photo sees each point of the wall
        dist = np.linalg.norm(d, axis=-1)
        horiz = np.stack([X - C[0], Y - C[1]], axis=-1)
        cosv = np.abs(horiz @ np.array([-u[1], u[0]])) / np.maximum(np.linalg.norm(horiz, axis=-1), 1e-6)
        quality = cosv - dist / 60
        take = quality > best
        if take.any():
            for ch in range(3):
                samp = map_coordinates(img[..., ch].astype(np.float32), [py[take], px[take] % W_], order=1, mode="nearest")
                out[..., ch][take] = samp
            best[take] = quality[take]
            used.append(p["id"])
    im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    return im, used


def build(bid: str, k: int, max_photos: int = 3) -> dict | None:
    from PIL import Image
    A, B, h, b = wall_of(bid, k)
    L = float(np.linalg.norm(B - A))
    u = (B - A) / L
    n_out = np.array([u[1], -u[0]])                                   # outside of a counter-clockwise ring
    cands = candidates(A, B, n_out)
    if not cands:
        print(f"{bid} wall {k}: no 360 photo in front of it")
        return None
    # spread the photos along long walls
    chosen = [cands[0]]
    if L > 25:
        for sc, C, p in cands[1:]:
            if len(chosen) >= max_photos:
                break
            if all(np.linalg.norm(C - c[1]) > L / (max_photos + 0.5) for c in chosen):
                chosen.append((sc, C, p))
    cams = []
    for _, C, p in chosen:
        f = CACHE / "img" / f"{p['id']}.jpg"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(_bytes(p["thumb_original_url"]))
        cams.append((C, p, np.asarray(Image.open(f).convert("RGB"))))
    im, used = render(A, B, h, cams)
    out = PHOTOS / "mapillary" / f"{bid}_w{k}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=85)
    dates = sorted(time.strftime("%Y-%m-%d", time.gmtime(p["captured_at"] / 1000)) for _, _, p in chosen if p["id"] in used)
    print(f"{bid} wall {k}: {L:.1f} x {h:.1f} m from {len(used)} photo(s) {used} ({', '.join(dates)}) -> {out.relative_to(ROOT)}")
    return {"file": f"mapillary/{bid}_w{k}.jpg", "source": "mapillary", "building": bid, "wall": k,
            "corners": [[0, 0], [1, 0], [1, 1], [0, 1]], "bottom_m": 0, "top_m": h,
            "image_id": ",".join(used), "date": dates[-1] if dates else "",
            "credit": "Street photos: Mapillary contributors, CC BY-SA 4.0 (images " + ", ".join(used) + ")",
            "status": "to check by eye"}


def find_walls(bbox: str, min_len: float = 10, min_photos: int = 2, max_out: float = 45) -> list:
    """Walls in a bbox 'lon0,lat0,lon1,lat1' with 360 photos 8-45 m in front of them: (photos, building, wall,
    length, distance, names)."""
    to_xy, _ = frame()
    lon0, lat0, lon1, lat1 = map(float, bbox.split(","))
    P, a = [], lon0
    while a < lon1:
        b = min(lon1, a + 0.003)
        for p in _get(f"https://graph.mapillary.com/images?fields=id,is_pano,computed_geometry&bbox={a},{lat0},{b},{lat1}&limit=2000")["data"]:
            if p.get("is_pano") and p.get("computed_geometry"):
                P.append(to_xy(*p["computed_geometry"]["coordinates"][::-1]))
        a = b
    P = np.array(P)
    if not len(P):
        return []
    W = json.loads((ROOT / "web" / "world" / "world.json").read_text())
    rows = []
    for bd in W["buildings"]:
        r, pts, X, Y = bd["r"], [], 0, 0
        for i in range(0, len(r), 2):
            X += r[i]; Y += r[i + 1]
            pts.append((X / 10, Y / 10))
        pts = np.array(pts)
        c = pts.mean(0)
        if not (P[:, 0].min() - 50 < c[0] < P[:, 0].max() + 50 and P[:, 1].min() - 50 < c[1] < P[:, 1].max() + 50):
            continue
        for k in range(len(pts)):
            A, B = pts[k], pts[(k + 1) % len(pts)]
            L = float(np.linalg.norm(B - A))
            if L < min_len:
                continue
            u = (B - A) / L
            rel = P - A
            al, out = rel @ u, rel @ np.array([u[1], -u[0]])
            m = (out >= 8) & (out <= max_out) & (al >= 0) & (al <= L)
            if m.sum() >= min_photos:
                rows.append((int(m.sum()), bd["id"], k, round(L), round(float(np.median(out[m]))),
                             [W["biz"][i]["n"] for i in bd.get("b", [])][:3], bd.get("pa", "")))
    return sorted(rows, key=lambda r: -r[0])


if __name__ == "__main__":
    if sys.argv[1] == "find":
        for r in find_walls(sys.argv[2])[:40]:
            print(r)
        sys.exit()
    doc = json.loads(LIST.read_text())
    for arg in sys.argv[1:]:
        bid, k = arg.split(":")
        e = build(bid, int(k))
        if e:
            doc["photos"] = [x for x in doc["photos"] if not (x.get("building") == bid and x.get("wall") == int(k))] + [e]
    LIST.write_text(json.dumps(doc, indent=1))
