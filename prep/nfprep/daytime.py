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


# what each group buys, in the residents' spending categories (lunch, coffee, snacks: restaurants;
# snacks and drinks from stores: groceries)
TO_CATEGORY = {"high_school_student": {"restaurants": 0.65, "groceries": 0.35},
               "elementary_student": {"restaurants": 0.7, "groceries": 0.3},
               "worker": {"restaurants": 0.85, "groceries": 0.15}}
RESIDENTS_WORKING_IN_CITY = 13_560     # 2021 Census, place of work (traffic_layers)
TICKET = 12.0                          # SEED: typical daytime purchase, sets how many trips the money is spread over


def add_to_city(doc: dict, road_nodes: list[dict]) -> dict:
    """Add the daytime crowd to the city's households: one group per road spot where people study or
    work, spending near it. Residents' share is taken out of city households' budgets (it was counted
    at home); workers from other towns bring theirs in as new money. Returns a summary."""
    import numpy as np
    from .traffic_layers import BAND_JOBS  # noqa: F401  (same job counts as the traffic layers)
    cats = doc["categories"]["name"]
    h = doc["households"]
    n_cat = len(cats)
    n_city = sum(1 for r in h["regional"] if not r)
    lat = np.array([n["lat"] for n in road_nodes]); lon = np.array([n["lon"] for n in road_nodes])
    ps = places()
    jobs = sum(p["people"] for p in ps if p["group"] == "worker")
    resident_share = min(1.0, RESIDENTS_WORKING_IN_CITY * JOBS_AT_USUAL_PLACE / max(jobs, 1))
    groups = {}
    for p in ps:
        d = np.hypot((lat - p["lat"]) * 111_320, (lon - p["lon"]) * 81_200)
        node = int(d.argmin())
        g = groups.setdefault(node, {"budget": np.zeros(n_cat), "resident": np.zeros(n_cat), "students": 0, "workers": 0.0, "names": []})
        for cat, share in TO_CATEGORY[p["group"]].items():
            amount = p["per_year"] * share
            g["budget"][cats.index(cat)] += amount
            g["resident"][cats.index(cat)] += amount * (1.0 if p["group"] != "worker" else resident_share)
        if p["group"] == "worker":
            g["workers"] += p["people"]
        else:
            g["students"] += p["people"]
        if len(g["names"]) < 3:
            g["names"].append(p["name"])
    budget = np.array(h["budget"], dtype=float).reshape(-1, n_cat)
    moved = sum(g["resident"] for g in groups.values())
    city_cat = budget[:n_city].sum(axis=0)
    keep = np.where(city_cat > 0, 1.0 - moved / np.maximum(city_cat, 1.0), 1.0)
    budget[:n_city] *= np.clip(keep, 0.0, 1.0)[None, :]
    rows, nodes, persons5, vehicles = [], [], [], []
    for node, g in groups.items():
        trips_per_day = g["budget"].sum() / TICKET / 365.0
        nodes.append(node)
        persons5.append(max(1, int(round(trips_per_day / doc["params"]["outings_per_person5_per_day"]))))
        vehicles.append(1 if g["workers"] > 0 else 0)
        rows.append(g["budget"])
    h["budget"] = [round(float(x), 1) for x in np.vstack([budget] + rows).ravel()]
    h["node"] = h["node"] + nodes
    h["persons5"] = h["persons5"] + persons5
    h["vehicles"] = h["vehicles"] + vehicles
    h["regional"] = h["regional"] + [0] * len(nodes)
    h["daytime_from"] = len(h["node"]) - len(nodes)      # households from here on are daytime groups
    h["daytime_label"] = ["; ".join(g["names"]) for g in groups.values()]
    summary = {"daytime_groups": len(nodes), "per_year_total": round(float(sum(g["budget"].sum() for g in groups.values()))),
               "moved_from_residents_homes": {c: round(float(moved[i])) for i, c in enumerate(cats) if moved[i] > 0},
               "new_money_from_workers_living_elsewhere": round(float(sum((g["budget"] - g["resident"]).sum() for g in groups.values()))),
               "resident_share_of_workers": round(resident_share, 3)}
    doc.setdefault("meta", {})["daytime"] = summary
    return summary
