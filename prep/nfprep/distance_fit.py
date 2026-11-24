"""How far residents go for each kind of purchase: fit a distance-decay multiplier per spending category.

Groceries are bought close to home and restaurants or clothing farther away (Agarwal, Jensen and
Monte, card transactions: median 4.2 km for food stores, 12.6 km for eating and drinking places, 9.0 km
overall; data/category_distance.json). The model keeps its overall trip length from the travel survey
(the calibration fits beta to it) and sets each category's multiplier on beta so the categories' typical
trip lengths (geometric mean, which moves smoothly) stand in the paper's ratios of medians. The fitted multipliers are written back to
data/category_distance.json and used by the build (params.category_beta_mult).

Usage: python3 -m nfprep.distance_fit      (after a build; the calibration follows)
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
FILE = DATA_DIR / "category_distance.json"
CITY = ROOT / "build" / "city_2025" / "city.json"
ROUNDS = 14


def medians(city: dict, beta: float) -> list[float]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "city.json"
        p.write_text(json.dumps(city, separators=(",", ":")))
        out = subprocess.run([str(ROOT / "engine/target/release/nfsim"), "category-km", str(p), "--beta", str(beta)],
                             check=True, capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def main() -> dict:
    doc = json.loads(FILE.read_text())
    city = json.loads(CITY.read_text())
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    names = city["categories"]["name"]
    budget = city["households"]["budget"]
    n = len(names)
    spend = [sum(budget[i::n]) for i in range(n)]
    target = [doc["categories"][c]["median_km"] / doc["all_sectors_median_km"] if c in doc["categories"] else None for c in names]
    mult = [float(doc.get("fitted_beta_mult", {}).get(c, 1.0)) for c in names]
    hist = []
    for _ in range(ROUNDS):
        city["params"]["category_beta_mult"] = mult
        m = medians(city, beta)
        idx = [i for i in range(n) if target[i] and m[i] > 0]
        w = [spend[i] for i in idx]
        # the scale that keeps the categories' overall level: spend-weighted geometric mean of model/target
        g = math.exp(sum(wi * math.log(m[i] / target[i]) for wi, i in zip(w, idx)) / sum(w))
        hist.append({names[i]: round(m[i] / g, 2) for i in idx})
        for i in idx:
            mult[i] *= (m[i] / (target[i] * g)) ** 0.5          # too far -> stronger decay
            mult[i] = min(max(mult[i], 0.3), 3.0)
    city["params"]["category_beta_mult"] = mult
    m = medians(city, beta)
    doc["fitted_beta_mult"] = {names[i]: round(mult[i], 4) for i in range(n)}
    doc["fit"] = {"model_median_km": {names[i]: round(m[i], 2) for i in range(n)},
                  "model_ratio_by_round": hist,
                  "target_ratio": {names[i]: round(target[i], 2) for i in range(n) if target[i]}}
    FILE.write_text(json.dumps(doc, indent=1))
    CITY.write_text(json.dumps(city, separators=(",", ":")))
    print("median trip km by category (model):", doc["fit"]["model_median_km"])
    print("multipliers:", doc["fitted_beta_mult"])
    return doc


if __name__ == "__main__":
    main()
