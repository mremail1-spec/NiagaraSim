"""Fit hotel demand to the published 2025 occupancy and room rates (data/hotel_market_2025.json).

Occupancy each month = base + k x (occupancy from the travel survey's visitors alone).
`base` is rooms filled every night by stays the survey does not count; `k` is rooms per party.
The two are solved from the two public figures: March, and the January-July average. The
full-year figure is then a check, not an input.

Usage: python3 -m nfprep.hotel_fit   (needs the engine built)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .loader import DATA_DIR
from .population import BUILD_DIR

ROOT = Path(__file__).resolve().parents[2]
DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def main() -> dict:
    market = json.loads((DATA_DIR / "hotel_market_2025.json").read_text())
    out = DATA_DIR / "calibrated_hotels.json"
    if out.exists():
        out.unlink()                                    # measure the survey-only shape first
    subprocess.run(["python3", "-m", "nfprep.build", "2025", "1"], check=True, cwd=ROOT / "prep", stdout=subprocess.DEVNULL)
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    run = subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(BUILD_DIR / "city_2025/city.json"),
                          "--mode", "flow", "--days", "365", "--beta", str(beta)], check=True, capture_output=True, text=True)
    shape = json.loads(run.stdout)["tourism"]["hotel_occupancy_by_month"]
    import numpy as np
    cbre = json.loads((DATA_DIR / "hotel_monthly_cbre.json").read_text())
    occ = market["occupancy"]
    # 2025 targets: (months covered, published occupancy, published room rate or None)
    targets = [([0], cbre["monthly"]["2025-01"]["occupancy"], cbre["monthly"]["2025-01"]["adr"]),
               ([2], cbre["monthly"]["2025-03"]["occupancy"], cbre["monthly"]["2025-03"]["adr"]),
               ([10], cbre["monthly"]["2025-11"]["occupancy"], cbre["monthly"]["2025-11"]["adr"]),
               ([0, 1, 2], cbre["year_to_date"]["2025-01..03"]["occupancy"], cbre["year_to_date"]["2025-01..03"]["adr"]),
               (list(range(7)), occ["january_to_july_2025"], market["average_daily_rate_cad"]["january_to_july_2025"]),
               (list(range(11)), cbre["year_to_date"]["2025-01..11"]["occupancy"], cbre["year_to_date"]["2025-01..11"]["adr"])]
    # occupancy_m = base + k x shape_m, least squares over the six figures (averages weighted by days)
    A, y = [], []
    for months, o, _ in targets:
        d = sum(DAYS[m] for m in months)
        A.append([1.0, sum(shape[m] * DAYS[m] for m in months) / d])
        y.append(o)
    (base, k), *_ = np.linalg.lstsq(np.array(A), np.array(y), rcond=None)
    base, k = float(base), float(k)
    fitted = [min(0.97, base + k * s) for s in shape]
    # room rate_m = a + b x occupancy_m; a period's rate is weighted by the room nights sold in it
    def period_rate(a, b, months):
        nights = [fitted[m] * DAYS[m] for m in months]
        return sum(n * (a + b * fitted[m]) for n, m in zip(nights, months)) / sum(nights)
    best = None
    for a in np.arange(-100, 200, 1.0):
        for b in np.arange(50, 600, 2.0):
            err = sum((period_rate(a, b, ms) - r) ** 2 for ms, _, r in targets)
            if best is None or err < best[0]:
                best = (err, a, b)
    _, ra, rb = best
    rates = [round(max(90.0, ra + rb * o), 2) for o in fitted]
    check = [{"period": "+".join(str(m + 1) for m in ms), "published_occupancy": o,
              "model_occupancy": round(sum(fitted[m] * DAYS[m] for m in ms) / sum(DAYS[m] for m in ms), 3),
              "published_rate": r, "model_rate": round(period_rate(ra, rb, ms), 2)} for ms, o, r in targets]
    jan_jul = None
    result = {
        "_meta": {"fitted_to": "Six published 2025 figures: CBRE January, March and November months, January-March and "
                               "January-November year to date (data/hotel_monthly_cbre.json), and January-July (data/hotel_market_2025.json). "
                               "Least squares for occupancy; room rate fitted to the same six periods.",
                  "rate_rule": "Room rate = a + b x occupancy, floor $90."},
        "fit_check": check, "room_rate_line": {"a": ra, "b": rb},
        "hotel": {"rooms_per_party": round(k, 4), "base_share": round(base, 4), "rate_by_month": rates},
        "survey_only_occupancy_by_month": shape,
        "fitted_occupancy_by_month_before_capacity_limits": [round(x, 3) for x in fitted],
        "fitted_full_year_before_capacity_limits": round(sum(f * d for f, d in zip(fitted, DAYS)) / 365, 3),
    }
    out.write_text(json.dumps(result, indent=1))
    subprocess.run(["python3", "-m", "nfprep.build", "2025", "1"], check=True, cwd=ROOT / "prep", stdout=subprocess.DEVNULL)
    for key, v in result.items():
        if key != "_meta":
            print(key, v)
    return result


if __name__ == "__main__":
    main()
