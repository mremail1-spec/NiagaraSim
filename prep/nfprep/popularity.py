"""Popularity from Google ratings and review counts: which shops people actually go to.

Review counts follow customer numbers, but tourists review far more often than residents do. For each
kind of business the review counts are fitted as  reviews = a * (resident sales + w * tourist sales):
w says how much more often a tourist dollar leaves a review than a resident dollar, so a shop's reviews
tell how much of its trade is local or visitor. What is left over is popularity the model does not
explain (a well-liked shop, a favourite lunch stop): each rated shop's pull is moved part of the way
toward its reviews, a few rounds, competitors losing what it gains (spending by kind stays fixed).
Chains' branches move against each other only, so each chain still matches its published sales.

Writes the fitted pulls into the city (build/city_2025/city.json) and docs/popularity_check.json.
Usage: python3 -m nfprep.popularity        (after the default scenario)
"""
import csv, json, math, subprocess, tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
CITY = ROOT / "build/city_2025/city.json"
NFSIM = str(ROOT / "engine/target/release/nfsim")
ROUNDS, DAMP, STEP = 4, 0.5, (0.67, 1.5)
TOTAL = (0.25, 4.0)        # a shop's pull moves at most this far from where the model put it
MIN_REVIEWS, MIN_RATED = 5, 8
W_GRID = [0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24]


def ratings(b: dict) -> dict:
    """business index -> (reviews, rating), by OpenStreetMap id or by name and address."""
    g = json.loads((DATA_DIR / "google_places.json").read_text())
    by_osm, by_name = {}, {}
    for k, v in g.items():
        m = v.get("match") or {}
        if m.get("userRatingCount") is None or m.get("businessStatus") not in (None, "OPERATIONAL"):
            continue
        val = (int(m["userRatingCount"]), float(m.get("rating") or 0))
        if "/" in k and ":" not in k:
            by_osm[k] = val
        by_name[(v["name"].lower(), (v.get("address") or "").lower())] = val
    out = {}
    for i in range(len(b["node"])):
        v = by_osm.get(b["osm_id"][i]) if b["osm_id"][i] else None
        v = v or by_name.get((b["name"][i].lower(), (b["address"][i] or "").lower()))
        if v and v[0] >= MIN_REVIEWS and not b["external"][i] and not b["closed_from"][i]:
            out[i] = v
    return out


def year(city: dict, beta: float) -> tuple[dict, dict]:
    with tempfile.TemporaryDirectory() as d:
        cp, bp = Path(d) / "c.json", Path(d) / "b.csv"
        cp.write_text(json.dumps(city, separators=(",", ":")))
        subprocess.run([NFSIM, "run", str(cp), "--mode", "flow", "--days", "365", "--beta", str(beta), "--businesses", str(bp)],
                       check=True, capture_output=True)
        r = list(csv.DictReader(open(bp)))
    tot = {int(x["business"]): float(x["revenue"]) for x in r}
    tour = {int(x["business"]): float(x["tourist_revenue"]) for x in r}
    return {i: tot[i] - tour[i] for i in tot}, tour


def spearman(a, b) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def fit_kind(ids, n, res, tour):
    """Best w and a for reviews = a * (resident + w * tourist); residuals on the log scale."""
    best = None
    y = np.log(np.array([n[i] for i in ids], float))
    for w in W_GRID:
        x = np.log(np.array([max(res.get(i, 0) + w * tour.get(i, 0), 1.0) for i in ids]))
        la = float(np.mean(y - x))
        err = float(np.mean((y - x - la) ** 2))
        if best is None or err < best[0]:
            best = (err, w, la, y - x - la)
    return best


def main() -> dict:
    city = json.loads(CITY.read_text())
    b, cats = city["businesses"], city["sim_categories"]
    cal = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text()); beta = cal.get("beta_fit", cal["beta"])
    rated = ratings(b)
    kinds = defaultdict(list)
    for i in rated:
        kinds[b["sim_category"][i]].append(i)
    kinds = {k: v for k, v in kinds.items() if len(v) >= MIN_RATED}
    n = {i: rated[i][0] for i in rated}
    start = list(b["format_weight"])
    report, hist = {}, []
    for rnd in range(ROUNDS + 1):
        res, tour = year(city, beta)
        row = {}
        for k, ids in kinds.items():
            err, w, la, resid = fit_kind(ids, n, res, tour)
            sales = [res.get(i, 0) + tour.get(i, 0) for i in ids]
            row[cats[k]] = {"rated": len(ids), "tourist_review_weight": w, "rank_match": round(spearman(sales, [n[i] for i in ids]), 3),
                            "log_sd": round(math.sqrt(err), 2)}
            if rnd == ROUNDS:
                report[cats[k]] = (ids, resid, w)
                continue
            for i, r in zip(ids, resid):
                f = min(max(math.exp(DAMP * r), STEP[0]), STEP[1])
                b["format_weight"][i] = min(max(b["format_weight"][i] * f, start[i] * TOTAL[0]), start[i] * TOTAL[1])
        hist.append(row)
        print("round", rnd, {k: (v["rank_match"], v["tourist_review_weight"]) for k, v in row.items()}, flush=True)
        if rnd == ROUNDS:
            break
        # a chain's branches move against each other only (the chain still matches its published sales)
        groups = defaultdict(list)
        for i in rated:
            if b["chain"][i] >= 0:
                groups[b["chain"][i]].append(i)
        for ids in groups.values():
            g = math.exp(np.mean([math.log(b["format_weight"][i] / start[i]) for i in ids]))
            for i in ids:
                b["format_weight"][i] /= g
    # what the reviews say the model is missing: shops far busier, or far quieter, than modelled
    res, tour = year(city, beta)
    miss = []
    for k, (ids, resid, w) in report.items():
        for i, r in zip(ids, resid):
            miss.append({"name": b["name"][i], "address": b["address"][i], "kind": k, "reviews": n[i], "rating": rated[i][1],
                         "pull_change": round(b["format_weight"][i] / start[i], 2), "reviews_vs_model": round(math.exp(r), 2),
                         "tourist_share_model": round(tour.get(i, 0) / max(res.get(i, 0) + tour.get(i, 0), 1), 2)})
    miss.sort(key=lambda x: -abs(math.log(x["reviews_vs_model"])))
    rr = [(rated[i][1], math.log(b["format_weight"][i] / start[i])) for i in rated if b["format_weight"][i] != start[i]]
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "source": "Google Places ratings and review counts (data/google_places.json, 2026-11)",
                     "rated_businesses_used": len(rated), "rounds": ROUNDS},
           "by_kind_by_round": hist,
           "rating_vs_pull_change_corr": round(float(np.corrcoef(*zip(*rr))[0, 1]), 3) if len(rr) > 5 else None,
           "still_unexplained": miss[:60]}
    (ROOT / "docs/popularity_check.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    CITY.write_text(json.dumps(city, separators=(",", ":")))
    from .default_scenario import fit_brands
    fit_brands(city, beta, rounds=4)                   # chains back on their published sales
    CITY.write_text(json.dumps(city, separators=(",", ":")))
    return doc


if __name__ == "__main__":
    main()
