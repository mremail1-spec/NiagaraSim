"""Cash balances and closures: why businesses fail when cash runs out, not when revenue dips.

Every business gets a yearly profit and loss from the model's revenue for that year (run with the
year's real conditions) and its industry's cost structure (Statistics Canada benchmarks):
  - goods and part of wages move with sales; rent, utilities and the rest of wages are fixed;
  - wage costs rise with the real minimum wage (Ontario, deflated by Ontario's consumer price index);
  - debt costs follow the Bank of Canada prime rate;
  - 2020-2021: wage and rent subsidies when revenue fell more than 30%; a $60,000 CEBA loan in 2020,
    $40,000 repaid by January 2024.
Owners draw the normal profit; the cash balance takes everything else. A business closes when its
cash runs out, and some close every year for other reasons (retirement, a lease). New businesses
open each year, more when the industry's revenue is up.

Each business is run many times with different margins, debts and cash buffers (Monte Carlo).

Usage: python3 -m nfprep.survival
"""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import numpy as np

from .businesses import CATEGORY_NAICS
from .loader import DATA_DIR
from .population import BUILD_DIR
from .yearly_check import INDUSTRIES, SHARE, count

ROOT = Path(__file__).resolve().parents[2]
YEARS = [str(y) for y in range(2019, 2026)]
DRAWS = 300
# SEEDs, each from research or a stated assumption
MARGIN_SPREAD = 0.06          # spread of normal-year profit margins within an industry
DEBT_RATIO = 0.20             # business debt as a share of normal-year revenue
BASE_EXIT = 0.07              # yearly closures for reasons other than running out of cash
BASE_ENTRY = 0.09             # yearly openings as a share of businesses, in a normal year
WAGE_LINKED_TO_MINIMUM = {"722": 0.6, "721": 0.5, "713": 0.5, "445": 0.5, "812": 0.4, "621": 0.15}


def scenario(cond: dict, pop: dict, y: str) -> str:
    v = cond["hotel_occupancy"][y] / cond["hotel_occupancy"]["2025"]
    us, ov = cond["us"][y], cond["overseas"][y]
    res = cond["resident"][y] * pop[y] / pop["2025"]
    dom = (v * sum(SHARE.values()) - us * SHARE["us"] - ov * SHARE["overseas"]) / SHARE["domestic"]
    return f"{us},{max(dom, 0.05)},{ov},1.0,{res}"


def yearly_revenue(city_path: Path, tag: str) -> dict[str, np.ndarray]:
    cond = json.loads((DATA_DIR / "yearly_conditions.json").read_text())
    pop = json.loads((DATA_DIR / "sim_parameters.json").read_text())["residents"]["population"]["statcan_estimates_by_year"]
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = {}
    for y in YEARS:
        f = BUILD_DIR / f"survival_{tag}_{y}.csv"
        subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "flow", "--days", "365",
                        "--beta", str(beta), "--scenario", scenario(cond, pop, y), "--businesses", str(f)],
                       check=True, stdout=subprocess.DEVNULL)
        rows = list(csv.DictReader(open(f)))
        arr = np.zeros(max(int(r["business"]) for r in rows) + 1)
        for r in rows:
            arr[int(r["business"])] = float(r["revenue"])
        out[y] = arr
    return out


