"""Backtest: can the model tell which 2021 businesses would be gone by 2026?

The city is rebuilt with the restaurants and shops that were on the map on 1 January 2021
(central Niagara Falls). Each gets the model's expected yearly revenue. A business counts as
closed if it has left the map since, or now carries a clearly different name.

The model is never told which businesses closed. Demand is the calibrated 2025 city, so the
score measures how well a site and format fit the city's normal spending, not the pandemic.

Usage: python3 -m nfprep.backtest
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .budgets import export_city
from .build import build_city, load_full_pack
from .loader import DATA_DIR
from .population import BUILD_DIR, points_in_ring

ROOT = Path(__file__).resolve().parents[2]
BOX = (43.04, 43.13, -79.16, -79.05)
FOOD = {"restaurant": "restaurant", "fast_food": "fast_food", "cafe": "cafe", "bar": "bar_pub", "pub": "bar_pub"}
STOP = {"the", "restaurant", "restaurants", "cafe", "bar", "grill", "and", "of", "niagara", "falls", "inc", "ltd", "store", "shop"}


def norm(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("&", " and ").replace("'s", "s")).split()
    return " ".join(w for w in words if w not in STOP)


def same_business(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    return bool(a) and bool(b) and (a == b or a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.7)


def in_box(p: dict) -> bool:
    return BOX[0] <= p["lat"] <= BOX[1] and BOX[2] <= p["lon"] <= BOX[3]


def is_target(p: dict) -> bool:
    return p["amenity"] in FOOD or bool(p["shop"])


def label_closures(old: list[dict], now: list[dict]) -> dict[str, dict]:
    by_id = {p["id"]: p for p in now}
    nlat, nlon = np.array([p["lat"] for p in now]), np.array([p["lon"] for p in now])
    out = {}
    for p in old:
        if not p["name"] or p["shop"] == "vacant":
            continue                                      # nothing to follow up on
        cur = by_id.get(p["id"])
        if cur is not None:
            closed = bool(cur["disused"]) or not (is_target(cur)) or not same_business(p["name"], cur["name"] or "")
            how = "still there" if not closed else ("replaced: " + (cur["name"] or "vacant"))
        else:
            # the map object is gone; it only counts as open if the same name is still within 60 m
            d = np.hypot((nlat - p["lat"]) * 111_320, (nlon - p["lon"]) * 111_320 * np.cos(np.radians(p["lat"])))
            near = [now[i] for i in np.where(d < 60)[0]]
            moved = any(same_business(p["name"], q["name"] or "") for q in near)
            closed, how = (not moved), ("remapped, still there" if moved else "gone from the map")
        out[p["id"]] = {"closed": closed, "how": how}
    return out


def auc(scores_closed: list[float], scores_open: list[float]) -> float:
    """Chance that a closed business scored below a surviving one (0.5 = coin flip)."""
    c, o = np.array(scores_closed), np.array(scores_open)
    less = (c[:, None] < o[None, :]).sum() + 0.5 * (c[:, None] == o[None, :]).sum()
    return float(less / (len(c) * len(o)))


def _rate(rows: list[dict]) -> dict:
    return {"followed": len(rows), "closed": sum(r["closed"] for r in rows),
            "rate": round(sum(r["closed"] for r in rows) / max(len(rows), 1), 3)}


def _pct(r: dict, rows: list[dict]) -> float:
    peers = [x["site_demand"] for x in rows if x["sim_category"] == r["sim_category"]]
    return sum(p < r["site_demand"] for p in peers) / len(peers)


def main() -> dict:
    old = json.loads((DATA_DIR / "backtest" / "osm_places_2021-01-01.json").read_text())["places"]
    now = json.loads((DATA_DIR / "backtest" / "osm_places_2026-07-15.json").read_text())["places"]
    labels = label_closures(old, now)

    # --- rebuild the city with the 2021 restaurants and shops in the central area ---
    pack = load_full_pack()
    today = pack.tables["places.csv"]
    kind = {(r["key"], r["value"]): r["sim_category"] for r in today}
    keep = [r for r in today if not (BOX[0] <= float(r["lat"]) <= BOX[1] and BOX[2] <= float(r["lon"]) <= BOX[3]
                                     and (r["sim_category"] in set(FOOD.values()) | {"nightlife"} and r["key"] == "amenity"
                                          or r["key"] == "shop"))]
    core = pack.geo["tourist_core_boundary.geojson"]["features"][0]["geometry"]["coordinates"][0]
    tracts = {f["properties"]["census_tract"]: f["geometry"]["coordinates"][0]
              for f in pack.geo["census_tracts_boundaries.geojson"]["features"]}
    lat, lon = np.array([p["lat"] for p in old]), np.array([p["lon"] for p in old])
    in_core = points_in_ring(lon, lat, core)
    tract_of = np.array([""] * len(old), dtype=object)
    for t, ring in tracts.items():
        tract_of[points_in_ring(lon, lat, ring)] = t
    lookup = [r for r in pack.tables["places_tract_lookup.csv"] if r["osm_id"] in {k["osm_id"] for k in keep}]
    added = 0
    for i, p in enumerate(old):
        if not tract_of[i] or p["shop"] == "vacant":
            continue
        key, value = ("amenity", p["amenity"]) if p["amenity"] in FOOD else ("shop", p["shop"])
        cat = FOOD.get(p["amenity"]) or kind.get((key, value), "retail_other")
        keep.append({"osm_id": p["id"], "name": p["name"], "key": key, "value": value, "sim_category": cat, "brand": p["brand"],
                     "cuisine": "", "stars": "", "rooms": "", "opening_hours": "", "street": "", "lat": str(p["lat"]),
                     "lon": str(p["lon"]), "neighbourhood": "", "community": "", "in_tourist_core": str(int(in_core[i]))})
        lookup.append({"osm_id": p["id"], "census_tract": tract_of[i]})
        added += 1
    pack.tables["places.csv"], pack.tables["places_tract_lookup.csv"] = keep, lookup

    city = build_city(2025, 1, pack)
    out = BUILD_DIR / "backtest_2021"
    export_city(pack, city, city["budgets"], 2025, out / "city.json")
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(out / "city.json"), "--mode", "flow", "--days", "365",
                    "--beta", str(beta), "--businesses", str(out / "revenue.csv")], check=True, stdout=subprocess.DEVNULL)
    table = list(csv.DictReader(open(out / "revenue.csv")))
    revenue = {int(r["business"]): float(r["revenue"]) for r in table}
    tourist = {int(r["business"]): float(r["tourist_revenue"]) for r in table}

    # --- score: where a business's expected revenue ranks among its own kind ---
    biz = city["businesses"]
    rows = []
    by_cat: dict[str, list[float]] = {}
    for i, b in enumerate(biz):
        if b["osm_id"] in labels:
            by_cat.setdefault(b["sim_category"], []).append(revenue.get(i, 0.0))
    for i, b in enumerate(biz):
        if b["osm_id"] not in labels:
            continue
        peers = np.array(by_cat[b["sim_category"]])
        rev = revenue.get(i, 0.0)
        rows.append({"osm_id": b["osm_id"], "name": b["name"], "sim_category": b["sim_category"],
                     "in_tourist_core": b["in_tourist_core"], "expected_revenue": round(rev),
                     "rank_among_its_kind": round(float((peers < rev).mean()), 3),
                     "revenue_vs_size": round(rev / max(float(b["revenue_prior"] or 0), 30_000), 3),
                     # demand the site offers per unit of pull: strips out the size the model assigned at random
                     "site_demand": round(rev / (max(b["format_weight"], 0.01) * max(float(b["revenue_prior"] or 0), 30_000) ** 0.5), 1),
                     "tourist_share": round(tourist.get(i, 0.0) / rev, 3) if rev > 0 else 0.0,
                     "closed": labels[b["osm_id"]]["closed"], "what_happened": labels[b["osm_id"]]["how"]})
    closed = [r for r in rows if r["closed"]]
    alive = [r for r in rows if not r["closed"]]
    rate = len(closed) / len(rows)
    ranked = sorted(rows, key=lambda r: r["rank_among_its_kind"])
    tenth = ranked[:max(1, len(ranked) // 10)]
    fifth = ranked[:max(1, len(ranked) // 5)]
    result = {
        "period": "1 January 2021 to 15 July 2026", "businesses_followed": len(rows), "closed_or_replaced": len(closed),
        "closure_rate": round(rate, 3),
        "closure_ranking_auc": round(auc([r["rank_among_its_kind"] for r in closed], [r["rank_among_its_kind"] for r in alive]), 3),
        "auc_using_revenue_vs_size": round(auc([r["revenue_vs_size"] for r in closed], [r["revenue_vs_size"] for r in alive]), 3),
        "auc_using_site_demand_within_kind": round(auc(*[[_pct(r, rows) for r in grp] for grp in (closed, alive)]), 3),
        # not a forecast of the model: tests whether reliance on tourists marked out the casualties of 2020-2022
        "auc_using_low_tourist_share": round(auc([1 - r["tourist_share"] for r in closed], [1 - r["tourist_share"] for r in alive]), 3),
        "closure_rate_by_tourist_share": {
            "under_25pct": _rate([r for r in rows if r["tourist_share"] < 0.25]),
            "25_to_60pct": _rate([r for r in rows if 0.25 <= r["tourist_share"] < 0.60]),
            "over_60pct": _rate([r for r in rows if r["tourist_share"] >= 0.60])},
        "closure_rate_in_weakest_tenth": round(sum(r["closed"] for r in tenth) / len(tenth), 3),
        "closure_rate_in_weakest_fifth": round(sum(r["closed"] for r in fifth) / len(fifth), 3),
        "closure_rate_in_strongest_half": round(sum(r["closed"] for r in ranked[len(ranked) // 2:]) / (len(ranked) - len(ranked) // 2), 3),
        "pass_marks": {"closure_ranking_auc": 0.70, "weakest_tenth_at_least": round(2 * rate, 3)},
        "by_kind": {}, "businesses": sorted(rows, key=lambda r: r["rank_among_its_kind"]),
    }
    for cat in sorted(by_cat):
        sub = [r for r in rows if r["sim_category"] == cat]
        c = [r["rank_among_its_kind"] for r in sub if r["closed"]]
        o = [r["rank_among_its_kind"] for r in sub if not r["closed"]]
        if len(sub) >= 15:
            result["by_kind"][cat] = {"followed": len(sub), "closed": len(c), "auc": round(auc(c, o), 3) if c and o else None}
    (ROOT / "docs" / "backtest_2021_2026.json").write_text(json.dumps(result, indent=1))
    for k, v in result.items():
        if k not in ("businesses",):
            print(f"{k:34s} {v}")
    print("weakest ten by the model:")
    for r in result["businesses"][:10]:
        print(f"  {r['name'][:34]:34s} {r['sim_category']:14s} {'CLOSED' if r['closed'] else 'open  '} {r['what_happened']}")
    return result


if __name__ == "__main__":
    main()
