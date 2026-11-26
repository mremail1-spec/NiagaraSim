"""Cut the 3D city into tiles the game streams in as you move, like an open-world game.

The simulation keeps the whole city; the game only downloads what is around you. This reads the full world
(web/world/world.json, terrain.png, terrain_fine.png) and writes:

- web/world/tiles/<ix>_<iz>.json: one 1.6 km tile: its buildings, businesses (with the model's sales), roads,
  sidewalks, creeks, trees and parking lots, the City's facts on its buildings' lots (parcels, see nfprep.lots), plus its ground heights at 10 m and the 2.5 m cliff detail, as PNGs
  inside the file. Tiles are squares in game coordinates (x east, z south, metres from the origin).
- web/world/index.json: what is seen from far away and needed before any tile: the origin, the tile list, the
  whole ground at 40 m, the skyline (towers 22 m and up), landmarks, the places where mist rises at the Falls,
  the City's public art.
- web/world/citymap.png: the city map (roads and water) for the in-game map.

Run from prep/ after nfprep.world3d and nfprep.terrain: python3 -m nfprep.world_tiles
"""
import base64
import io
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "web" / "world"
TILE = 1600.0          # metres; a multiple of the game's 200 m chunk
CELL = 10.0
FAR_CELL = 40.0
TALL = 22.0


def png_data_url(packed: np.ndarray) -> str:
    rgb = np.zeros(packed.shape + (3,), dtype="uint8")
    rgb[..., 0] = packed & 0xFF
    rgb[..., 1] = packed >> 8
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def read_packed(path: Path) -> np.ndarray:
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.uint16)
    return a[..., 0] | (a[..., 1] << 8)


def undelta(d):
    out, x, y = [], 0, 0
    for i in range(0, len(d), 2):
        x += d[i]; y += d[i + 1]
        out.append((x, y))
    return out


def tile_of(x_m, z_m):
    return int(math.floor(x_m / TILE)), int(math.floor(z_m / TILE))


