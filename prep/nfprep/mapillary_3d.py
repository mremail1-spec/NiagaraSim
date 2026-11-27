"""Building heights from Mapillary's 3D reconstructions (structure from motion; Mapillary, CC BY-SA 4.0).

Mapillary computes a sparse 3D point cloud for each stretch of a drive (OpenSfM "sfm_cluster": coloured points in
metres east, north and up from a reference latitude and longitude). Downloaded to build/mapillary/clusters/ by
`python3 -m nfprep.mapillary_3d fetch <bbox>`.

Height of a building = roof points inside its footprint minus the ground points just outside it, both taken from
the same point cloud, so each drive's GPS altitude error cancels. Each point cloud gives one estimate; the building
gets the median over the clouds that see it (at least 30 roof points and 30 ground points each). Compared with the
City's 2018 survey heights (government data) in the report.

Run from prep/: python3 -m nfprep.mapillary_3d heights
"""
from __future__ import annotations

import json
import math
import sys
import urllib.request
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "build" / "mapillary"
OUT = ROOT / "data" / "mapillary_building_heights.json"
UA = {"User-Agent": "nfsim-prep/0.1"}       # the access token is added by the network proxy


def _get(url: str):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120))


def fetch(bbox: str) -> None:
    """Every Mapillary point cloud that covers a bbox 'lon0,lat0,lon1,lat1' (split into strips of 0.003 deg)."""
    lon0, lat0, lon1, lat1 = map(float, bbox.split(","))
    im, a = [], lon0
    while a < lon1:
        b = min(lon1, a + 0.003)
        im += _get(f"https://graph.mapillary.com/images?fields=id,sfm_cluster&bbox={a},{lat0},{b},{lat1}&limit=2000")["data"]
        a = b
    one = {}
    for i in im:
        c = i.get("sfm_cluster")
        if c and c["id"] not in one:
            one[c["id"]] = i["id"]
    (CACHE / "clusters").mkdir(parents=True, exist_ok=True)
    got = 0
    for cid, iid in one.items():
        f = CACHE / "clusters" / f"{cid}.json"
        if f.exists():
            continue
        url = (_get(f"https://graph.mapillary.com/{iid}?fields=sfm_cluster").get("sfm_cluster") or {}).get("url")
        if not url:
            continue                                # Mapillary lists the cloud but offers no download
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180).read()
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        f.write_bytes(raw)
        got += 1
    print(f"{len(im)} photos, {len(one)} point clouds, {got} downloaded now")


def clouds():
    """Yield (cluster id, points as array of [x_m east, y_m north, z_m up] in the game's frame, colours)."""
    from nfprep.world3d import LAT0, LON0
    kx = 111320.0 * math.cos(math.radians(LAT0))
    for f in sorted((CACHE / "clusters").glob("*.json")):
        for r in json.loads(f.read_text()):
            ref = r["reference_lla"]
            P = np.array([p["coordinates"] for p in r["points"].values()], dtype=float)
            C = np.array([p["color"] for p in r["points"].values()], dtype=float)
            if len(P) == 0:
                continue
            # topocentric east/north/up around the reference -> metres east/north of the game origin
            lat = ref["latitude"] + P[:, 1] / 110540.0
            lon = ref["longitude"] + P[:, 0] / (111320.0 * math.cos(math.radians(ref["latitude"])))
            xy = np.column_stack([(lon - LON0) * kx, (lat - LAT0) * 110540.0, P[:, 2] + ref.get("altitude", 0.0)])
            yield f.stem, xy, C


def _inside(ring: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    hit = np.zeros(len(x), bool)
    for (ax, ay), (bx, by) in zip(ring, np.roll(ring, -1, axis=0)):
        cond = (ay > y) != (by > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = ax + (y - ay) * (bx - ax) / (by - ay)
        hit ^= cond & (x < xi)
    return hit


def heights(min_pts: int = 30) -> dict:
    W = json.loads((ROOT / "web" / "world" / "world.json").read_text())

    def ring(b):
        r, out, X, Y = b["r"], [], 0, 0
        for i in range(0, len(r), 2):
            X += r[i]; Y += r[i + 1]
            out.append((X / 10, Y / 10))
        return np.array(out)

    C = list(clouds())
    allxy = np.vstack([c[1][:, :2] for c in C])
    lo, hi = allxy.min(0) - 20, allxy.max(0) + 20
    blds = []
    for b in W["buildings"]:
        R = ring(b)
        if (R[:, 0] > lo[0]).all() and (R[:, 0] < hi[0]).all() and (R[:, 1] > lo[1]).all() and (R[:, 1] < hi[1]).all():
            blds.append((b, R))
    est = defaultdict(list)
    for cid, P, _ in C:
        for b, R in blds:
            x0, y0 = R.min(0) - 15
            x1, y1 = R.max(0) + 15
            m = (P[:, 0] > x0) & (P[:, 0] < x1) & (P[:, 1] > y0) & (P[:, 1] < y1)
            if m.sum() < 2 * min_pts:
                continue
            Q = P[m]
            ins = _inside(R, Q[:, 0], Q[:, 1])
            # ground: points outside the footprint, within the 15 m ring, lowest third
            out = Q[~ins]
            if ins.sum() < min_pts or len(out) < min_pts:
                continue
            g = np.percentile(out[:, 2], 15)
            top = np.percentile(Q[ins][:, 2], 90)
            h = top - g
            if 2.0 <= h <= 120:
                est[b["id"]].append((h, int(ins.sum()), cid))
    res = {}
    for b, R in blds:
        e = est.get(b["id"])
        if not e:
            continue
        hs = np.array([x[0] for x in e])
        res[b["id"]] = {"height_m": round(float(np.median(hs)), 1), "clouds": len(e), "spread_m": round(float(hs.max() - hs.min()), 1),
                        "roof_points": int(sum(x[1] for x in e)), "city_2018_m": b["h"]}
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "source": "Mapillary structure-from-motion point clouds (CC BY-SA 4.0)",
                     "city_2018_m": "City of Niagara Falls building footprints 2018 height (government data)"},
           "buildings": res}
    OUT.write_text(json.dumps(doc, indent=1))
    return doc


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    else:
        d = heights()["buildings"]
        diffs = [v["height_m"] - v["city_2018_m"] for v in d.values() if v["clouds"] >= 2]
        print(f"{len(d)} buildings measured, {sum(v['clouds'] >= 2 for v in d.values())} by 2+ point clouds; "
              f"median difference from the City's 2018 heights {np.median(diffs):+.1f} m" if diffs else f"{len(d)} buildings measured")
        for k in ("981b0da65f", "6e35ae6a7a", "361d957eef", "6be732fa38"):
            if k in d:
                print(k, d[k])
