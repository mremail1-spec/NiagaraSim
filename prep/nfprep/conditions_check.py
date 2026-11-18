"""Does the Conditions panel's logic see 2025's rise in closings?

Feeds the real 2024 and 2025 settings (prime rate, minimum wage, US visitors; Bank of Canada,
Ontario, Statistics Canada) through the same squeeze and closure-odds formula as the page, for each
kind of business at its city-wide mix of resident, Canadian, US and overseas customers (calibrated
flow run), and compares the predicted change in closures with St. Catharines-Niagara's closing rate
(2024 0.540 -> 2025 0.591 a year, Statistics Canada 33-10-0270) and Ontario's exits.

Usage: python3 -m nfprep.conditions_check   (writes docs/conditions_check.json)
"""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]


def main() -> dict:
    P = json.loads((DATA_DIR / "policy_levers.json").read_text())
    ind = json.loads((DATA_DIR / "economic_indicators.json").read_text())
    mi = json.loads((DATA_DIR / "monthly_indicators.json").read_text())
    yr = lambda s, y: sum(v for k, v in mi[s].items() if k.startswith(y))
    us_change = yr("us_overnight", "2025") / yr("us_overnight", "2024")
    s24 = {"prime": ind["prime_rate_pct"]["2024"], "mw": ind["minimum_wage_cad_year_weighted"]["2024"]}
    s25 = {"prime": ind["prime_rate_pct"]["2025"], "mw": ind["minimum_wage_cad_year_weighted"]["2025"]}
    city = json.loads((ROOT / "build/city_2025/city.json").read_text())
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = ROOT / "build/city_2025/cond_rev.csv"
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(ROOT / "build/city_2025/city.json"), "--mode", "flow",
                    "--days", "365", "--beta", str(beta), "--businesses", str(out)], check=True, stdout=subprocess.DEVNULL)
    rows = list(csv.DictReader(open(out)))
    b, cats = city["businesses"], city["sim_categories"]
    menu = {m["sim_category"]: m for m in city["build_menu"]}
    # US share of visitor spending: from the tourism segments (persons x spend x days)
    segs = city["tourism"]["segments"]
    w = {s["origin"]: 0.0 for s in segs}
    for s in segs:
        w[s["origin"]] += s["persons_per_year"] * s.get("spend_per_person_day", 1.0)
    us_share_of_visitors = w.get("us", 0) / sum(w.values())
    by_kind, total_w, odds = {}, 0.0, 0.0
    for r in rows:
        i = int(r["business"]); c = b["sim_category"][i]
        if c not in menu:
            continue
        m = menu[c]
        rev, tour = float(r["revenue"]), float(r["tourist_revenue"])
        if rev <= 0:
            continue
        margin = 1 - (m["cost_of_sales_pct"] + m["wages_pct"]) / 100
        linked = P["minimum_wage"]["wage_share_linked"].get(m["naics"], P["minimum_wage"]["wage_share_linked"]["default"])
        cost = P["interest_rate"]["business_debt_share_of_revenue"] * (s25["prime"] - s24["prime"]) / 100 \
            + m["wages_pct"] / 100 * linked * (s25["mw"] / s24["mw"] - 1)
        lost = (tour / rev) * us_share_of_visitors * (1 - us_change)
        resid = (1 - tour / rev) * (-P["interest_rate"]["resident_spending_per_point"]) * (s25["prime"] - s24["prime"])
        squeeze = cost + (lost + resid) * margin
        k = cats[c]
        d = by_kind.setdefault(k, [0.0, 0.0])
        d[0] += squeeze; d[1] += 1
        odds += np.exp(P["gamma"] * squeeze); total_w += 1
    mult = odds / total_w
    bd = json.loads((DATA_DIR / "business_dynamics_statcan.json").read_text())
    def rate(series, y):
        a, c = series["Business sector industries | Active businesses"], series["Business sector industries | Closing businesses"]
        ms = [m for m in a if m.startswith(y) and m in c]
        return 12 * float(np.mean([c[m] / a[m] for m in ms]))
    cma24, cma25 = rate(bd["st_catharines_niagara_cma"], "2024"), rate(bd["st_catharines_niagara_cma"], "2025")
    ont = bd["ontario"]
    ex = lambda y: 12 * float(np.mean([ont["Business sector industries | Exits"][m] / ont["Business sector industries | Active businesses"][m]
                                       for m in ont["Business sector industries | Exits"] if m.startswith(y)]))
    res = {"_meta": {"what": __doc__.strip().split("\n\n")[0]},
           "settings": {"2024": s24, "2025": s25, "us_overnight_2025_vs_2024": round(us_change, 3),
                        "us_share_of_visitor_spending": round(us_share_of_visitors, 3)},
           "model_closure_odds_multiplier_2025_vs_2024": round(float(mult), 3),
           "mean_squeeze_by_kind_pct_of_sales": {k: round(100 * v[0] / v[1], 2) for k, v in sorted(by_kind.items())},
           "observed": {"st_catharines_niagara_closing_rate": [round(cma24, 3), round(cma25, 3), round(cma25 / cma24, 3)],
                        "ontario_exit_rate": [round(ex("2024"), 3), round(ex("2025"), 3), round(ex("2025") / ex("2024"), 3)]},
           "direction_right": bool(mult > 1) == bool(cma25 > cma24)}
    (ROOT / "docs" / "conditions_check.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main()
