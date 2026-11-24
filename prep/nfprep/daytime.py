"""The daytime crowd: what students and workers spend near their school or workplace.

Residents' spending in the model starts from home. But on a school day a high school student buys
lunch and snacks near the school, and a worker buys coffee and lunch near work. This values every
school (Ontario enrolment, government data) and every workplace (Niagara Region's Employment
Inventory 2025, employee bands) by the money its people spend nearby each year
(data/daytime_spending.json: per person, by group, with ranges), and sums it around any spot.

Usage: python3 -m nfprep.daytime                     (city summary; docs/daytime_value.json)
       python3 -m nfprep.daytime near "8123 Lundy's Ln" [metres]
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
JOBS_AT_USUAL_PLACE = 0.8      # as in traffic_layers: share of jobs worked at the usual place on a day


def places() -> list[dict]:
    """Every school and workplace with its people and the money they spend nearby each year."""
    spend = json.loads((DATA_DIR / "daytime_spending.json").read_text())["groups"]
    out = []
    for s in json.loads((DATA_DIR / "school_enrolment_2022_23.json").read_text())["schools"]:
        if not s["enrolment"] or s["lat"] is None:
            continue
        g = "high_school_student" if s["level"] == "Secondary" else "elementary_student"
        v = spend[g]
        out.append({"name": s["name"], "kind": "high school" if g.startswith("high") else "elementary school",
                    "address": s["address"], "lat": float(s["lat"]), "lon": float(s["lon"]), "people": s["enrolment"],
                    "group": g, "per_year": s["enrolment"] * v["per_year"], "low": s["enrolment"] * v["low"], "high": s["enrolment"] * v["high"]})
    from .traffic_layers import BAND_JOBS
    v = spend["worker"]
    for x in csv.DictReader(open(DATA_DIR / "sources/region/employment_inventory_niagara_falls_2016_2025.csv", encoding="utf-8-sig")):
        if x["Year"] != "2025" or not x["lat"]:
            continue
        jobs = BAND_JOBS.get(x["sizerangeemployees"], 0) * JOBS_AT_USUAL_PLACE
        if jobs <= 0:
            continue
        out.append({"name": x["businessname"].strip(), "kind": "workplace", "naics": x["primarynaics"],
                    "address": f'{x["businessstreetnumber"]} {x["businessstreetname"]}'.strip(),
                    "lat": float(x["lat"]), "lon": float(x["lon"]), "people": round(jobs, 1), "group": "worker",
                    "per_year": jobs * v["per_year"], "low": jobs * v["low"], "high": jobs * v["high"]})
    return out


def dist(a, b, c, d) -> float:
    return math.hypot((a - c) * 111_320, (b - d) * 111_320 * math.cos(math.radians(a)))


def near(address: str, metres: float = 500) -> dict:
    pts = {r["address"].upper(): r for r in csv.DictReader(open(DATA_DIR / "address_points.csv"))}
    from .rent_surface import _key
    p = pts.get(_key(address) or "") or next((v for k, v in pts.items() if k.startswith((_key(address) or "~")[:12])), None)
    if not p:
        raise SystemExit(f"address not found: {address}")
    la, lo = float(p["lat"]), float(p["lon"])
    rows = [dict(x, metres=round(dist(la, lo, x["lat"], x["lon"]))) for x in places() if dist(la, lo, x["lat"], x["lon"]) <= metres]
    rows.sort(key=lambda r: -r["per_year"])
    tot = {g: sum(r["per_year"] for r in rows if r["group"] == g) for g in ("high_school_student", "elementary_student", "worker")}
    doc = {"address": address, "radius_m": metres, "people": round(sum(r["people"] for r in rows)),
           "per_year": round(sum(tot.values())), "low": round(sum(r["low"] for r in rows)), "high": round(sum(r["high"] for r in rows)),
           "by_group": {k: round(v) for k, v in tot.items()},
           "places": [{k: (round(v) if k in ("per_year", "low", "high") else v) for k, v in r.items() if k not in ("lat", "lon")} for r in rows[:25]]}
    print(json.dumps(doc, indent=1))
    return doc


def main() -> dict:
    ps = places()
    by = {}
    for x in ps:
        b = by.setdefault(x["kind"], {"places": 0, "people": 0.0, "per_year": 0.0, "low": 0.0, "high": 0.0})
        b["places"] += 1; b["people"] += x["people"]
        for k in ("per_year", "low", "high"):
            b[k] += x[k]
    top = sorted(ps, key=lambda x: -x["per_year"])[:40]
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "values": "data/daytime_spending.json (SEED, ranges)",
                     "schools": "Ontario School Information 2022-23 (government)", "workplaces": "Niagara Region Employment Inventory 2025 (government survey)"},
           "by_kind": {k: {kk: round(vv) for kk, vv in v.items()} for k, v in by.items()},
           "biggest": [{k: (round(v) if isinstance(v, float) else v) for k, v in x.items() if k not in ("lat", "lon")} for x in top]}
    (ROOT / "docs" / "daytime_value.json").write_text(json.dumps(doc, indent=1))
    print(json.dumps(doc["by_kind"], indent=1))
    for x in top[:15]:
        print(f"  ${x['per_year'] / 1e3:8.0f}k  {x['kind']:16s} {x['name'][:45]:45s} {x['people']:>7}")
    return doc


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "near":
        near(a[1], float(a[2]) if len(a) > 2 else 500)
    else:
        main()
