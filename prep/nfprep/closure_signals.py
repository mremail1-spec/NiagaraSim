"""Do neighbourhood signals predict closures beyond a business's kind, age and size?

Businesses open in the Employment Inventory in a start year (2023, after the pandemic; 2016,
before it) and their status two years later. Signals, all measured at or before the start year:
  facts         kind's closure rate, age, employee band, floor area
  neighbourhood vacant or closed units within 300 m (share), the area's change in open
                businesses within 500 m over the previous years, same-kind businesses that opened
                within 500 m in the last two years (new competition), same-kind businesses within 300 m
  site          the model's people on foot and vehicles passing the address, bus departures per
                weekday within 300 m (Niagara Region Transit), the model's forecast rank within kind
Ten-fold cross-validated logistic models (ridge), Brier skill against each kind's rate.

Usage: python3 -m nfprep.closure_signals   (writes docs/closure_signals_check.json)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .backtest import auc
from .closure_risk import fit, predict
from .inventory_check import BAND, forecasts, kind_of, load
from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
L2 = 10.0


def _km(lat, lon, la, lo):
    return np.hypot((lat - la) * 111.32, (lon - lo) * 111.32 * np.cos(np.radians(la)))


def rows_for(status, rec, start, end, city):
    cats = city["sim_categories"]
    menu = {m["sim_category"]: m["sizes"]["typical"] for m in city["build_menu"]}
    # every inventory record of each year with a position
    pos = {}
    for i, st in status.items():
        for y, s in st.items():
            x = rec[i][y]
            if x["lat"]:
                pos.setdefault(y, []).append((i, float(x["lat"]), float(x["lon"]), s, kind_of(x["primarynaics"]),
                                              int(float(x["yearopen"] or 0))))
    P = {y: (np.array([p[1] for p in v]), np.array([p[2] for p in v]), np.array([p[3] for p in v]),
             np.array([p[4] or "" for p in v]), np.array([p[5] for p in v])) for y, v in pos.items()}
    prev = max(y for y in P if y <= start - 3) if any(y <= start - 3 for y in P) else min(P)
    stops = json.loads((DATA_DIR / "transit_stops_niagara_falls.json").read_text())["stops"]
    sl, so, sd = (np.array([s[k] for s in stops]) for k in ("lat", "lon", "weekday_departures"))
    nl, no = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    walk, passing = np.array(city["nodes"].get("walkers", [0] * len(nl)), float), np.array(city["nodes"].get("passing", [0] * len(nl)), float)
    rows = []
    for i, st in status.items():
        if st.get(start) != "open" or st.get(end) not in ("open", "closed"):
            continue
        x = rec[i][start]
        k = kind_of(x["primarynaics"])
        if not k or k not in cats or not x["lat"]:
            continue
        la, lo = float(x["lat"]), float(x["lon"])
        yo = int(float(x["yearopen"] or 0))
        la_s, lo_s, s_s, k_s, yo_s = P[start]
        d = _km(la_s, lo_s, la, lo)
        near3, near5 = (d < 0.3) & (d > 0.001), d < 0.5
        vac = float((s_s[near3] == "closed").mean()) if near3.any() else 0.0
        open_now = int(((s_s == "open") & near5).sum())
        la_p, lo_p, s_p, _, _ = P[prev]
        open_before = int(((s_p == "open") & (_km(la_p, lo_p, la, lo) < 0.5)).sum())
        new_comp = int(((k_s == k) & near5 & (s_s == "open") & (yo_s >= start - 2) & (d > 0.001)).sum())
        same300 = int(((k_s == k) & near3 & (s_s == "open")).sum())
        dn = _km(nl, no, la, lo); node = int(dn.argmin())
        ds = _km(sl, so, la, lo)
        rows.append({"kind": k, "cat": cats.index(k), "lat": la, "lon": lo, "closed": st[end] == "closed",
                     "age": (start - yo) if 1900 < yo <= start else None, "band": BAND[x["sizerangeemployees"]],
                     "gfa": float(x["indoorgfa"] or 0), "vacancy_300m": vac,
                     "area_trend": np.log((open_now + 1) / (open_before + 1)), "new_competitors_500m": new_comp,
                     "same_kind_300m": same300, "walkers": walk[node], "passing": passing[node],
                     "bus_departures_300m": float(sd[ds < 0.3].sum())})
    f = forecasts([[r["lat"], r["lon"], r["cat"], menu.get(r["cat"], 300_000.0)] for r in rows])
    for r, (res, tour) in zip(rows, f):
        r["forecast"] = res + tour
    for k in {r["kind"] for r in rows}:
        g = [r for r in rows if r["kind"] == k]
        v = np.array([r["forecast"] for r in g])
        for r in g:
            r["pct"] = float((v < r["forecast"]).mean())
    return rows


def evaluate(rows, label):
    y = np.array([float(r["closed"]) for r in rows])
    med_age = float(np.median([r["age"] for r in rows if r["age"] is not None]))
    F = {
        "log age": [np.log1p(r["age"] if r["age"] is not None else med_age) for r in rows],
        "employee band": [r["band"] for r in rows],
        "log floor area": [np.log1p(r["gfa"]) for r in rows],
        "vacancy within 300 m": [r["vacancy_300m"] for r in rows],
        "area trend (open businesses within 500 m)": [r["area_trend"] for r in rows],
        "new same-kind competitors within 500 m": [np.log1p(r["new_competitors_500m"]) for r in rows],
        "same kind within 300 m": [np.log1p(r["same_kind_300m"]) for r in rows],
        "people on foot (model)": [np.log1p(r["walkers"]) for r in rows],
        "vehicles passing (model)": [np.log1p(r["passing"]) for r in rows],
        "bus departures within 300 m": [np.log1p(r["bus_departures_300m"]) for r in rows],
        "model forecast rank in kind": [r["pct"] for r in rows],
    }
    names = list(F)
    X_all = np.column_stack([F[n] for n in names]).astype(float)
    sets = {"facts": names[:3], "facts + neighbourhood": names[:7], "facts + site": names[:3] + names[7:],
            "everything": names}
    rng = np.random.default_rng(11); fold = rng.permutation(len(rows)) % 10
    preds = {"kind rate": np.zeros(len(rows)), **{s: np.zeros(len(rows)) for s in sets}}
    for fo in range(10):
        tr, te = fold != fo, fold == fo
        o = y[tr].mean(); rate = {}
        for k in {r["kind"] for r in rows}:
            m = np.array([r["kind"] == k for r in rows]) & tr
            rate[k] = (y[m].sum() + 20 * o) / (m.sum() + 20)
        p = np.array([rate[r["kind"]] for r in rows]); kr = np.log(p / (1 - p))
        preds["kind rate"][te] = p[te]
        import nfprep.closure_risk as cr
        cr.L2 = L2
        for s, cols in sets.items():
            X = np.column_stack([kr] + [X_all[:, names.index(c)] for c in cols])
            preds[s][te] = predict(fit(X[tr], y[tr]), X[te])
    brier = lambda q: float(np.mean((q - y) ** 2))
    out = {"window": label, "businesses": len(rows), "closed": int(y.sum()), "models": {}}
    for s, q in preds.items():
        out["models"][s] = {"skill_vs_kind_rate": round(1 - brier(q) / brier(preds["kind rate"]), 3),
                            "ranking": round(1 - auc(q[y == 1], q[y == 0]), 3)}
    out["single_signal_ranking_higher_means_more_closures"] = {n: round(1 - auc(X_all[y == 1, j], X_all[y == 0, j]), 3)
                                                               for j, n in enumerate(names)}
    return out


def main() -> dict:
    status, rec = load()
    city = json.loads((ROOT / "build/city_2025/city.json").read_text())
    res = {"_meta": {"what": __doc__.strip().split("\n\n")[0]},
           "after_pandemic": evaluate(rows_for(status, rec, 2023, 2025, city), "open 2023, status 2025"),
           "before_pandemic": evaluate(rows_for(status, rec, 2016, 2018, city), "open 2016, status 2018")}
    (ROOT / "docs" / "closure_signals_check.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main()
