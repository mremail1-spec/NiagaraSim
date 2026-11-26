"""What the City's open data says about each property parcel (government data, City of Niagara Falls Terms of Use).

One fact sheet per parcel id, joined by the parcel id each layer carries, or by where the lot lies for the area
layers (zoning, Official Plan, Business Improvement Areas). Used by nfprep.world3d for the game's building card.

Keys (all optional): z zone code, zt zone type, op Official Plan designation, env an Official Plan environmental area over it, bia Business Improvement Area,
n civic addresses on the lot, her [heritage status, name], condo [name, units, type], city [City property kind, name],
bp [permits 2015-2023, latest year, latest type, latest work], da [file, application type, link, summary].
"""
import html
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "city_open_data" / "world"
CELL = 0.005                                   # degrees, for the area-layer lookup grid


def _features(name):
    path = SRC / f"{name}.geojson"
    return json.loads(path.read_text())["features"] if path.exists() else []


def _props(name):
    return [f["properties"] for f in _features(name)]


def _title(s):
    return " ".join(w.capitalize() for w in str(s).split()) if s else s


def _rings(geom):
    if not geom:
        return []
    return [p[0] for p in ([geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"])]


def _inside(ring, x, y):
    hit = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


class _Areas:
    """Point-in-polygon lookup over one area layer."""

    def __init__(self, names, value):
        self.items, self.grid = [], defaultdict(list)
        for name in names:
            for f in _features(name):
                v = value(f["properties"])
                if v is None:
                    continue
                for ring in _rings(f["geometry"]):
                    k = len(self.items)
                    self.items.append((ring, v))
                    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
                    for gx in range(int(min(xs) // CELL), int(max(xs) // CELL) + 1):
                        for gy in range(int(min(ys) // CELL), int(max(ys) // CELL) + 1):
                            self.grid[(gx, gy)].append(k)

    def all_at(self, lon, lat):
        return [self.items[k][1] for k in self.grid.get((int(lon // CELL), int(lat // CELL)), []) if _inside(self.items[k][0], lon, lat)]

    def at(self, lon, lat):
        hits = self.all_at(lon, lat)
        return hits[0] if hits else None


def _zone(p):
    code = (p.get("ZONE_ABR") or p.get("ZONE_TTL") or "").strip()
    sp = (p.get("SP1") or p.get("SPEC_PROV1") or "")
    sp = str(sp).strip() if sp not in (None, 0, "0") else ""
    if sp and sp not in code:
        code = f"{code}-{sp}"
    return [code, _title(p.get("ZONE_TYPE") or p.get("ZONE_TTL") or "")] if code else None


def _summary(text, n=280):
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def parcel_facts(pids_with_points):
    """pids_with_points: {parcel id: (lon, lat)} for the lots to describe. Returns {parcel id: facts}."""
    want = set(pids_with_points)
    out = defaultdict(dict)

    n = defaultdict(int)
    for p in _props("address_points"):
        if p.get("pID") in want:
            n[p["pID"]] += 1
    for k, v in n.items():
        if v > 1:
            out[k]["n"] = v
    for p in _props("heritage"):
        if p.get("pID") in want:
            out[p["pID"]]["her"] = [_title(p.get("Type")), (p.get("Name") or "").strip()]
    for p in _props("condominiums"):
        if p.get("PID") in want:
            out[p["PID"]]["condo"] = [(p.get("NAME") or "").strip(), p.get("CONNOUNITS"), _title(p.get("TYPE"))]
    for p in _props("city_property"):
        if p.get("pID") in want:
            out[p["pID"]]["city"] = [p.get("FacilityType") or "City property", (p.get("CP_WebName") or "").strip()]
    permits = defaultdict(list)
    for p in _props("building_permits"):
        if p.get("PID") in want:
            permits[p["PID"]].append(p)
    for k, ps in permits.items():
        last = max(ps, key=lambda q: q.get("PermitDate") or 0)
        out[k]["bp"] = [len(ps), last.get("Year"), last.get("PermitType"), (last.get("WorkDesc") or "").strip()]
    for p in _props("development_applications"):
        if p.get("pid") in want:
            out[p["pid"]]["da"] = [p.get("FileNumber"), p.get("TypeDesc") or "Planning application", p.get("weblink"), _summary(p.get("ApplDesc"))]

    zoning = _Areas(["zoning_79200", "zoning_willoughby", "zoning_crowland", "zoning_humberstone"], _zone)
    op = _Areas(["op_land_use"], lambda p: _title(p.get("DESIGNAT")))
    bia = _Areas(["bia"], lambda p: _title(p.get("NAME")))
    for pid, (lon, lat) in pids_with_points.items():
        z = zoning.at(lon, lat)
        if z:
            out[pid]["z"], out[pid]["zt"] = z
        ds = op.all_at(lon, lat)                   # environmental areas overlay the land-use designations
        base = [d for d in ds if not d.startswith("Environmental")]
        env = [d for d in ds if d.startswith("Environmental")]
        if base or env:
            out[pid]["op"] = (base or env)[0]
        if base and env:
            out[pid]["env"] = env[0]
        b = bia.at(lon, lat)
        if b:
            out[pid]["bia"] = b
    return dict(out)
