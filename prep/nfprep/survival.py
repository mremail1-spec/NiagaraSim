"""Cash balances and closures: why businesses fail when cash runs out, not when revenue dips.

Every business gets a yearly profit and loss from the model's revenue for that year (run with the
year's real conditions) and its industry's cost structure (Statistics Canada benchmarks):
  - goods and part of wages move with sales; rent, utilities and the rest of wages are fixed;
  - rent: the industry's rent share, scaled by asking rent on the business's street against the
    city median (rent_surface.py; 13 listings, so districts rather than blocks);
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
from .rent_surface import location_factors
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


def yearly_revenue(city_path: Path, tag: str, reuse: bool = False) -> dict[str, np.ndarray]:
    cond = json.loads((DATA_DIR / "yearly_conditions.json").read_text())
    pop = json.loads((DATA_DIR / "sim_parameters.json").read_text())["residents"]["population"]["statcan_estimates_by_year"]
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = {}
    for y in YEARS:
        f = BUILD_DIR / f"survival_{tag}_{y}.csv"
        if reuse and f.exists() and f.stat().st_mtime > city_path.stat().st_mtime:
            rows = list(csv.DictReader(open(f)))
            arr = np.zeros(max(int(r["business"]) for r in rows) + 1)
            for r in rows:
                arr[int(r["business"])] = float(r["revenue"])
            out[y] = arr
            continue
        subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "flow", "--days", "365",
                        "--beta", str(beta), "--scenario", scenario(cond, pop, y), "--businesses", str(f)],
                       check=True, stdout=subprocess.DEVNULL)
        rows = list(csv.DictReader(open(f)))
        arr = np.zeros(max(int(r["business"]) for r in rows) + 1)
        for r in rows:
            arr[int(r["business"])] = float(r["revenue"])
        out[y] = arr
    return out


def simulate(revenue: dict, naics: list[str], buffer_months: float, seed: int = 1, rent_factor: np.ndarray | None = None):
    """Returns closed[year] -> (n_business, DRAWS) booleans of financial closures in that year.

    rent_factor: rent on each business's street relative to the city's median asking rent
    (rent_surface.location_factors); its rent is the industry's rent share times that factor."""
    ind = json.loads((DATA_DIR / "economic_indicators.json").read_text())
    bench = {r["naics"]: r for r in csv.DictReader(open(DATA_DIR / "business_revenue_benchmarks.csv"))}
    rng = np.random.default_rng(seed)
    n = len(naics)
    f = lambda k: np.array([float(bench.get(x, {}).get(k) or 0) / 100 for x in naics])
    margin = f("net_profit_pct")[:, None] + rng.normal(0, MARGIN_SPREAD, (n, DRAWS))
    wages = (f("wages_benefits_pct") + f("labour_commissions_pct"))[:, None]
    rent = f("rent_pct") * (np.ones(n) if rent_factor is None else np.clip(rent_factor, 0.3, 3.0))
    fixed_share = (rent + f("utilities_pct"))[:, None] + 0.4 * wages
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
    rf = location_factors([b["lat"][i] for i in keep], [b["lon"][i] for i in keep])
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
        closed = simulate(rev, naics, months, rent_factor=rf)
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
    closed = simulate(rev, naics, buffer_months, rent_factor=location_factors(b["lat"], b["lon"]))
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


# Statistics Canada industries (Ontario detail) and the model's NAICS codes inside each
RATE_GROUPS = {"Retail trade": "44", "Food and beverage services (tourism)": "722",
               "Accommodation (tourism)": "721", "Arts, entertainment and recreation": "71",
               "Other services (except public administration)": "81"}


def statcan_rates(series: dict, measure: str = "Closing businesses") -> dict[str, float]:
    """Yearly closing rate: the mean monthly share of active businesses closing, times 12."""
    act, clo = series["Active businesses"], series[measure]
    out = {}
    for y in YEARS:
        ms = [m for m in act if m.startswith(y) and m in clo]
        if ms:
            out[y] = 12 * float(np.mean([clo[m] / act[m] for m in ms]))
    return out


