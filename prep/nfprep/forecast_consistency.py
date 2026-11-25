"""Does the newcomer forecast agree with the model's own sales for the same business?

For a sample of existing businesses of each kind: take the business out, forecast a newcomer of its own
size and format at its own address, and compare with what the model says it sells now. The opportunity
scan compares newcomer forecasts with existing businesses' sales, so the two must agree on average;
a ratio above 1 means newcomers look better than they are.
Usage: python3 -m nfprep.forecast_consistency [per_kind]
"""
import csv, json, random, subprocess, sys, tempfile
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NFSIM = str(ROOT / "engine/target/release/nfsim")
KINDS = ["fast_food", "cafe", "restaurant", "grocery_food", "pharmacy", "general_merchandise", "personal_services", "fuel"]


def main(per_kind: int = 25) -> dict:
    city = json.load(open(ROOT / "build/city_2025/city.json")); b = city["businesses"]; cats = city["sim_categories"]
    cal = json.load(open(ROOT / "data/calibrated_multipliers.json")); beta = cal.get("beta_fit", cal["beta"])
    with tempfile.TemporaryDirectory() as d:
        bp = Path(d) / "b.csv"
        subprocess.run([NFSIM, "run", str(ROOT / "build/city_2025/city.json"), "--mode", "flow", "--days", "365", "--beta", str(beta),
                        "--closures-as-of", "209912", "--businesses", str(bp)], check=True, capture_output=True)
        rev = {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(bp))}
    rng = random.Random(1)

    def one(i):
        c = json.loads(json.dumps(city))
        c["businesses"]["closed_from"][i] = "200001"
        with tempfile.TemporaryDirectory() as d:
            cp, sp, op = Path(d) / "c.json", Path(d) / "s.json", Path(d) / "o.json"
            cp.write_text(json.dumps(c, separators=(",", ":")))
            sp.write_text(json.dumps([[b["lat"][i], b["lon"][i], b["sim_category"][i], b["revenue_prior"][i], b["format_weight"][i]]]))
            subprocess.run([NFSIM, "forecast", str(cp), "--beta", str(beta), "--closures-as-of", "209912", "--sites", str(sp), "--out", str(op)],
                           check=True, capture_output=True)
            return float(np.array(json.load(open(op)), float).sum())

    out = {}
    for k in KINDS:
        ids = [i for i in range(len(b["node"])) if cats[b["sim_category"][i]] == k and not b["external"][i] and not b["closed_from"][i] and rev.get(i, 0) > 0]
        ids = rng.sample(ids, min(per_kind, len(ids)))
        with ThreadPoolExecutor(4) as ex:
            f = list(ex.map(one, ids))
        r = np.array([fi / rev[i] for fi, i in zip(f, ids)])
        out[k] = {"sampled": len(ids), "forecast_over_model_median": round(float(np.median(r)), 2),
                  "p25": round(float(np.percentile(r, 25)), 2), "p75": round(float(np.percentile(r, 75)), 2)}
        print(k, out[k], flush=True)
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0]}, "by_kind": out}
    (ROOT / "docs/forecast_consistency.json").write_text(json.dumps(doc, indent=1))
    return doc


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 25)
