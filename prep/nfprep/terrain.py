"""Ground heights for the 3D city: the escarpment, the Niagara gorge, the Falls and the hydro canal.

Source: Natural Resources Canada, High Resolution Digital Elevation Model (HRDEM), lidar project
NRCAN-Hamilton_Niagara_2021 (bare-earth DTM, 1 m), Open Government Licence - Canada. Government data, used only
for the shape of the ground. Read from the cloud-optimised file at its 8 m overview and resampled to the game's
local grid (metres east and north of the world origin, as in web/world/world.json).

Writes web/world/terrain.png: one pixel per cell, red = low byte and green = high byte of a 16-bit value, row 0
at the south edge. Low 14 bits: height in decimetres above BASE_M (metres above sea level). Top 2 bits: 0 land,
1 water (City rivers layer), 2 park (City community parks). The grid's description goes into world.json "terrain".

Run from prep/ after nfprep.world3d: python3 -m nfprep.terrain
"""
import json
import math
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.fill import fillnodata
from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
WORLD = ROOT / "web" / "world"
DTM = ("/vsicurl/https://canelevation-dem.s3.ca-central-1.amazonaws.com/hrdem-lidar/"
       "NRCAN-Hamilton_Niagara_2021_2-1m-dtm.tif")
CELL = 10.0            # metres per grid cell in the game
READ_RES = 8.0         # metres per pixel read from the lidar file (one of its overviews)
BASE_M = 50.0          # stored heights are decimetres above this (the lower river is about 75 m)
ZERO_M = 175.0         # the game's ground level y = 0 sits at this height (about the upper river)
MARGIN = 600.0


