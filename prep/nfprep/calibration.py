"""Government-based sales targets by store type for Niagara Falls.

Supply side: Ontario sales per employee in a store type, times the employees the business
register shows for that store type in the city. Per-capita side: Ontario sales per resident
times the city's population (what residents alone would generate if nothing leaked in or out).
"""
from __future__ import annotations

import json

from .loader import DATA_DIR, Pack

BANDS = ["nf_1_4", "nf_5_9", "nf_10_19", "nf_20_49", "nf_50_99", "nf_100_plus"]
# business group -> register rows it covers (the map files building-supply stores with home goods)
GROUPS = {"445": ["445"], "449": ["449", "444"], "455": ["455"], "456": ["456"], "457": ["457"],
          "458": ["458"], "459": ["459"], "722": ["722"], "441": ["441"]}
CALIBRATED = ["445", "449", "455", "456", "457", "458", "459", "722"]


def sales_targets(pack: Pack, population: int) -> dict:
    bench = json.loads((DATA_DIR / "calibration_benchmarks_ontario_2025.json").read_text())
    reg = {r["naics"]: r for r in pack.tables["business_revenue_benchmarks.csv"]}
    mid = bench["_meta"]["band_midpoints_employees"]
    out = {}
    for group, rows in GROUPS.items():
        employees = big = sales = ontario_emp = 0.0
        for n in rows:
            for b in BANDS:
                k = float(reg[n][b] or 0)
                employees += k * mid[b]
                big += k * mid[b] if b == "nf_100_plus" else 0
            sales += bench["ontario_sales_thousands_2025"][n] * 1000
            ontario_emp += bench["ontario_employees_2025"][n]
        out[group] = {"city_employees_est": round(employees), "share_in_100_plus_firms": round(big / max(employees, 1), 3),
                      "ontario_sales_per_employee": round(sales / ontario_emp),
                      "supply_side_sales": round(sales / ontario_emp * employees),
                      "per_capita_sales": round(sales / bench["ontario_population_2025"] * population),
                      "calibrate": group in CALIBRATED}
    return out