def main():
    W = json.loads((WORLD / "world.json").read_text())
    T = W["terrain"]
    grid = read_packed(WORLD / T["file"])                    # rows: north from y0, cols: east from x0
    ny, nx = grid.shape

    def sample(xs, zs):
        """The 10 m grid at game points (x east, z south), nearest cell."""
        gj = np.clip(np.round((xs - T["x0"]) / CELL).astype(int), 0, nx - 1)
        gi = np.clip(np.round((-zs - T["y0"]) / CELL).astype(int), 0, ny - 1)
        return grid[gi, gj]

    # tile extent: the ground grid's coverage
    xmin, xmax = T["x0"], T["x0"] + (nx - 1) * CELL
    zmin, zmax = -(T["y0"] + (ny - 1) * CELL), -T["y0"]
    ix0, iz0 = tile_of(xmin, zmin)
    ix1, iz1 = tile_of(xmax, zmax)
    tiles = defaultdict(lambda: {"buildings": [], "biz": [], "roads": [], "walks": [], "creeks": [], "trees": [], "lots": [], "parcels": {}})
    facts = W.get("parcels", {})

    # businesses go with their building; remember each one's new place
    biz = W["biz"]
    biz_home = {}
    for b in W["buildings"]:
        pts = undelta(b["r"])
        cx = sum(p[0] for p in pts) / len(pts) / 10
        cz = -sum(p[1] for p in pts) / len(pts) / 10
        t = tiles[tile_of(cx, cz)]
        local = []
        for i in b.get("b", []):
            biz_home[i] = (tile_of(cx, cz), len(t["biz"]))
            local.append(len(t["biz"]))
            t["biz"].append(biz[i])
        nb = dict(b)
        if local:
            nb["b"] = local
        t["buildings"].append(nb)
        pk = str(b.get("pp"))
        if pk in facts:
            t["parcels"][pk] = facts[pk]
    for i, z in enumerate(biz):
        if i not in biz_home:
            tiles[tile_of(z["x"][0] / 10, -z["x"][1] / 10)]["biz"].append(z)

    # lines: kept whole, stored in every tile they pass through, with an id so the game loads each once
    for kind, items in (("roads", W["roads"]), ("walks", W["walks"]), ("creeks", W["creeks"])):
        for n, item in enumerate(items):
            pts = undelta(item["p"] if isinstance(item, dict) else item)
            keys = {tile_of(x / 10, -y / 10) for x, y in pts}
            for (a, b_), (c, d) in zip(pts, pts[1:]):        # long segments can cross a tile without a point in it
                L = math.hypot(c - a, d - b_) / 10
                for s in range(1, int(L // 400) + 1):
                    t_ = s * 400 / L
                    keys.add(tile_of((a + (c - a) * t_) / 10, -(b_ + (d - b_) * t_) / 10))
            rec = dict(item, id=n) if isinstance(item, dict) else {"id": n, "p": item}
            for k in keys:
                tiles[k][kind].append(rec)
    tr = W["trees"]
    for i in range(0, len(tr), 4):
        tiles[tile_of(tr[i] / 10, -tr[i + 1] / 10)]["trees"].extend(tr[i:i + 4])
    for lot in W.get("lots", []):
        pts = undelta(lot["r"])
        tiles[tile_of(sum(p[0] for p in pts) / 40, -sum(p[1] for p in pts) / 40)]["lots"].append(lot)

    # street furniture from the City's open data: light poles, signs, signals, hydrants (nfprep.street_furniture)
    from nfprep.street_furniture import build as build_furniture
    from nfprep.world3d import xy
    furn, art = build_furniture(W, xy)
    for kind, items in furn.items():
        for it in items:
            t = tiles[tile_of(it[0] / 10, -it[1] / 10)]
            t.setdefault("furn", {}).setdefault(kind, []).append(it)

    # fine cliff detail: 200 m chunks keyed by game chunk (x // 200, z // 200)
    F = T.get("fine")
    fine_atlas = read_packed(WORLD / F["file"]) if F else None

    out_dir = WORLD / "tiles"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    n_cells = int(TILE / CELL) + 1
    index_tiles = {}
    total = 0
    for ix in range(ix0, ix1 + 1):
        for iz in range(iz0, iz1 + 1):
            t = tiles[(ix, iz)]
            xs = ix * TILE + np.arange(n_cells) * CELL
            zs = iz * TILE + np.arange(n_cells) * CELL
            XS, ZS = np.meshgrid(xs, zs)                          # rows go south (z increasing)
            ground = sample(XS, ZS)
            doc = {"tile": [ix, iz], "x0": ix * TILE, "z0": iz * TILE, "cell": CELL, "n": n_cells,
                   "ground": png_data_url(ground), **t}
            if F:
                n = F["n"]
                fine_keys = []
                for k, slot in F["tiles"].items():
                    kx, kz = map(int, k.split(","))
                    if tile_of(kx * F["tile"] + 1, kz * F["tile"] + 1) == (ix, iz):
                        fine_keys.append((k, slot))
                if fine_keys:
                    per_row = 8
                    rows = (len(fine_keys) + per_row - 1) // per_row
                    atlas = np.zeros((rows * n, per_row * n), dtype="uint16")
                    slots = {}
                    for s, (k, slot) in enumerate(fine_keys):
                        r0, c0 = (slot // F["per_row"]) * n, (slot % F["per_row"]) * n
                        r1, c1 = (s // per_row) * n, (s % per_row) * n
                        atlas[r1:r1 + n, c1:c1 + n] = fine_atlas[r0:r0 + n, c0:c0 + n]
                        slots[k] = s
                    doc["fine"] = {"cell": F["cell"], "tile": F["tile"], "n": n, "per_row": per_row, "slots": slots, "image": png_data_url(atlas)}
            name = f"{ix}_{iz}.json"
            text = json.dumps(doc, separators=(",", ":"))
            (out_dir / name).write_text(text)
            index_tiles[f"{ix},{iz}"] = {"file": f"tiles/{name}", "kb": round(len(text) / 1024)}
            total += len(text)

    # far ground: the whole grid at 40 m in game coordinates
    step = int(FAR_CELL / CELL)
    fxs = np.arange(xmin, xmax + 1, FAR_CELL)
    fzs = np.arange(zmin, zmax + 1, FAR_CELL)
    FX, FZ = np.meshgrid(fxs, fzs)
    far = sample(FX, FZ)

    # skyline: every tall building, so towers show from anywhere
    skyline = [b for b in W["buildings"] if b["h"] >= TALL]
    for b in skyline:
        b.pop("b", None)

    # mist: the foot of every waterfall cell
    h = (grid & 0x3FFF) / 10.0 + T["base_m"]
    kind = grid >> 14
    gy, gx = np.gradient(h, CELL)
    falls = (kind == 1) & (np.hypot(gx, gy) > 1.2)
    sites = []
    for i, j in zip(*np.nonzero(falls)):
        i0, i1, j0, j1 = max(0, i - 2), min(ny, i + 3), max(0, j - 2), min(nx, j + 3)
        win = np.where(kind[i0:i1, j0:j1] == 1, h[i0:i1, j0:j1], 1e9)
        a, b_ = np.unravel_index(np.argmin(win), win.shape)
        sites.append([round(T["x0"] + (j0 + b_) * CELL, 1), round(float(win[a, b_]) - T["zero_m"], 1), round(-(T["y0"] + (i0 + a) * CELL), 1)])

    # the city map picture
    S = 2048
    sx = S / (xmax - xmin)
    Hh = int((zmax - zmin) * sx)
    img = Image.new("RGB", (S, Hh), (29, 43, 39))
    dr = ImageDraw.Draw(img)
    wy, wx = np.nonzero(kind == 1)
    for i, j in zip(wy[::2], wx[::2]):
        x, z = T["x0"] + j * CELL, -(T["y0"] + i * CELL)
        px, pz = (x - xmin) * sx, (z - zmin) * sx
        dr.rectangle([px, pz, px + CELL * sx * 1.6, pz + CELL * sx * 1.6], fill=(47, 111, 143))
    for r in W["roads"]:
        pts = [((x / 10 - xmin) * sx, (-y / 10 - zmin) * sx) for x, y in undelta(r["p"])]
        dr.line(pts, fill=(242, 194, 48) if r["c"] <= 2 else (127, 145, 138), width=3 if r["c"] <= 2 else 1)
    img.save(WORLD / "citymap.png", optimize=True)

    index = {
        "_meta": {**W["_meta"], "what": "Index of the streamed 3D city: tiles to load around the player, plus what is seen from far away."},
        "tile": TILE, "tiles": index_tiles,
        "terrain": {k: T[k] for k in ("base_m", "zero_m", "kinds", "source")},
        "far": {"x0": float(xmin), "z0": float(zmin), "cell": FAR_CELL, "nx": len(fxs), "nz": len(fzs), "image": png_data_url(far)},
        "map": {"file": "citymap.png", "x0": float(xmin), "z0": float(zmin), "scale": sx},
        "skyline": skyline, "landmarks": W["landmarks"], "mist": sites, "art": art,
    }
    (WORLD / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    sizes = sorted(v["kb"] for v in index_tiles.values())
    print(f"{len(index_tiles)} tiles, {total / 1e6:.1f} MB in all; median {sizes[len(sizes) // 2]} KB, largest {sizes[-1]} KB; "
          f"index.json {(WORLD / 'index.json').stat().st_size / 1e3:.0f} KB; {len(skyline)} skyline towers; {len(sites)} mist sites; "
          + ", ".join(f"{len(v)} {k}" for k, v in furn.items()) + f", {len(art)} public art")


if __name__ == "__main__":
    main()