def main():
    world = json.loads((WORLD / "world.json").read_text())
    o = world["_meta"]["origin"]
    lat0, lon0 = o["lat"], o["lon"]
    kx, ky = 111320.0 * math.cos(math.radians(lat0)), 110540.0

    # extent: every building plus a margin
    xs, ys = [], []
    for b in world["buildings"]:
        r, x, y = b["r"], 0, 0
        for i in range(0, len(r), 2):
            x += r[i]; y += r[i + 1]
            xs.append(x / 10); ys.append(y / 10)
    x0, x1 = math.floor((min(xs) - MARGIN) / CELL) * CELL, math.ceil((max(xs) + MARGIN) / CELL) * CELL
    y0, y1 = math.floor((min(ys) - MARGIN) / CELL) * CELL, math.ceil((max(ys) + MARGIN) / CELL) * CELL
    nx, ny = int((x1 - x0) / CELL) + 1, int((y1 - y0) / CELL) + 1
    print(f"grid {nx} x {ny} cells of {CELL:.0f} m ({(x1 - x0) / 1000:.1f} x {(y1 - y0) / 1000:.1f} km)")

    gx = x0 + np.arange(nx) * CELL
    gy = y0 + np.arange(ny) * CELL
    GX, GY = np.meshgrid(gx, gy)
    lon = lon0 + GX / kx
    lat = lat0 + GY / ky

    os.environ.setdefault("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")
    with rasterio.Env(GDAL_HTTP_TIMEOUT="120", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(DTM) as src:
            px, py = warp_transform("EPSG:4326", src.crs, lon.ravel().tolist(), lat.ravel().tolist())
            px, py = np.array(px).reshape(lon.shape), np.array(py).reshape(lon.shape)
            l, b_, r_, t_ = px.min() - 50, py.min() - 50, px.max() + 50, py.max() + 50
            win = from_bounds(l, b_, r_, t_, src.transform)
            w, h = int(round(win.width / READ_RES)), int(round(win.height / READ_RES))
            arr = src.read(1, window=win, out_shape=(h, w), resampling=Resampling.average, masked=False).astype("float32")
            nod = src.nodata
            wt = src.window_transform(win) * Affine.scale(win.width / w, win.height / h)
    print(f"read {w} x {h} pixels at {READ_RES:.0f} m")
    valid = (arr != nod) & np.isfinite(arr) & (arr > 0)
    filled = fillnodata(np.where(valid, arr, 0).astype("float32"), mask=valid.astype("uint8"), max_search_distance=400)
    # bilinear sample at the game's cell centres
    inv = ~wt
    col, row = inv * (px, py)
    c0, r0 = np.floor(col - 0.5).astype(int), np.floor(row - 0.5).astype(int)
    fc, fr = (col - 0.5) - c0, (row - 0.5) - r0
    c0, r0 = np.clip(c0, 0, w - 2), np.clip(r0, 0, h - 2)
    z = (filled[r0, c0] * (1 - fc) * (1 - fr) + filled[r0, c0 + 1] * fc * (1 - fr)
         + filled[r0 + 1, c0] * (1 - fc) * fr + filled[r0 + 1, c0 + 1] * fc * fr)
    z = np.where(z > 0, z, ZERO_M)

    # water, islands and parks from the City layers, rasterised on the game grid
    gt = Affine(CELL, 0, x0 - CELL / 2, 0, CELL, y0 - CELL / 2)       # row 0 at the south edge

    def shapes(path, keep):
        out = []
        for f in json.loads(path.read_text())["features"]:
            g = f["geometry"]
            if not g or not keep(f["properties"]):
                continue
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            for poly in polys:
                rings = [[((lo - lon0) * kx, (la - lat0) * ky) for lo, la in ring] for ring in poly]
                out.append({"type": "Polygon", "coordinates": rings})
        return out

    W = DATA / "city_open_data/world"
    water = rasterize([(s, 1) for s in shapes(W / "rivers.geojson", lambda p: p.get("Theme") != "Land")], out_shape=(ny, nx), transform=gt, fill=0, dtype="uint8")
    land = rasterize([(s, 1) for s in shapes(W / "rivers.geojson", lambda p: p.get("Theme") == "Land")], out_shape=(ny, nx), transform=gt, fill=0, dtype="uint8")
    parks = rasterize([(s, 1) for s in shapes(W / "parks.geojson", lambda p: True)], out_shape=(ny, nx), transform=gt, fill=0, dtype="uint8")
    kind = np.where((water == 1) & (land == 0), 1, np.where(parks == 1, 2, 0)).astype("uint16")

    dm = np.clip(np.round((z - BASE_M) * 10), 0, 16383).astype("uint16")
    packed = (dm | (kind << 14)).astype("uint16")
    # stored as a PNG the browser can read exactly: red = low byte, green = high byte, blue unused; row 0 is the
    # south edge (the image is upside down compared with a map)
    from PIL import Image
    rgb = np.zeros((ny, nx, 3), dtype="uint8")
    rgb[..., 0] = packed & 0xFF
    rgb[..., 1] = packed >> 8
    Image.fromarray(rgb, "RGB").save(WORLD / "terrain.png", optimize=True)
    (WORLD / "terrain.bin").unlink(missing_ok=True)
    world["terrain"] = {"file": "terrain.png", "x0": x0, "y0": y0, "cell": CELL, "nx": nx, "ny": ny,
                        "base_m": BASE_M, "zero_m": ZERO_M, "kinds": {"0": "land", "1": "water", "2": "park"},
                        "source": "Natural Resources Canada HRDEM lidar, Hamilton-Niagara 2021, bare-earth DTM, "
                                  "Open Government Licence - Canada (government data), read at 8 m, resampled to 10 m"}
    srcs = world["_meta"]["sources"]
    note = "Natural Resources Canada (government): HRDEM lidar ground heights, Hamilton-Niagara 2021, Open Government Licence - Canada"
    if note not in srcs:
        srcs.append(note)
    (WORLD / "world.json").write_text(json.dumps(world, separators=(",", ":")))
    print(f"terrain.png {(WORLD / 'terrain.png').stat().st_size / 1e6:.1f} MB; heights {z.min():.0f}-{z.max():.0f} m; "
          f"water {int((kind == 1).sum())} cells, parks {int((kind == 2).sum())} cells")


if __name__ == "__main__":
    main()
