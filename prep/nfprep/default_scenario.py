"""The model's default: the scenario that matches the chains' own published sales most closely.

Chains report sales per location in audited filings (Dollarama, Shoppers Drug Mart, Canadian Tire,
the restaurant royalty funds...), so they are the most trusted check on what a single business sells
(the owner's rule: they are most exposed to audit and least motivated to misreport). Among the
scenarios that fit every measured fact (nfprep.ensemble), the default is the one closest to the
chains. Its settings are kept in data/default_scenario.json and applied to the calibrated city at
every rebuild; the city as calibrated is kept beside it (city_base.json) for the ensemble.

Usage: python3 -m nfprep.default_scenario choose   (pick from build/ensemble/runs.jsonl)
       python3 -m nfprep.default_scenario apply    (rebuild step, after build2)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
FILE = DATA_DIR / "default_scenario.json"
CITY = ROOT / "build" / "city_2025" / "city.json"
BASE = CITY.with_name("city_base.json")
CAL = DATA_DIR / "calibrated_multipliers.json"


def choose() -> dict:
    runs = [json.loads(l) for l in open(ROOT / "build" / "ensemble" / "runs.jsonl")]
    tp = ROOT / "build" / "ensemble" / "traffic.jsonl"
    traffic = {json.loads(l)["run"]: json.loads(l) for l in open(tp)} if tp.exists() else {}
    ok = [(i, r) for i, r in enumerate(runs) if r["max_implausibility"] <= 1.0 and "chains_rms_log" in r
          and r.get("chains_above_best", 0) == min(x.get("chains_above_best", 0) for x in runs if x["max_implausibility"] <= 1.0)]
    # traffic-tested runs must also pass the road counts
    ok = [(i, r) for i, r in ok if i not in traffic or (traffic[i]["regional_2019"] >= 0.6 and traffic[i]["highways"] >= 0.6)]
    i, best = min(ok, key=lambda x: x[1]["chains_rms_log"])
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0],
                     "chosen_from": f"{len(ok)} scenarios that fit every fact (run {i} of {len(runs)})",
                     "chains_by_brand_model_over_published": best["chains_by_brand"],
                     "chains_typical_error_factor": round(2.718281828 ** best["chains_rms_log"], 3)},
           "knobs": best["knobs"]}
    FILE.write_text(json.dumps(doc, indent=1))
    print(json.dumps(doc, indent=1))
    return doc


def fit_brands(city: dict, beta: float, rounds: int = 8) -> dict:
    """Each chain's pull, fitted so its branches' median sales match its published sales per store.
    Spending stays in the same kind of business; it moves between the chain and its competitors."""
    import csv, math, re, subprocess, tempfile
    import numpy as np
    bench = json.loads((DATA_DIR / "public_unit_sales_2025.json").read_text())["brands"]
    b = city["businesses"]
    cats = city["sim_categories"]
    groups = {brand: [i for i, nm in enumerate(b["name"]) if re.search(v["pattern"], nm.lower())
                      and ("category" not in v or cats[b["sim_category"][i]] == v["category"])]
              for brand, v in bench.items()}
    groups = {k: v for k, v in groups.items() if v}
    mult = {k: 1.0 for k in groups}
    hist = []
    for _ in range(rounds):
        with tempfile.TemporaryDirectory() as d:
            cp, bp = Path(d) / "city.json", Path(d) / "biz.csv"
            cp.write_text(json.dumps(city, separators=(",", ":")))
            subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(cp), "--mode", "flow", "--days", "365",
                            "--beta", str(beta), "--businesses", str(bp)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rev = {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(bp))}
        ratios = {k: float(np.median([rev.get(i, 0) for i in ids])) / bench[k]["average"] for k, ids in groups.items()}
        hist.append({k: round(v, 3) for k, v in ratios.items()})
        for k, ids in groups.items():
            step = (1.0 / max(ratios[k], 1e-3)) ** 0.7          # damped: competitors react
            step = min(max(step, 0.25), 4.0)
            mult[k] *= step
            for i in ids:
                b["format_weight"][i] *= step
    print("chain sales per store, model / published, by round:")
    for h in hist:
        print("  ", h)
    return {"multiplier": {k: round(v, 4) for k, v in mult.items()}, "ratio_by_round": hist}


def apply() -> None:
    from .ensemble import apply as apply_knobs
    if not FILE.exists():
        print("no default scenario chosen yet; the calibrated city is the default")
        return
    k = json.loads(FILE.read_text())["knobs"]
    shutil.copy(CITY, BASE)
    city = json.loads(CITY.read_text())
    city = apply_knobs(city, k)
    city.setdefault("meta", {})["default_scenario"] = k
    CITY.write_text(json.dumps(city, separators=(",", ":")))
    cal = json.loads(CAL.read_text())
    beta = cal.get("beta_fit", cal["beta"]) if cal.get("beta_fit_source") == "calibrate" else cal["beta"]
    fit = fit_brands(city, beta * k["beta_mult"])
    city.setdefault("meta", {})["brand_pull"] = fit
    CITY.write_text(json.dumps(city, separators=(",", ":")))
    doc = json.loads(FILE.read_text())
    doc["brand_pull"] = fit
    FILE.write_text(json.dumps(doc, indent=1))
    if cal.get("beta_fit_source") != "calibrate":     # first apply since calibration: keep the fitted beta
        cal["beta_fit"], cal["beta_fit_source"] = cal["beta"], "calibrate"
    cal["beta"] = round(cal["beta_fit"] * k["beta_mult"], 6)
    CAL.write_text(json.dumps(cal, indent=1))
    print(f"default scenario applied; beta {cal['beta_fit']} x {k['beta_mult']:.3f} = {cal['beta']}")


if __name__ == "__main__":
    {"choose": choose, "apply": apply}[sys.argv[1]]()
