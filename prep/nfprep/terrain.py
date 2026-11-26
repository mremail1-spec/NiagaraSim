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
FINE_CELL = 2.5        # metres per sample in the fine tiles on steep ground
FINE_TILE = 200.0      # metres per fine tile (the game's streaming chunk)
FINE_SLOPE = 0.5       # a tile gets fine samples where the 10 m ground is steeper than this (rise over run)
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TR_Z = 15
CACHE = ROOT / "build" / "terrarium"


def terrarium(lon, lat):
    """Heights from the public AWS Terrain Tiles (zoom 15). Used outside the Canadian lidar: in the United States
    these are the USGS 3D Elevation Program's heights (public domain, government data)."""
    import urllib.request
    from PIL import Image
    n = 2 ** TR_Z
    fx = (lon + 180.0) / 360.0 * n
    lr = np.radians(lat)
    fy = (1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / math.pi) / 2.0 * n
    out = np.zeros(lon.shape, dtype="float64")
    tx, ty = np.floor(fx).astype(int), np.floor(fy).astype(int)
    CACHE.mkdir(parents=True, exist_ok=True)
    for x, y in set(zip(tx.ravel().tolist(), ty.ravel().tolist())):
        f = CACHE / f"{TR_Z}_{x}_{y}.png"
        if not f.exists():
            req = urllib.request.Request(TERRARIUM.format(z=TR_Z, x=x, y=y), headers={"User-Agent": "niagara-sim-research"})
            import time
            for attempt in range(5):                       # the proxy can drop a tunnel mid-transfer: retry
                try:
                    f.write_bytes(urllib.request.urlopen(req, timeout=60).read())
                    break
                except OSError:
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
        a = np.asarray(Image.open(f).convert("RGB")).astype("float64")
        hts = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
        sel = (tx == x) & (ty == y)
        px, py = (fx[sel] - x) * 256 - 0.5, (fy[sel] - y) * 256 - 0.5
        c0, r0 = np.clip(np.floor(px).astype(int), 0, 254), np.clip(np.floor(py).astype(int), 0, 254)
        dx, dy = np.clip(px - c0, 0, 1), np.clip(py - r0, 0, 1)
        out[sel] = (hts[r0, c0] * (1 - dx) * (1 - dy) + hts[r0, c0 + 1] * dx * (1 - dy)
                    + hts[r0 + 1, c0] * (1 - dx) * dy + hts[r0 + 1, c0 + 1] * dx * dy)
    return out


