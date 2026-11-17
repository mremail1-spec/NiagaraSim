"""Year-by-year check, 2020-2025: does the model's revenue swing by industry line up with the
official count of businesses with employees in Niagara Falls?

Each year is the calibrated 2025 city run with that year's visitor and resident conditions
(data/yearly_conditions.json). A bad year should be followed by fewer businesses in that industry
by the next June, a good year by more.

Usage: python3 -m nfprep.yearly_check
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .businesses import CATEGORY_NAICS
from .loader import DATA_DIR
from .population import BUILD_DIR

ROOT = Path(__file__).resolve().parents[2]
INDUSTRIES = {"722": "Restaurants and food service", "721": "Hotels and motels", "713": "Amusement and gambling",
              "445": "Food stores", "812": "Personal services", "621": "Health offices"}
SHARE = {"domestic": 7.91, "us": 2.21, "overseas": 0.93}      # 2025 city visitors by origin, millions


def count(d: dict, period: str, code: str):
    for k, v in d.get(period, {}).items():
        m = re.search(r"\[(\w+)\]$", k)
        if m and m.group(1) == code:
            return v.get("Total, with employees")
    return None


def spearman(a, b) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> dict:
    cond = json.loads((DATA_DIR / "yearly_conditions.json").read_text())
    counts = json.loads((DATA_DIR / "business_counts_niagara_falls_2020_2026.json").read_text())
    city_path = BUILD_DIR / "city_2025" / "city.json"
    city = json.loads(city_path.read_text())
    cats, b = city["sim_categories"], city["businesses"]
    naics = [CATEGORY_NAICS.get(cats[c], "") if not b["external"][i] else "" for i, c in enumerate(b["sim_category"])]
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    years = [str(y) for y in range(2020, 2026)]
    revenue = {}
    for y in years:
        v = cond["hotel_occupancy"][y] / cond["hotel_occupancy"]["2025"]
        us, ov, res = cond["us"][y], cond["overseas"][y], cond["resident"][y]
        dom = (v * sum(SHARE.values()) - us * SHARE["us"] - ov * SHARE["overseas"]) / SHARE["domestic"]
        out = BUILD_DIR / "city_2025" / f"revenue_{y}.csv"
        subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "flow", "--days", "365",
                        "--beta", str(beta), "--scenario", f"{us},{max(dom, 0.05)},{ov},1.0,{res}", "--businesses", str(out)],
                       check=True, stdout=subprocess.DEVNULL)
        by = {k: 0.0 for k in INDUSTRIES}
        for r in csv.DictReader(open(out)):
            n = naics[int(r["business"])]
            if n in by:
                by[n] += float(r["revenue"])
        revenue[y] = by
        print(f"{y}: domestic {dom:.2f}, US {us:.2f}, overseas {ov:.2f}, residents {res:.2f}")
    rows = []
    for code, name in INDUSTRIES.items():
        for i in range(1, len(years)):
            y0, y1 = years[i - 1], years[i]
            c0 = count(counts, f"{y1}-06", code)
            c1 = count(counts, f"{int(y1) + 1}-06", code)
            if not c0 or not c1:
                continue
            rows.append({"industry": name, "year": y1,
                         "model_revenue_change": round(revenue[y1][code] / revenue[y0][code] - 1, 3),
                         "employer_count_change_next_june": round(c1 / c0 - 1, 3),
                         "count_june_of_year": c0, "count_next_june": c1})
    m = [r["model_revenue_change"] for r in rows]
    c = [r["employer_count_change_next_june"] for r in rows]
    agree = sum(1 for a, b_ in zip(m, c) if (a > 0.02 and b_ > 0) or (a < -0.02 and b_ < 0) or (abs(a) <= 0.02))
    result = {"_meta": {"question": "When the model says an industry's revenue fell (rose) in a year, did the number of businesses with employees in that industry fall (rise) by the next June?",
                        "conditions": "data/yearly_conditions.json"},
              "rank_correlation": round(spearman(m, c), 2), "pearson_correlation": round(float(np.corrcoef(m, c)[0, 1]), 2),
              "direction_agrees": f"{agree} of {len(rows)}", "rows": rows,
              "model_revenue_by_year_musd": {y: {INDUSTRIES[k]: round(v / 1e6, 1) for k, v in r.items()} for y, r in revenue.items()}}
    (ROOT / "docs" / "yearly_check_2020_2025.json").write_text(json.dumps(result, indent=1))
    print(f"\n{'industry':30s}{'year':>6s}{'model revenue':>15s}{'businesses next June':>22s}")
    for r in rows:
        print(f"{r['industry']:30s}{r['year']:>6s}{r['model_revenue_change']:>14.0%}{r['employer_count_change_next_june']:>21.0%}")
    print(f"\nrank correlation {result['rank_correlation']}, direction agrees {result['direction_agrees']}")
    return result


if __name__ == "__main__":
    main()