def simulate(revenue: dict, naics: list[str], buffer_months: float, seed: int = 1):
    """Returns closed[year] -> (n_business, DRAWS) booleans of financial closures in that year."""
    ind = json.loads((DATA_DIR / "economic_indicators.json").read_text())
    bench = {r["naics"]: r for r in csv.DictReader(open(DATA_DIR / "business_revenue_benchmarks.csv"))}
    rng = np.random.default_rng(seed)
    n = len(naics)
    f = lambda k: np.array([float(bench.get(x, {}).get(k) or 0) / 100 for x in naics])
    margin = f("net_profit_pct")[:, None] + rng.normal(0, MARGIN_SPREAD, (n, DRAWS))
    wages = (f("wages_benefits_pct") + f("labour_commissions_pct"))[:, None]
    fixed_share = (f("rent_pct") + f("utilities_pct"))[:, None] + 0.4 * wages
    linked = np.array([WAGE_LINKED_TO_MINIMUM.get(x, 0.3) for x in naics])[:, None]
    r0 = revenue["2019"][:n][:, None]
    variable = 1 - margin - fixed_share
    monthly_costs = r0 * (1 - margin) / 12
    cash = buffer_months * monthly_costs * rng.lognormal(-0.32, 0.8, (n, DRAWS))   # mean = buffer_months
    debt = DEBT_RATIO * r0 * rng.lognormal(-0.32, 0.8, (n, DRAWS))
    cpi, mw, prime = ind["cpi_ontario_2002_100"], ind["minimum_wage_cad_year_weighted"], ind["prime_rate_pct"]
    real_mw = lambda y: (mw[y] / cpi[y]) / (mw["2019"] / cpi["2019"])
    alive = np.ones((n, DRAWS), bool)
    closed = {}
    for y in YEARS[1:]:
        r = revenue[y][:n][:, None]
        profit = r * (1 - variable) - fixed_share * r0
        profit -= linked * wages * r0 * (real_mw(y) - 1)
        profit -= debt * (prime[y] - prime["2019"]) / 100
        sub = ind["support"]["wage_and_rent_subsidy_share_of_fixed_costs"].get(y, 0.0)
        if sub:
            hit = r < (1 - ind["support"]["applies_if_revenue_below_normal_by"]) * r0
            profit += np.where(hit, sub * fixed_share * r0, 0.0)
        cash += profit - margin * r0                          # owners draw the normal profit
        cash += ind["support"]["ceba_loan_cad"].get(y, 0) - ind["support"]["ceba_repayment_cad"].get(y, 0)
        broke = alive & (cash < 0)
        closed[y] = broke
        alive &= ~broke
    return closed


def industry_counts(revenue: dict, naics: list[str], closed: dict, counts: dict, dormant_below: float, entry_sensitivity: float,
                    seed: int = 2):
    """Simulated businesses-with-employees per industry each June, anchored to June 2020.

    A business whose revenue in a year falls below `dormant_below` of normal (threshold varies by
    business) lays off its staff and drops out of the count without closing; it returns when its
    revenue recovers. The June count of year Y uses year Y's revenue (2025's for June 2026).
    """
    rng = np.random.default_rng(seed)
    out = {}
    for code in INDUSTRIES:
        idx = [i for i, x in enumerate(naics) if x == code]
        if not idx:
            continue
        start = count(counts, "2020-06", code)
        n = float(len(idx))
        r0 = revenue["2019"][idx]
        tau = np.clip(dormant_below + rng.normal(0, 0.12, (len(idx), DRAWS)), 0.05, 0.95)

        def active(y):          # share of surviving businesses with staff on payroll in year y
            ratio = (revenue[y][idx] / np.maximum(r0, 1))[:, None]
            return float((ratio >= tau).mean())

        level, series = n, {}
        base = active("2020")
        for y in YEARS[1:]:
            fin = closed[y][idx].mean(axis=1).sum() * (level / n)
            ratio = revenue[y][idx].sum() / r0.sum()
            entries = BASE_ENTRY * level * np.clip(1 + entry_sensitivity * (ratio - 1), 0, 3)
            level = level - fin - BASE_EXIT * level + entries
            june = str(int(y) + 1)
            series[june] = round(start * (level / n) * active(min(june, "2025")) / max(base, 1e-9), 1)
        out[code] = series
    return out


