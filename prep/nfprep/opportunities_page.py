"""Data for the opportunities page (docs/pages/opportunities_data.json) from the scan and its checks."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "docs"
FOOD = ["Fast food", "Cafe", "Grocery or convenience store", "Restaurant"]
HIGH = ["A N Myer Secondary School", "Saint Michael Catholic High School", "Westlane Secondary School", "Stamford Collegiate", "Saint Paul Catholic High School"]


def main():
    o = json.loads((D / "opportunities.json").read_text())
    by = {}
    for name, v in o["by_kind"].items():           # "Kind (format)" -> best of the two formats per kind
        kind, fmt = name.rsplit(" (", 1)
        v = dict(v, format=fmt.rstrip(")"))
        if kind not in by or v["best_spot_vs_median_worst_scenario"] > by[kind]["best_spot_vs_median_worst_scenario"]:
            by[kind] = dict(v, spots=v["spots_beating_median_in_every_scenario"])
        else:
            by[kind]["spots"] = max(by[kind]["spots"], v["spots_beating_median_in_every_scenario"])
    kinds = sorted(({"kind": k, "format": v["format"], "now": v["businesses_now"], "median": v["median_sales_now_default"], "spots": v["spots"],
                     "best": v["best_spot"], "best_x": v["best_spot_vs_median_worst_scenario"], "best_sales": v["best_spot_forecast_default"]}
                    for k, v in by.items()), key=lambda r: -r["best_x"])
    top, seen = [], set()
    for f in o["opportunities"]:                      # the strongest spot of each kind
        if f["kind"] not in seen:
            seen.add(f["kind"]); top.append(f)
    lease = [f for f in o["opportunities"] if f["for_lease"]][:12]
    gaps = {p["name"]: p for p in json.loads((D / "lunch_gaps.json").read_text())["places"]}
    from nfprep.daytime import places
    closed = {p["name"] for p in places() if p.get("cafeteria_closed")}
    schools = []
    for s in HIGH:
        v = o["near_schools"].get(s)
        if not v:
            continue
        g = gaps.get(s, {})
        rows = {}
        for r in v["best_by_kind"]:
            if r["kind"] in FOOD and (r["kind"] not in rows or r["vs_yardstick_worst"] > rows[r["kind"]]["vs_yardstick_worst"]):
                rows[r["kind"]] = r
        schools.append({"school": s, "students": v["students"], "money": g.get("own_money_per_year"), "food_places": g.get("food_places_in_walk"),
                        "cafeteria_closed": s in closed, "kinds": [rows[k] for k in FOOD if k in rows]})
    cb = json.loads((D / "chain_backtest.json").read_text())
    fc = json.loads((D / "forecast_consistency.json").read_text())["by_kind"]
    chains = [{"chain": k, "kind": v["category"], "branches": v["branches"], "published": v["published"], "quality": v["quality"],
               "newcomer_forecast": v["median_forecast"], "ratio": v["median_vs_published"],
               "near_school": [b for b in v["branches_detail"] if b["metres"] <= 800]} for k, v in cb.items()]
    # the chains' check per kind: a typical newcomer at the chains' own addresses against their published sales
    import numpy as np
    KIND_CAT = {"Fast food": "fast_food", "Cafe": "cafe", "Restaurant": "restaurant", "Grocery or convenience store": "grocery_food"}
    chain_factor = {}
    for k, cat in KIND_CAT.items():
        r = [c["ratio"] for c in chains if c["kind"] == cat]
        chain_factor[k] = round(float(np.median(r)), 2) if r else None
    for s_ in schools:
        for r in s_["kinds"]:
            f = chain_factor.get(r["kind"])
            r["chain_factor"] = f
            r["after_chain_check"] = round(r["vs_yardstick_worst"] / max(f, 1.0), 2) if f else None
    doc = {"chain_factor": chain_factor, "meta": o["_meta"], "kinds": kinds, "top": top[:12], "lease": lease, "total": o["opportunities_total"],
           "forecast_error": json.loads((ROOT / "data/forecast_error.json").read_text()),
           "schools": schools, "chains": chains, "consistency": fc}
    (D / "pages/opportunities_data.json").write_text(json.dumps(doc, indent=1))
    print("page data:", len(kinds), "kinds,", len(top), "top,", len(lease), "lease,", len(schools), "schools,", len(chains), "chains")


if __name__ == "__main__":
    main()
