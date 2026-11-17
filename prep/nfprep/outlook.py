"""Outlook ranges from the monthly indicators (data/monthly_indicators.json).

Writes data/scenario_ranges.json and docs/outlook_check.json.

  calibrated  the 2025 year the model is calibrated to (every multiplier 1).
  expected    2025 moved by the latest months: same months this year against last year
              (January-August 2026 against January-August 2025 at the time of writing).
  low/high    expected times the 10th and 90th percentiles of the change over the next twelve
              months in ordinary years (pandemic window left out). The low end of the US band is never
              above the worst political year on record outside the pandemic (2025, trade tensions),
              because a decision of that kind can come back.

Checked out of sample: for every August from 2012 on (pandemic window left out), the band is
built only from months before it, and the next twelve months are compared with it.
"""
import json, math
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA, DOCS = ROOT / "data", ROOT / "docs"
PANDEMIC = ("2020-02", "2022-12")          # a twelve-month change touching these months is left out
POLITICAL_FLOOR = {"us": ("us_overnight", 2025)}
# SEED: bands with no monthly series behind them stay judgement calls (see _meta.seed).
SEED = {"domestic": (0.93, 1.07), "base_stays": (0.85, 1.05), "resident": (0.97, 1.03)}


def load():
    return json.loads((DATA / "monthly_indicators.json").read_text())


def months_of(d, s):
    return sorted(d[s])


def trailing(d, s, ms, i, n=12):
    return sum(d[s][ms[j]] for j in range(i - n + 1, i + 1))


def changes(d, s, until=None):
    """Twelve-month change of the trailing twelve-month total, by end month of the base window."""
    ms = months_of(d, s)
    out = []
    for i in range(11, len(ms) - 12):
        a, b = ms[i - 11], ms[i + 12]
        if until and b >= until:
            continue
        if not (b < PANDEMIC[0] or a > PANDEMIC[1]):
            continue
        out.append((ms[i], trailing(d, s, ms, i + 12) / trailing(d, s, ms, i)))
    return out


def same_months(d, s, year, last):
    m = [f"{last[:4]}-{k:02d}" for k in range(1, int(last[5:]) + 1)]
    now = sum(d[s][x] for x in m)
    before = sum(d[s][f"{int(x[:4]) - 1}{x[4:]}"] for x in m)
    return now / before


def year_change(d, s, y):
    return sum(d[s][f"{y}-{k:02d}"] for k in range(1, 13)) / sum(d[s][f"{y - 1}-{k:02d}"] for k in range(1, 13))


def band(d, s, until=None):
    r = [v for _, v in changes(d, s, until)]
    return float(np.percentile(r, 10)), float(np.percentile(r, 90)), len(r)


def check(d, s):
    """Out of sample: the band from data before each August against the following twelve months."""
    ms = months_of(d, s)
    rows = []
    for i, m in enumerate(ms):
        if m[5:] != "08" or i + 12 >= len(ms) or m < "2012":
            continue
        if not (ms[i + 12] < PANDEMIC[0] or ms[i - 11] > PANDEMIC[1]):
            continue
        lo, hi, n = band(d, s, until=ms[i + 1])
        got = trailing(d, s, ms, i + 12) / trailing(d, s, ms, i)
        rows.append({"base_window_ends": m, "low": round(lo, 3), "high": round(hi, 3), "actual": round(got, 3),
                     "inside": lo <= got <= hi})
    return rows


def main():
    d = load()
    last = max(d["us_overnight"])
    out = {"calibrated": dict(us=1.0, domestic=1.0, overseas=1.0, base_stays=1.0, resident=1.0)}
    # US: overnight visitors carry most of the spending, so they set the outlook.
    us_now = same_months(d, "us_overnight", int(last[:4]), last)
    us_lo, us_hi, n_us = band(d, "us_overnight")
    floor = year_change(d, *POLITICAL_FLOOR["us"])
    us_lo = min(us_lo, floor)
    # Canadians who stopped crossing into the US in 2025 spent at home instead; the domestic
    # outlook stays at 2025 while their US trips stay down, and its low end is them going back.
    back = same_months(d, "canadians_back_from_us", int(last[:4]), last)
    exp = dict(us=round(us_now, 3), domestic=1.0, overseas=1.0, base_stays=1.0, resident=1.0)
    out["expected"] = exp
    out["low"] = dict(us=round(us_now * us_lo, 3), domestic=SEED["domestic"][0], overseas=0.85,
                      base_stays=SEED["base_stays"][0], resident=SEED["resident"][0])
    out["high"] = dict(us=round(us_now * us_hi, 3), domestic=SEED["domestic"][1], overseas=1.10,
                       base_stays=SEED["base_stays"][1], resident=SEED["resident"][1])
    checks = {s: check(d, s) for s in ["us_overnight", "us_sameday", "canadians_back_from_us"]}
    hits = {s: f"{sum(r['inside'] for r in v)} of {len(v)}" for s, v in checks.items()}
    lbl = f"January-{['','January','February','March','April','May','June','July','August','September','October','November','December'][int(last[5:])]} {last[:4]}"
    meta = {
        "purpose": "Forward simulations give a range, because political events and decisions cannot be predicted. Each outlook multiplies the calibrated year (2025). Written by prep/nfprep/outlook.py from data/monthly_indicators.json.",
        "latest_month": last,
        "labels": {"calibrated": "2025, calibrated", "expected": f"Next 12 months, from {lbl}",
                   "low": "Next 12 months, low", "high": "Next 12 months, high"},
        "basis": {
            "us": f"US overnight visitors through the Niagara ports, {lbl} against the same months a year earlier: {us_now:.3f}. Band: 10th-90th percentile of the twelve-month change in {n_us} ordinary months ({band(d,'us_overnight')[0]:.3f}-{us_hi:.3f}), with the low end set no higher than 2025's political drop ({floor:.3f}).",
            "domestic": f"Canadians returning from the US through Niagara, {lbl}: {back:.3f} of a year earlier (2025 was {year_change(d,'canadians_back_from_us',2025):.3f} of 2024). While those trips stay down, the domestic outlook stays at 2025; the low end is some of them returning to US trips.",
            "overseas": "The Niagara land ports see few overseas visitors (most land at Toronto Pearson), so the overseas band stays a judgement call.",
            "base_stays": "Hotel room nights outside leisure travel. Federal contracts housing asylum seekers ended in 2024-2025; a government decision could add or remove such stays again.",
            "resident": "Household spending moves a few percent with jobs and prices."},
        "seed": "Domestic, overseas, base-stay and resident bands are judgement calls: no monthly series measures them for Niagara Falls. Widen or narrow them in prep/nfprep/outlook.py (SEED).",
        "out_of_sample": hits}
    (DATA / "scenario_ranges.json").write_text(json.dumps({"_meta": meta, **out}, indent=1))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "outlook_check.json").write_text(json.dumps({"latest_month": last, "outlooks": out, "checks": checks,
                                                         "inside_band": hits}, indent=1))
    print(json.dumps(out, indent=1))
    print("inside the 10-90 band, out of sample:", hits)


if __name__ == "__main__":
    main()
