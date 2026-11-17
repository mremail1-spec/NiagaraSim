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
    jan_jul = sum(shape[m] * DAYS[m] for m in range(7)) / sum(DAYS[:7])
    occ = market["occupancy"]
    k = (occ["january_to_july_2025"] - occ["march_2025"]) / (jan_jul - shape[2])
    base = occ["march_2025"] - k * shape[2]
    fitted = [base + k * s for s in shape]
    rate = market["average_daily_rate_cad"]
    slope = (rate["january_to_july_2025"] - rate["march_2025"]) / (occ["january_to_july_2025"] - occ["march_2025"])
    rates = [round(max(100.0, rate["march_2025"] + slope * (o - occ["march_2025"])), 2) for o in fitted]
    result = {
        "_meta": {"fitted_to": "data/hotel_market_2025.json: March 2025 occupancy 47.4% and January-July 2025 average 59.5%; "
                               "room rate $149.66 in March and $206.28 for January-July.",
                  "check": "Full-year occupancy is not fitted. Published: about 63%.",
                  "rate_rule": "Room rate rises in a straight line with occupancy through the two published points, floor $100."},
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
