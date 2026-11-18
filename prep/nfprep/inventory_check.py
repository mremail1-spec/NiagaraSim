"""Tests against Niagara Region's Employment Inventory for Niagara Falls (2016-2025, surveyed yearly
except 2020-2021; data/sources/region/employment_inventory_niagara_falls_2016_2025.csv).

A. Sales at a site: the model's revenue against each business's surveyed employee band (micro 1-4,
   small 5-99, medium 100-499, large 500+), for businesses matched to the model's 2025 businesses
   (within 40 m, similar name). Rank correlation within each kind of business.
B. Closures after the pandemic: businesses open in 2023 and marked "Permanently Closed or Vacant"
   by 2025. Each is scored by the engine's forecast for its kind at its spot (nfsim forecast), plus
   the inventory's own facts (age, employee band, floor area); ten-fold cross-validated logistic
   model; Brier skill against each kind's own closure rate.

Usage: python3 -m nfprep.inventory_check   (writes docs/inventory_check.json)
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .closure_risk import fit, predict
from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
INV = DATA_DIR / "sources" / "region" / "employment_inventory_niagara_falls_2016_2025.csv"
BAND = {"Micro (1-4 Employees)": 1, "Micro": 1, "Small (5-99 Employees)": 2, "Small": 2,
        "Medium (100-499 Employees)": 3, "Medium": 3, "Large (500 + Employees)": 4}
# NAICS prefix -> model kind (longest prefix wins)
NAICS_KIND = {"722511": "restaurant", "722512": "fast_food", "722513": "fast_food", "722514": "fast_food", "722515": "cafe",
              "7224": "bar_pub", "7223": "restaurant", "721": "accommodation", "445": "grocery_food", "458": "apparel",
              "448": "apparel", "455": "general_merchandise", "452": "general_merchandise", "449": "home_electronics",
              "444": "home_electronics", "443": "home_electronics", "442": "home_electronics", "456": "pharmacy",
              "446": "pharmacy", "457": "fuel", "447": "fuel", "459420": "souvenir_gift", "453220": "souvenir_gift",
              "459": "retail_other", "453": "retail_other", "451": "retail_other", "71321": "casino", "7131": "attraction",
              "7121": "attraction", "713": "leisure_paid", "711": "entertainment", "812": "personal_services",
              "811": "auto_service", "621": "health", "624410": "childcare", "441": "auto_dealer", "532": "car_rental"}


def kind_of(naics: str) -> str | None:
    for k in range(6, 2, -1):
        if naics[:k] in NAICS_KIND:
            return NAICS_KIND[naics[:k]]
    return None


def load():
    rows = list(csv.DictReader(open(INV, encoding="utf-8-sig")))
    status, rec = defaultdict(dict), defaultdict(dict)
    for x in rows:
        s = x["sizerangeemployees"]
        y = int(x["Year"])
        status[x["nei_id"]][y] = "closed" if s.startswith("Permanently") else ("open" if s in BAND else "unknown")
        rec[x["nei_id"]][y] = x
    return status, rec


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).replace(" the ", " ").strip()


def forecasts(sites: list) -> np.ndarray:
    tmp = ROOT / "build" / "city_2025"
    (tmp / "inv_sites.json").write_text(json.dumps(sites))
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "forecast", str(tmp / "city.json"), "--beta", str(beta),
                    "--sites", str(tmp / "inv_sites.json"), "--out", str(tmp / "inv_out.json")], check=True, stdout=subprocess.DEVNULL)
    return np.array(json.loads((tmp / "inv_out.json").read_text()))


def spearman(a, b) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def test_sizes(status, rec, city) -> dict:
    b = city["businesses"]; cats = city["sim_categories"]
    lat, lon = np.array(b["lat"]), np.array(b["lon"])
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = ROOT / "build" / "city_2025" / "inv_rev.csv"
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(ROOT / "build/city_2025/city.json"), "--mode", "flow",
                    "--days", "365", "--beta", str(beta), "--businesses", str(out)], check=True, stdout=subprocess.DEVNULL)
    rev = {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(out))}
    pairs = []
    for i, st in status.items():
        if st.get(2025) != "open":
            continue
        x = rec[i][2025]
        k = kind_of(x["primarynaics"])
        if not k or not x["lat"]:
            continue
        la, lo = float(x["lat"]), float(x["lon"])
        d = np.hypot((lat - la) * 111_320, (lon - lo) * 81_200)
        near = [j for j in np.argsort(d)[:6] if d[j] < 40 and j in rev and not b["external"][j]]
        best, score = None, 0.0
        for j in near:
            s = difflib.SequenceMatcher(None, norm(b["name"][j]), norm(x["businessname"])).ratio()
            if s > score:
                best, score = j, s
        if best is not None and score >= 0.6:
            pairs.append((cats[b["sim_category"][best]], BAND[x["sizerangeemployees"]], rev[best]))
    by = defaultdict(list)
    for k, band, r in pairs:
        by[k].append((band, r))
    per_kind = {}
    for k, v in by.items():
        if len(v) >= 12 and len({x for x, _ in v}) > 1:
            per_kind[k] = {"matched": len(v), "rank_correlation": round(spearman([x for x, _ in v], [r for _, r in v]), 3)}
    w = [v["matched"] for v in per_kind.values()]
    overall = float(np.average([v["rank_correlation"] for v in per_kind.values()], weights=w)) if w else float("nan")
    return {"matched_businesses": len(pairs), "by_kind": per_kind, "weighted_rank_correlation": round(overall, 3)}


def test_closures(status, rec, city, start=2023, end=2025) -> dict:
    from .backtest import auc
    cats = city["sim_categories"]
    menu = {m["sim_category"]: m["sizes"]["typical"] for m in city["build_menu"]}
    rows = []
    for i, st in status.items():
        if st.get(start) != "open" or st.get(end) not in ("open", "closed"):
            continue
        x = rec[i][start]
        k = kind_of(x["primarynaics"])
        if not k or k not in cats or not x["lat"]:
            continue
        c = cats.index(k)
        yo = int(float(x["yearopen"] or 0))
        rows.append({"id": i, "kind": k, "cat": c, "lat": float(x["lat"]), "lon": float(x["lon"]), "closed": st[end] == "closed",
                     "age": (start - yo) if 1900 < yo <= start else None, "band": BAND[x["sizerangeemployees"]],
                     "gfa": float(x["indoorgfa"] or 0)})
    f = forecasts([[r["lat"], r["lon"], r["cat"], menu.get(r["cat"], 300_000.0)] for r in rows])
    for r, (res, tour) in zip(rows, f):
        r["forecast"] = res + tour
        r["tourist_share"] = tour / max(res + tour, 1)
    # forecast percentile within kind
    for k in {r["kind"] for r in rows}:
        g = [r for r in rows if r["kind"] == k]
        v = np.array([r["forecast"] for r in g])
        for r in g:
            r["pct"] = float((v < r["forecast"]).mean())
    y = np.array([float(r["closed"]) for r in rows])
    ages = [r["age"] for r in rows if r["age"] is not None]
    med_age = float(np.median(ages))
    X_all = np.column_stack([[r["pct"] for r in rows], [r["tourist_share"] for r in rows],
                             [np.log1p(r["age"] if r["age"] is not None else med_age) for r in rows],
                             [r["age"] is None for r in rows], [r["band"] for r in rows],
                             [np.log1p(r["gfa"]) for r in rows]])
    names = ["model forecast rank within kind", "model visitor share", "log age", "age unknown", "employee band", "log floor area"]
    rng = np.random.default_rng(11); fold = rng.permutation(len(rows)) % 10
    preds = {"kind_rate": np.zeros(len(rows)), "model_location": np.zeros(len(rows)), "facts": np.zeros(len(rows)),
             "facts_and_model": np.zeros(len(rows))}
    sets = {"model_location": [0, 1], "facts": [2, 3, 4, 5], "facts_and_model": [0, 1, 2, 3, 4, 5]}
    for fo in range(10):
        tr, te = fold != fo, fold == fo
        overall = y[tr].mean()
        rate = {}
        for k in {r["kind"] for r in rows}:
            m = np.array([r["kind"] == k for r in rows]) & tr
            rate[k] = (y[m].sum() + 20 * overall) / (m.sum() + 20)
        kr = np.log(np.array([rate[r["kind"]] for r in rows]) / (1 - np.array([rate[r["kind"]] for r in rows])))
        preds["kind_rate"][te] = 1 / (1 + np.exp(-kr[te]))
        for name, cols in sets.items():
            X = np.column_stack([kr, X_all[:, cols]])
            mdl = fit(X[tr], y[tr])
            preds[name][te] = predict(mdl, X[te])
    base = np.full(len(rows), y.mean())
    brier = lambda p: float(np.mean((p - y) ** 2))
    res = {}
    for name, p in preds.items():
        res[name] = {"brier": round(brier(p), 4), "skill_vs_overall": round(1 - brier(p) / brier(base), 3),
                     "skill_vs_kind_rate": round(1 - brier(p) / brier(preds["kind_rate"]), 3),
                     "ranking": round(1 - auc(p[y == 1], p[y == 0]), 3)}
    single = {}
    for j, n in enumerate(names):
        v = X_all[:, j].astype(float)
        single[n] = round(1 - auc(v[y == 1], v[y == 0]), 3)
    by_kind = {k: {"open": n, "closed": int(sum(r["closed"] for r in rows if r["kind"] == k))}
               for k, n in Counter(r["kind"] for r in rows).most_common()}
    full = fit(np.column_stack([np.zeros(len(rows)), X_all]), y)
    return {"window": f"open in {start}, closed by {end}", "businesses": len(rows), "closed": int(y.sum()),
            "closure_rate": round(float(y.mean()), 4), "scores": res,
            "single_feature_ranking_higher_means_more_closures": single, "by_kind": by_kind,
            "coefficients_standardised": dict(zip(["intercept", "kind (none here)"] + names, [round(float(v), 3) for v in full[0]]))}


def main() -> dict:
    status, rec = load()
    city = json.loads((ROOT / "build/city_2025/city.json").read_text())
    out = {"_meta": {"what": __doc__.strip().split("\n\n")[0]}, "sizes": test_sizes(status, rec, city),
           "closures_2023_2025": test_closures(status, rec, city, 2023, 2025),
           "closures_2016_2019": test_closures(status, rec, city, 2016, 2019)}
    (ROOT / "docs" / "inventory_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "_meta"}, indent=1)[:6000])
    return out


if __name__ == "__main__":
    main()