def main() -> dict:
    counts = json.loads((DATA_DIR / "business_counts_niagara_falls_2020_2026.json").read_text())
    city_path = BUILD_DIR / "city_2025" / "city.json"
    city = json.loads(city_path.read_text())
    cats, b = city["sim_categories"], city["businesses"]
    keep = [i for i in range(len(b["name"])) if not b["external"][i] and b["employer"][i]]
    naics_all = [CATEGORY_NAICS.get(cats[c], "") for c in b["sim_category"]]
    revenue = yearly_revenue(city_path, "city")
    rev = {y: v[keep] for y, v in revenue.items()}
    naics = [naics_all[i] for i in keep]
    actual = {code: {y: count(counts, f"{y}-06", code) for y in ["2021", "2022", "2023", "2024", "2025", "2026"]} for code in INDUSTRIES}

    def error(series):
        e = []
        for code, s in series.items():
            for y, v in s.items():
                if actual[code].get(y):
                    e.append(np.log(v / actual[code][y]))
        return float(np.mean(np.square(e)))

    best = None
    for months in (1, 2, 3, 4, 6, 9):
        closed = simulate(rev, naics, months)
        for tau in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7):
            for s in (0.0, 0.2, 0.5):
                err = error(industry_counts(rev, naics, closed, counts, tau, s))
                if best is None or err < best[0]:
                    best = (err, months, tau, s, closed)
    err, months, tau, s, closed = best
    series = industry_counts(rev, naics, closed, counts, tau, s)
    # without the cash model, for comparison: closures only at the base rate
    none = {y: np.zeros_like(c) for y, c in closed.items()}
    plain = industry_counts(rev, naics, none, counts, 0.0, s)
    result = {"_meta": {"what": "Businesses with employees by industry each June: simulated with cash balances, against Statistics Canada counts.",
                        "fitted": {"cash_buffer_months": months, "dormant_below_share_of_normal_revenue": tau, "entry_sensitivity": s},
                        "fixed": {"base_exit": BASE_EXIT, "base_entry": BASE_ENTRY, "debt_ratio": DEBT_RATIO, "margin_spread": MARGIN_SPREAD}},
              "rms_log_error_with_cash_model": round(err ** 0.5, 3), "rms_log_error_base_rate_only": round(error(plain) ** 0.5, 3),
              "industries": {INDUSTRIES[c]: {"simulated": series[c], "actual": actual[c], "base_rate_only": plain[c]} for c in series}}
    (ROOT / "docs" / "survival_check_2020_2026.json").write_text(json.dumps(result, indent=1))
    print(f"fitted: cash buffer {months} months, dormant below {tau:.0%} of normal revenue, entry sensitivity {s}; "
          f"error {err ** 0.5:.3f} (closures at the base rate only, no dormancy: {error(plain) ** 0.5:.3f})")
    for c in series:
        print(f"{INDUSTRIES[c]:30s} sim  " + " ".join(f"{series[c][y]:6.0f}" for y in sorted(series[c])))
        print(f"{'':30s} real " + " ".join(f"{actual[c][y] or 0:6.0f}" for y in sorted(series[c])))
    return result


if __name__ == "__main__":
    main()


def closure_test(buffer_months: float = 3.0) -> dict:
    """Individual businesses: does the chance of running out of cash in 2021-2025 rank the 2021
    businesses that really closed by July 2026 (docs/backtest_2021_2026.json)?"""
    from .backtest import auc
    city_path = BUILD_DIR / "backtest_2021" / "city.json"
    city = json.loads(city_path.read_text())
    cats, b = city["sim_categories"], city["businesses"]
    revenue = yearly_revenue(city_path, "backtest")
    n = len(b["name"])
    naics = [CATEGORY_NAICS.get(cats[c], "") for c in b["sim_category"]]
    rev = {y: np.pad(v, (0, max(0, n - len(v))))[:n] for y, v in revenue.items()}
    closed = simulate(rev, naics, buffer_months)
    survived_2020 = ~closed["2020"]
    later = np.zeros_like(survived_2020)
    for y in ("2021", "2022", "2023", "2024", "2025"):
        later |= closed[y]
    p = (later & survived_2020).sum(axis=1) / np.maximum(survived_2020.sum(axis=1), 1)
    labels = {r["osm_id"]: r["closed"] for r in json.loads((ROOT / "docs" / "backtest_2021_2026.json").read_text())["businesses"]}
    rows = [(p[i], labels[b["osm_id"][i]]) for i in range(n) if b["osm_id"][i] in labels]
    c = [x for x, k in rows if k]
    o = [x for x, k in rows if not k]
    # auc() gives the chance a closed business scores BELOW a survivor; here a higher closure chance should mean closure
    score = 1 - auc(c, o)
    print(f"cash buffer {buffer_months} months: {len(rows)} businesses, {len(c)} closed; mean chance of running out of cash "
          f"{np.mean(c):.2f} for those that closed vs {np.mean(o):.2f} for survivors; ranking score {score:.3f}")
    return {"buffer_months": buffer_months, "followed": len(rows), "closed": len(c), "ranking_score": round(score, 3),
            "mean_chance_closed": round(float(np.mean(c)), 3), "mean_chance_survivors": round(float(np.mean(o)), 3)}