def rate_check(buffer_months: float = 3.0) -> dict:
    """Does the model's yearly path of cash-driven closures follow Statistics Canada's monthly
    closings? St. Catharines-Niagara (all industries) for the timing across years; Ontario
    industries for which industries the pandemic hit hardest. Compared as changes from 2019,
    because Statistics Canada's closings include temporary ones and the model counts only
    permanent closures for lack of cash."""
    sc = json.loads((DATA_DIR / "business_dynamics_statcan.json").read_text())
    pick = lambda g, ind: {k.split(" | ")[1]: v for k, v in sc[g].items() if k.startswith(ind + " | ")}
    city_path = BUILD_DIR / "city_2025" / "city.json"
    city = json.loads(city_path.read_text())
    cats, b = city["sim_categories"], city["businesses"]
    keep = [i for i in range(len(b["name"])) if not b["external"][i] and b["employer"][i]]
    naics_all = [CATEGORY_NAICS.get(cats[c], "") for c in b["sim_category"]]
    revenue = yearly_revenue(city_path, "city", reuse=True)
    rev = {y: v[keep] for y, v in revenue.items()}
    naics = [naics_all[i] for i in keep]
    rf = location_factors([b["lat"][i] for i in keep], [b["lon"][i] for i in keep])
    closed = simulate(rev, naics, buffer_months, rent_factor=rf)
    alive = np.ones_like(closed["2020"])
    model = {}
    for y in YEARS[1:]:
        model[y] = float(closed[y].sum() / max(alive.sum(), 1))
        alive &= ~closed[y]
    cma = statcan_rates(pick("st_catharines_niagara_cma", "Business sector industries"))
    yrs = YEARS[1:]
    m_ex = [model[y] for y in yrs]
    c_ex = [cma[y] - cma["2019"] for y in yrs]
    corr = float(np.corrcoef(m_ex, c_ex)[0, 1])
    # direction of each year-to-year move
    moves = sum(np.sign(m_ex[i] - m_ex[i - 1]) == np.sign(c_ex[i] - c_ex[i - 1]) for i in range(1, len(yrs)))
    # Statistics Canada's closings are mostly businesses dropping their payroll for a while; the
    # model's counterpart is a business whose revenue falls below a share of normal.
    r0 = rev["2019"]
    shed = {t: [float((rev[y] / np.maximum(r0, 1) < t).mean()) for y in yrs] for t in (0.5, 0.6, 0.7, 0.8)}
    shed_corr = {t: round(float(np.corrcoef(v, c_ex)[0, 1]), 3) for t, v in shed.items()}
    ind_rows = {}
    for name, prefix in RATE_GROUPS.items():
        idx = [i for i, x in enumerate(naics) if x.startswith(prefix)]
        if not idx:
            continue
        r = statcan_rates(pick("ontario", name))
        ind_rows[name] = {"businesses_in_model": len(idx),
                          "model_2020_closure_share": round(float(closed["2020"][idx].mean()), 3),
                          "model_2020_revenue_vs_2019": round(float(rev["2020"][idx].sum() / r0[idx].sum()), 3),
                          "ontario_2020_closing_rate_vs_2019": round(r["2020"] / r["2019"], 3)}
    xs = [v["model_2020_closure_share"] for v in ind_rows.values()]
    ys = [v["ontario_2020_closing_rate_vs_2019"] for v in ind_rows.values()]
    rank = lambda v: np.argsort(np.argsort(v))
    rho = float(np.corrcoef(rank(xs), rank(ys))[0, 1])
    drop = [1 - v["model_2020_revenue_vs_2019"] for v in ind_rows.values()]
    rho_drop = float(np.corrcoef(rank(drop), rank(ys))[0, 1])
    result = {"_meta": {"what": rate_check.__doc__.strip(), "cash_buffer_months": buffer_months},
              "years": {y: {"model_cash_closure_share": round(model[y], 4),
                            "st_catharines_niagara_closing_rate": round(cma[y], 3),
                            "change_from_2019": round(cma[y] - cma["2019"], 3)} for y in yrs},
              "year_correlation": round(corr, 3), "direction_right": f"{moves} of {len(yrs) - 1}",
              "industries_2020": ind_rows, "industry_rank_correlation": round(rho, 3),
              "staff_shedding": {"share_of_businesses_below_revenue_share_by_year": {str(t): dict(zip(yrs, map(lambda x: round(x, 3), v))) for t, v in shed.items()},
                                 "year_correlation_with_closings": {str(t): c for t, c in shed_corr.items()},
                                 "industry_rank_correlation_revenue_drop_2020": round(rho_drop, 3)}}
    (ROOT / "docs" / "survival_rate_check.json").write_text(json.dumps(result, indent=1))
    for y in yrs:
        print(y, f"model {model[y]:.3f}", f"St. Catharines-Niagara closing {cma[y]:.3f} ({cma[y] - cma['2019']:+.3f} on 2019)")
    print(f"year correlation {corr:.2f}; direction right {moves} of {len(yrs) - 1}")
    for k, v in ind_rows.items():
        print(f"{k:45s} model 2020 {v['model_2020_closure_share']:.3f}  Ontario 2020/2019 {v['ontario_2020_closing_rate_vs_2019']:.2f}")
    print(f"industry rank correlation {rho:.2f}")
    print("staff shedding (revenue below a share of 2019) against closings, year correlation:", shed_corr,
          f"; industry rank of the 2020 revenue drop {rho_drop:.2f}")
    return result
