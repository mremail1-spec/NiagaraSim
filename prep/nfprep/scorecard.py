"""One scorecard for the simulation: every test against real data the model was not tuned to,
with its pass mark. The pandemic years (2020-2022) are left out: they were not predictable.

Reads the check files in docs/ (re-run each check to refresh them) and writes docs/scorecard.json.
Usage: python3 -m nfprep.scorecard
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
PANDEMIC = {"2020", "2021", "2022"}


def _load(name):
    p = DOCS / name
    return json.loads(p.read_text()) if p.exists() else None


def main() -> dict:
    rows = []

    def add(area, test, score, mark, passed, note=""):
        rows.append({"area": area, "test": test, "score": score, "pass_mark": mark, "result": "pass" if passed else "miss", "note": note})

    t = _load("traffic_layers_check.json")
    if t:
        a = t["with_commuting_and_through_traffic"]
        add("Traffic", "Regional roads, correlation with 2019 counts", a["correlation_regional_roads"], ">= 0.6", a["correlation_regional_roads"] >= 0.6,
            f"{a['sections_tested']} sections; edge counts used as inputs are not scored")
        b25 = t.get("against_2025_regional_counts")
        if b25:
            add("Traffic", "Regional roads, correlation with 2025 counts", b25["correlation_regional_roads"], ">= 0.6",
                b25["correlation_regional_roads"] >= 0.6, "Region's 2025 counts, 69 sections in the city")
        add("Traffic", "Provincial highways, correlation with 2019 counts", a["correlation_highways"], ">= 0.6", a["correlation_highways"] >= 0.6)
        add("Traffic", "Regional roads, median share of counted traffic", f"{a['median_share_explained_regional_pct']}%", ">= 50%",
            a["median_share_explained_regional_pct"] >= 50, "school trips and trips between stops are not simulated yet")
    f = _load("foot_traffic_check.json")
    if f:
        add("Foot traffic", "Model walkers higher on the City's sidewalk sweeper routes (ranking)", f["ranking_score"], ">= 0.7",
            f["ranking_score"] >= 0.7, f"{f['top_100_walking_nodes_on_routes']} of the model's 100 busiest walking spots are on a route "
            f"({f['share_of_nodes_on_routes']:.0%} of road nodes are)")
    pk = _load("parking_check.json")
    if pk:
        st = next(v for k, v in pk["highway_420_2003"]["sections"].items() if k.startswith("STANLEY"))
        share = st["visitor_share_that_fits"]
        add("Traffic", "Highway 420 at Stanley Ave, visitor share from the 2003 (SARS) dip", share, "0.08-0.22 (model 0.15)",
            0.08 <= share <= 0.22, "Niagara Parks Falls lot x0.813 that year; one year of partly estimated MTO counts")
    tt = _load("travel_times_check.json")
    if tt:
        noon = tt["summary"]["as_built"]["Tue Jul noon"]
        add("Traffic", "Trip times, correlation with TomTom typical times (40 trips, Tuesday noon)", noon["correlation"], ">= 0.8",
            noon["correlation"] >= 0.8, f"routes match: model distance x{tt['summary']['distance_ratio_model_to_tomtom']} of TomTom's")
        add("Traffic", "Trip times, model against TomTom (median ratio, Tuesday noon)", noon["median_ratio_model_to_tomtom"], "0.8-1.25",
            0.8 <= noon["median_ratio_model_to_tomtom"] <= 1.25, "model about 1.5x fast; fixing it in routing hurts the count fit")
    u = _load("unit_sales_check.json")
    if u:
        r = u["summary"]["median_ratio_model_to_published"]
        add("Sales at a site", "Chain branches, model median against published sales per location", r, "0.67-1.5", 0.67 <= r <= 1.5,
            "4 chains; a tourist city can sit above national averages")
        n = u["summary"]["branches_above_brand_best"]
        add("Sales at a site", "Branches above the brand's best unit in Canada", n, "0", n == 0, "Tim Hortons in the Tourist Core")
    inv = _load("inventory_check.json")
    if inv:
        sz = inv["sizes"]
        add("Sales at a site", "Model revenue against surveyed employee band (Employment Inventory 2025)", sz["weighted_rank_correlation"],
            ">= 0.3", sz["weighted_rank_correlation"] >= 0.3, f"{sz['matched_businesses']} businesses matched")
        cl = inv["closures_2023_2025"]["scores"]
        add("Closures", "2023-2025 closures: model location forecast, skill over each kind's rate", cl["model_location"]["skill_vs_kind_rate"],
            "> 0.02", cl["model_location"]["skill_vs_kind_rate"] > 0.02,
            f"age, size and floor area reach {cl['facts']['skill_vs_kind_rate']} (ranking {cl['facts']['ranking']}); "
            f"{inv['closures_2023_2025']['closed']} closures of {inv['closures_2023_2025']['businesses']}")
    cs = _load("closure_signals_check.json")
    if cs and "pooled" in cs:
        m = cs["pooled"]["models"]["facts + neighbourhood"]
        add("Closures", "Two-year closure chance (kind, age, size, floor area, vacancy nearby), pooled 2016-2025, cross-validated ranking",
            m["ranking"], ">= 0.7", m["ranking"] >= 0.7,
            f"skill over each kind's rate {m['skill_vs_kind_rate']}; {cs['pooled']['closed']} closures; shown for new businesses on the page")
    o = _load("outlook_check.json")
    if o:
        inside = o["inside_band"]["us_overnight"]
        k, n = map(int, inside.split(" of "))
        add("Outlooks", "US visitors next 12 months, inside the 10-90 band (pandemic left out)", inside, ">= 70%", k / n >= 0.7)
    y = _load("yearly_check_2020_2025.json")
    if y:
        rr = [r for r in y["rows"] if r["year"] not in PANDEMIC]
        agree = sum(np.sign(r["model_revenue_change"]) == np.sign(r["employer_count_change_next_june"]) for r in rr)
        c = float(np.corrcoef([r["model_revenue_change"] for r in rr], [r["employer_count_change_next_june"] for r in rr])[0, 1])
        add("Business counts", "Industry revenue direction against next June's employer count, 2023-2025", f"{agree} of {len(rr)}", ">= 70%",
            agree / len(rr) >= 0.7, f"correlation {c:.2f}")
    cc = _load("conditions_check.json")
    if cc:
        obs = cc["observed"]["st_catharines_niagara_closing_rate"][2]
        add("Business counts", "2025 rise in closings: conditions logic (rates, wages, US visitors) predicts the direction",
            f"x{cc['model_closure_odds_multiplier_2025_vs_2024']} vs x{obs}", "direction right", cc["direction_right"],
            "St. Catharines-Niagara closing rate 0.540 to 0.591; Ontario exits x" + str(cc["observed"]["ontario_exit_rate"][2]) + "; model about half the size")
    # retired: five-year closure chance from 38 map edits (2021-2026), superseded by the Employment Inventory test
    passed = sum(r["result"] == "pass" for r in rows)
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "pandemic_years_left_out": sorted(PANDEMIC)},
           "passed": f"{passed} of {len(rows)}", "tests": rows,
           "calibrated_not_tests": ["store sales by type (7 of 8 within 1%)", "hotel occupancy by month 2025", "Niagara Parks revenue split",
                                    "casino revenue", "median driving trip 5.5 km", "attraction attendance"]}
    (DOCS / "scorecard.json").write_text(json.dumps(doc, indent=1))
    for r in rows:
        print(f"{r['result']:5s} {r['area']:16s} {r['test'][:80]:80s} {str(r['score']):>10s}  (mark {r['pass_mark']})")
    print(f"passed {passed} of {len(rows)}")
    return doc


if __name__ == "__main__":
    main()
