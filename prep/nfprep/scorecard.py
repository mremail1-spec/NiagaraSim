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
        add("Traffic", "Provincial highways, correlation with 2019 counts", a["correlation_highways"], ">= 0.6", a["correlation_highways"] >= 0.6)
        add("Traffic", "Regional roads, median share of counted traffic", f"{a['median_share_explained_regional_pct']}%", ">= 50%",
            a["median_share_explained_regional_pct"] >= 50, "school trips and trips between stops are not simulated yet")
    u = _load("unit_sales_check.json")
    if u:
        r = u["summary"]["median_ratio_model_to_published"]
        add("Sales at a site", "Chain branches, model median against published sales per location", r, "0.67-1.5", 0.67 <= r <= 1.5,
            "4 chains; a tourist city can sit above national averages")
        n = u["summary"]["branches_above_brand_best"]
        add("Sales at a site", "Branches above the brand's best unit in Canada", n, "0", n == 0, "Tim Hortons in the Tourist Core")
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
    s = _load("survival_rate_check.json")
    if s:
        yrs = [x for x in s["years"] if x not in PANDEMIC]
        add("Business counts", "St. Catharines-Niagara closings 2023-2025, rise the model sees", "not seen",
            "direction right", False, "closings rose in 2025 (0.54 to 0.59); the model's revenue did not fall")
    b = _load("backtest_2021_2026.json")
    cr = _load("closure_risk_check.json")
    if cr:
        sk = cr["brier_skill_vs_overall_rate"]["model"]
        add("Closures", "Five-year closure chance, skill over each kind's average (2021-2026, includes pandemic recovery)", sk, "> 0.05",
            sk > 0.05, "reported, not a pandemic-free test")
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
