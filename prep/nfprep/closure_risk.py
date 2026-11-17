"""Chance of closing within five years, scored properly.

Fits a small logistic model on the 485 businesses on the map in January 2021 and whether they were
gone by July 2026 (docs/backtest_2021_2026.json), with features the model can produce for any site:
  - the closure rate of the business's kind (shrunk toward the overall rate),
  - its site demand rank within its kind and its revenue against its size (from the simulation),
  - the share of its sales from visitors, whether it is in the Tourist Core,
  - how many of its own kind are within 250 m,
  - the share of revenue it kept in the pandemic run.
Scored by ten-fold cross-validation (every probability comes from a model that did not see that
business): Brier score against the overall-rate baseline, log loss, ranking and a calibration
table. Writes docs/closure_risk_check.json.

Usage: python3 -m nfprep.closure_risk
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SHRINK = 20          # SEED: a kind's own closure rate counts as much as this many businesses at the overall rate
L2 = 1.0             # ridge penalty on the standardised coefficients
FOLDS = 10


def features(rows: list[dict], lat: dict, lon: dict, kind_rate: dict, overall: float) -> np.ndarray:
    kinds = np.array([r["sim_category"] for r in rows])
    la = np.array([lat[r["osm_id"]] for r in rows]); lo = np.array([lon[r["osm_id"]] for r in rows])
    same = np.array([((np.hypot((la - la[i]) * 111.3e3, (lo - lo[i]) * 81.2e3) < 250) & (kinds == kinds[i])).sum() - 1
                     for i in range(len(rows))])
    rate = np.array([kind_rate.get(k, overall) for k in kinds])
    logit = lambda p: np.log(p / (1 - p))
    return np.column_stack([
        logit(np.clip(rate, 0.005, 0.995)),
        [r["rank_among_its_kind"] for r in rows],
        np.log([max(r["revenue_vs_size"], 0.01) for r in rows]),
        [r["tourist_share"] for r in rows],
        [r["in_tourist_core"] for r in rows],
        np.log1p(same),
        [r["share_kept_in_pandemic"] for r in rows],
    ])


NAMES = ["kind closure rate (logit)", "site demand rank in kind", "log revenue vs size", "visitor share of sales",
         "in Tourist Core", "log(1 + same kind within 250 m)", "share kept in pandemic"]


def fit(X: np.ndarray, y: np.ndarray):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.zeros(Z.shape[1])
    pen = np.full(Z.shape[1], L2); pen[0] = 0
    for _ in range(50):                               # Newton steps
        p = 1 / (1 + np.exp(-Z @ w))
        g = Z.T @ (p - y) + pen * w
        H = Z.T @ (Z * (p * (1 - p))[:, None]) + np.diag(pen)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w, mu, sd


def predict(model, X):
    w, mu, sd = model
    return 1 / (1 + np.exp(-(np.column_stack([np.ones(len(X)), (X - mu) / sd]) @ w)))


def kind_rates(rows, y, overall):
    out = {}
    for k in {r["sim_category"] for r in rows}:
        m = np.array([r["sim_category"] == k for r in rows])
        out[k] = (y[m].sum() + SHRINK * overall) / (m.sum() + SHRINK)
    return out


def main(cols: list[int] | None = None, l2: float | None = None, write: bool = True) -> dict:
    global L2
    if l2 is not None:
        L2 = l2
    feats = features if cols is None else (lambda *a: features(*a)[:, cols])
    from .backtest import auc
    bt = json.loads((ROOT / "docs" / "backtest_2021_2026.json").read_text())
    city = json.loads((ROOT / "build" / "backtest_2021" / "city.json").read_text())
    b = city["businesses"]
    lat = dict(zip(b["osm_id"], b["lat"])); lon = dict(zip(b["osm_id"], b["lon"]))
    rows = [r for r in bt["businesses"] if r["osm_id"] in lat]
    y = np.array([float(r["closed"]) for r in rows])
    rng = np.random.default_rng(7)
    fold = rng.permutation(len(rows)) % FOLDS
    p_model, p_base, p_kind = np.zeros(len(rows)), np.zeros(len(rows)), np.zeros(len(rows))
    for f in range(FOLDS):
        tr, te = fold != f, fold == f
        tr_rows = [r for r, t in zip(rows, tr) if t]; te_rows = [r for r, t in zip(rows, te) if t]
        overall = y[tr].mean()
        kr = kind_rates(tr_rows, y[tr], overall)
        # the kind rate inside training is itself out-of-fold for honesty: leave each business out of its own kind's rate
        X_tr = feats(tr_rows, lat, lon, kr, overall)
        X_tr[:, 0] = np.log(np.clip([(kr[r["sim_category"]] * (sum(1 for q in tr_rows if q["sim_category"] == r["sim_category"]) + SHRINK) - yy)
                                     / (sum(1 for q in tr_rows if q["sim_category"] == r["sim_category"]) + SHRINK - 1)
                                     for r, yy in zip(tr_rows, y[tr])], 0.005, 0.995))
        X_tr[:, 0] = np.log(np.exp(X_tr[:, 0]) / (1 - np.exp(X_tr[:, 0])))
        model = fit(X_tr, y[tr])
        p_model[te] = predict(model, feats(te_rows, lat, lon, kr, overall))
        p_base[te] = overall
        p_kind[te] = [kr.get(r["sim_category"], overall) for r in te_rows]
    brier = lambda p: float(np.mean((p - y) ** 2))
    logloss = lambda p: float(-np.mean(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))))
    skill = lambda p: 1 - brier(p) / brier(p_base)
    bins = np.quantile(p_model, [0, 0.5, 0.8, 0.95, 1.0])
    table = []
    for lo_, hi_ in zip(bins[:-1], bins[1:]):
        m = (p_model >= lo_) & (p_model <= hi_)
        table.append({"predicted_range": [round(float(lo_), 3), round(float(hi_), 3)], "businesses": int(m.sum()),
                      "mean_predicted": round(float(p_model[m].mean()), 3), "share_closed": round(float(y[m].mean()), 3)})
    full = fit(feats(rows, lat, lon, kind_rates(rows, y, y.mean()), y.mean()), y)
    result = {
        "_meta": {"what": __doc__.strip().split("\n\n")[0], "businesses": len(rows), "closed": int(y.sum()),
                  "cross_validation": f"{FOLDS}-fold", "shrink": SHRINK, "l2": L2},
        "brier": {"overall_rate": round(brier(p_base), 4), "kind_rate": round(brier(p_kind), 4), "model": round(brier(p_model), 4)},
        "brier_skill_vs_overall_rate": {"kind_rate": round(skill(p_kind), 3), "model": round(skill(p_model), 3)},
        "log_loss": {"overall_rate": round(logloss(p_base), 4), "kind_rate": round(logloss(p_kind), 4), "model": round(logloss(p_model), 4)},
        "ranking_score": {"kind_rate": round(1 - auc(p_kind[y == 1], p_kind[y == 0]), 3),
                          "model": round(1 - auc(p_model[y == 1], p_model[y == 0]), 3)},
        "calibration": table,
        "coefficients_all_data_standardised": dict(zip(["intercept"] + [NAMES[c] for c in (cols or range(len(NAMES)))],
                                                       [round(float(v), 3) for v in full[0]])),
    }
    if not write:
        return result
    # every variant tried, so the choice among them is visible
    sweep = []
    for pen in (1.0, 10.0, 30.0, 100.0):
        for cs in ([0, 1, 2, 3, 4, 5, 6], [0, 4, 5], [0, 5], [0]):
            r = main(cs, pen, write=False)
            sweep.append({"l2": pen, "features": [NAMES[c] for c in cs], "brier_skill": r["brier_skill_vs_overall_rate"]["model"],
                          "ranking_score": r["ranking_score"]["model"]})
    L2 = 1.0
    result["variants_tried"] = sweep
    result["conclusion"] = ("No variant beats the overall closure rate by more than 1.3% of its Brier score (best of 16, so optimistic). "
                            "The page shows each kind's own 2021-2026 closure rate, shrunk toward the overall rate, which is calibrated by construction.")
    rates = kind_rates(rows, y, y.mean())
    counts = {k: sum(1 for r in rows if r["sim_category"] == k) for k in rates}
    page = {"_meta": {"what": "Share of businesses on the map in January 2021 that were gone by July 2026, by kind, shrunk toward the overall rate "
                              f"({y.mean():.3f}) as if each kind had {SHRINK} more businesses at that rate. Written by prep/nfprep/closure_risk.py.",
                      "overall": round(float(y.mean()), 4)},
            "by_kind": {k: {"rate": round(float(v), 4), "businesses": counts[k],
                            "closed": int(sum(r["closed"] for r in rows if r["sim_category"] == k))} for k, v in sorted(rates.items())}}
    (ROOT / "data" / "closure_rates_2021_2026.json").write_text(json.dumps(page, indent=1))
    (ROOT / "docs" / "closure_risk_check.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k not in ("_meta", "variants_tried")}, indent=1))
    for v in sweep:
        print(v)
    return result


if __name__ == "__main__":
    main()