def lidar_sample(src, px, py, res):
    """Bilinear lidar heights at points (in the file's projection), read at the overview closest to res metres;
    NaN where the survey has no data."""
    l, b_, r_, t_ = px.min() - 3 * res, py.min() - 3 * res, px.max() + 3 * res, py.max() + 3 * res
    win = from_bounds(l, b_, r_, t_, src.transform)
    w, h = max(2, int(round(win.width / res))), max(2, int(round(win.height / res)))
    arr = src.read(1, window=win, out_shape=(h, w), resampling=Resampling.average, boundless=True, fill_value=src.nodata).astype("float64")
    arr[(arr == src.nodata) | ~np.isfinite(arr) | (arr <= 0)] = np.nan
    wt = src.window_transform(win) * Affine.scale(win.width / w, win.height / h)
    col, row = ~wt * (px, py)
    c0, r0 = np.floor(col - 0.5).astype(int), np.floor(row - 0.5).astype(int)
    fc, fr = (col - 0.5) - c0, (row - 0.5) - r0
    c0, r0 = np.clip(c0, 0, w - 2), np.clip(r0, 0, h - 2)
    return (arr[r0, c0] * (1 - fc) * (1 - fr) + arr[r0, c0 + 1] * fc * (1 - fr)
            + arr[r0 + 1, c0] * (1 - fc) * fr + arr[r0 + 1, c0 + 1] * fc * fr)


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
    # where the Canadian survey has no data (the United States, some open water), use the USGS heights
    vmask = valid.astype("float32")
    vs = (vmask[r0, c0] * (1 - fc) * (1 - fr) + vmask[r0, c0 + 1] * fc * (1 - fr) + vmask[r0 + 1, c0] * (1 - fc) * fr + vmask[r0 + 1, c0 + 1] * fc * fr)
    outside = vs < 0.5
    if outside.any():
        z = z.astype("float64")
        z[outside] = terrarium(lon[outside], lat[outside])
        print(f"{int(outside.sum())} cells from USGS / terrain tiles")
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
    # the City's river layer stops at the border: on the US side, water is the gorge floor below 115 m, or ground
    # lower than 172.5 m that is flat to a few centimetres over 30 m (elevation models flatten water surfaces)
    from scipy.ndimage import uniform_filter
    rough = np.sqrt(np.maximum(uniform_filter(z * z, 3) - uniform_filter(z, 3) ** 2, 0))
    us_water = outside & ((z < 115) | ((z < 172.5) & (rough < 0.05)))
    kind[us_water & (kind == 0)] = 1
    print(f"{int(us_water.sum())} US water cells")
    # the public tiles carry riverbed depths (lake and river surveys) on the US side, not the water surface: give
    # every US water cell the surface height of the nearest water the Canadian lidar measured
    from scipy.ndimage import distance_transform_edt
    ca_water = (kind == 1) & ~outside
    us_w = (kind == 1) & outside
    if ca_water.any() and us_w.any():
        _, (ri, ci) = distance_transform_edt(~ca_water, return_indices=True)
        z = z.astype("float64")
        z[us_w] = z[ri[us_w], ci[us_w]]
        print(f"{int(us_w.sum())} US water cells set to the nearest measured water surface")
    # the City's river outline also covers the gorge walls: steep "water" is a waterfall only where it touches the
    # upper river (above 150 m) within 60 m; elsewhere it is the rock wall of the gorge
    from scipy.ndimage import binary_dilation
    gy2, gx2 = np.gradient(z, CELL)
    steep_w = (kind == 1) & (np.hypot(gx2, gy2) > 0.6)
    upper = (kind == 1) & (z > 150) & ~steep_w             # the upper river is 150-175 m; the gorge floor 75-100 m
    near_upper = binary_dilation(upper, iterations=6)
    walls = steep_w & ~near_upper
    kind[walls] = 0
    print(f"{int(walls.sum())} gorge-wall cells taken out of the river; {int((steep_w & near_upper).sum())} waterfall cells")

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
                                  "Open Government Licence - Canada (government data), read at 8 m, resampled to 10 m; "
                                  "outside the survey (the US side): USGS 3DEP heights via the AWS Terrain Tiles (zoom 15)"}
    # fine tiles on steep ground: the gorge walls, the Falls, the escarpment, the canal cuts
    gy_, gx_ = np.gradient(z, CELL)
    steep = np.hypot(gx_, gy_) > FINE_SLOPE
    ys_, xs_ = np.nonzero(steep)
    tiles = sorted(set(zip(((x0 + xs_ * CELL) // FINE_TILE).astype(int).tolist(), ((-(y0 + ys_ * CELL)) // FINE_TILE).astype(int).tolist())))
    n = int(FINE_TILE / FINE_CELL) + 1
    per_row = 24
    rows = (len(tiles) + per_row - 1) // per_row
    atlas = np.zeros((rows * n, per_row * n), dtype="uint16")
    slot = {}
    with rasterio.Env(GDAL_HTTP_TIMEOUT="120", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(DTM) as src:
            for t_i, (kx_, kz_) in enumerate(tiles):
                cx_ = kx_ * FINE_TILE + np.arange(n) * FINE_CELL           # x east
                cz_ = kz_ * FINE_TILE + np.arange(n) * FINE_CELL           # z = minus north
                CX, CZ = np.meshgrid(cx_, cz_)
                tlon, tlat = lon0 + CX / kx, lat0 + (-CZ) / ky
                ppx, ppy = warp_transform("EPSG:4326", src.crs, tlon.ravel().tolist(), tlat.ravel().tolist())
                fz = lidar_sample(src, np.array(ppx), np.array(ppy), 2.0).reshape(CX.shape)
                # keep each sample's kind from the 10 m grid
                gi = np.clip(np.round((-CZ - y0) / CELL).astype(int), 0, ny - 1)
                gj = np.clip(np.round((CX - x0) / CELL).astype(int), 0, nx - 1)
                miss = ~np.isfinite(fz)
                if miss.any():
                    fz[miss] = terrarium(tlon[miss], tlat[miss])
                    wet = miss & (kind[gi, gj] == 1)                # US water: the corrected surface from the 10 m grid
                    fz[wet] = z[gi[wet], gj[wet]]
                fdm = np.clip(np.round((fz - BASE_M) * 10), 0, 16383).astype("uint16")
                r_, c_ = divmod(t_i, per_row)
                atlas[r_ * n:(r_ + 1) * n, c_ * n:(c_ + 1) * n] = fdm | (kind[gi, gj] << 14)
                slot[f"{kx_},{kz_}"] = t_i
                if t_i % 50 == 0:
                    print(f"  fine tile {t_i + 1}/{len(tiles)}")
    rgb2 = np.zeros(atlas.shape + (3,), dtype="uint8")
    rgb2[..., 0] = atlas & 0xFF
    rgb2[..., 1] = atlas >> 8
    Image.fromarray(rgb2, "RGB").save(WORLD / "terrain_fine.png", optimize=True)
    world["terrain"]["fine"] = {"file": "terrain_fine.png", "cell": FINE_CELL, "tile": FINE_TILE, "n": n, "per_row": per_row,
                                "tiles": slot, "note": "2.5 m samples on steep ground; tile key is the game chunk (x // 200, z // 200), z = minus north; rows run south"}
    print(f"terrain_fine.png {(WORLD / 'terrain_fine.png').stat().st_size / 1e6:.1f} MB, {len(tiles)} tiles")
    srcs = world["_meta"]["sources"]
    for note in ("Natural Resources Canada (government): HRDEM lidar ground heights, Hamilton-Niagara 2021, Open Government Licence - Canada",
                 "USGS 3D Elevation Program via the AWS Terrain Tiles (government, public domain): ground heights on the US side"):
        if note not in srcs:
            srcs.append(note)
    (WORLD / "world.json").write_text(json.dumps(world, separators=(",", ":")))
    print(f"terrain.png {(WORLD / 'terrain.png').stat().st_size / 1e6:.1f} MB; heights {z.min():.0f}-{z.max():.0f} m; "
          f"water {int((kind == 1).sum())} cells, parks {int((kind == 2).sum())} cells")


if __name__ == "__main__":
    main()
