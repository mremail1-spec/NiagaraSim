"""Bundle what the browser game needs into web/: the city file, the road shapes and the engine.

Usage: python3 -m nfprep.webexport [year] [beta]
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

from .population import BUILD_DIR

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
WASM = ROOT / "engine" / "target" / "wasm32-unknown-unknown" / "release" / "nfsim.wasm"
LON0, LAT0 = -79.20, 43.16          # origin of the integer road coordinates (1e-5 degrees)
CLASS = {"motorway": 2, "motorway_link": 2, "trunk": 2, "trunk_link": 2,
         "primary": 1, "primary_link": 1, "secondary": 1, "secondary_link": 1}


INDUSTRY = {"11": "farming", "21": "mining", "22": "utilities", "23": "construction", "31-33": "manufacturing",
            "41": "wholesale", "44-45": "retail", "48-49": "transport", "51": "media", "52": "finance", "53": "real estate",
            "54": "professional services", "55": "management", "56": "admin and support", "61": "education",
            "62": "health care", "71": "arts and recreation", "72": "hotels and restaurants", "81": "other services",
            "91": "public administration"}


def _inside(lon, lat, ring):
    """Points (arrays) inside one polygon ring, by ray casting."""
    import numpy as np
    xs, ys = np.array([p[0] for p in ring]), np.array([p[1] for p in ring])
    inside = np.zeros(len(lon), bool)
    j = len(ring) - 1
    for i in range(len(ring)):
        cross = ((ys[i] > lat) != (ys[j] > lat)) & (lon < (xs[j] - xs[i]) * (lat - ys[i]) / (ys[j] - ys[i] + 1e-15) + xs[i])
        inside ^= cross
        j = i
    return inside


def people(src: Path) -> None:
    """Who lives in each simulated household, for the person card in the game (web/people.json).
    Households are in the same order as in city.json; town blocks beyond them have no profile."""
    import numpy as np
    from .loader import DATA_DIR
    hh = list(csv.DictReader(open(src / "households.csv", encoding="utf-8")))
    members = [[] for _ in hh]
    for p in csv.DictReader(open(src / "persons.csv", encoding="utf-8")):
        job = INDUSTRY.get(p["industry_naics"], "") if p["employed"] == "1" else ""
        members[int(p["household_id"])].append(f"{p['age']}{p['sex']}{':' + job if job else ''}")
    lat, lon = np.array([float(h["lat"]) for h in hh]), np.array([float(h["lon"]) for h in hh])
    gj = json.loads((DATA_DIR / "neighbourhood_boundaries.geojson").read_text())
    names, where = [], np.full(len(hh), -1)
    for k, f in enumerate(gj["features"]):
        pr = f["properties"]
        names.append(f"{pr['NAME'].title()} ({pr['COMMUNITY'].title()})")
        geo = f["geometry"]
        polys = geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]
        for poly in polys:
            where[(where < 0) & _inside(lon, lat, poly[0])] = k
    codes = lambda col: sorted({h[col] for h in hh})
    types, ages, tenures, dwellings = codes("type"), codes("maintainer_age_band"), codes("tenure"), codes("dwelling")
    doc = {"neighbourhoods": names, "types": types, "age_bands": ages, "tenures": tenures, "dwellings": dwellings,
           "households": {"nb": where.tolist(), "type": [types.index(h["type"]) for h in hh],
                          "age": [ages.index(h["maintainer_age_band"]) for h in hh], "tenure": [tenures.index(h["tenure"]) for h in hh],
                          "dwelling": [dwellings.index(h["dwelling"]) for h in hh],
                          "income_k": [round(float(h["income"]) / 1000) for h in hh], "cars": [int(h["vehicles"]) for h in hh],
                          "members": [",".join(m) for m in members]}}
    (WEB / "people.json").write_text(json.dumps(doc, separators=(",", ":")))


def main(year: int = 2025, beta: float = 0.0) -> None:
    src = BUILD_DIR / f"city_{year}"
    WEB.mkdir(exist_ok=True)
    seen, lines, names, name_index = set(), [], [""], {"": 0}
    with open(src / "road_edges.csv", encoding="utf-8") as f:
        for e in csv.DictReader(f):
            key = (min(e["from_node"], e["to_node"]), max(e["from_node"], e["to_node"]), e["length_m"])
            if key in seen:                      # two-way streets are stored once each way
                continue
            seen.add(key)
            nm = e["name"] or ""
            if nm not in name_index:
                name_index[nm] = len(names)
                names.append(nm)
            # [class, street name index, x0, y0, x1, y1, ...]
            flat = [CLASS.get(e["road_class"], 0), name_index[nm]]
            for p in e["polyline"].split(";"):
                la, lo = p.split(",")
                flat += [round((float(lo) - LON0) * 1e5), round((LAT0 - float(la)) * 1e5)]
            lines.append(flat)
    (WEB / "roads.json").write_text(json.dumps({"lon0": LON0, "lat0": LAT0, "year": year, "beta": beta, "names": names, "lines": lines},
                                               separators=(",", ":")))
    shutil.copy(src / "city.json", WEB / "city.json")
    people(src)
    if WASM.exists():
        shutil.copy(WASM, WEB / "nfsim.wasm")
    for name in ("roads.json", "city.json", "people.json", "nfsim.wasm"):
        print(f"{name:12s} {(WEB / name).stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]) if a else 2025, float(a[1]) if len(a) > 1 else 0.0)
