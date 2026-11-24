"""Model revenue of chain branches in Niagara Falls against the chains' published sales per location.

Usage: python3 -m nfprep.unit_sales_check   (writes docs/unit_sales_check.json)
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]


def main(city_path: Path = ROOT / "build" / "city_2025" / "city.json") -> dict:
    bench = json.loads((DATA_DIR / "public_unit_sales_2025.json").read_text())["brands"]
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = city_path.with_name("unit_sales_revenue.csv")
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "flow", "--days", "365",
                    "--beta", str(beta), "--businesses", str(out)], check=True, stdout=subprocess.DEVNULL)
    rev = {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(out))}
    b = json.loads(city_path.read_text())["businesses"]
    result, ratios = {}, []
    for brand, v in bench.items():
        cats = json.loads(city_path.read_text())["sim_categories"]
        ids = [i for i, n in enumerate(b["name"]) if re.search(v["pattern"], n.lower()) and i in rev
               and ("category" not in v or cats[b["sim_category"][i]] == v["category"])]
        vals = [rev[i] for i in ids]
        if not vals:
            continue
        med = float(np.median(vals))
        ratios.append(med / v["average"])
        result[brand] = {"branches": [{"address": b["address"][i], "model_revenue": round(rev[i])} for i in ids],
                         "model_median": round(med), "published_average": v["average"], "ratio": round(med / v["average"], 2),
                         "above_brand_best_unit": sum(1 for x in vals if v["high"] and x > v["high"]), "source_quality": v["quality"]}
    summary = {"median_ratio_model_to_published": round(float(np.median(ratios)), 2),
               "branches_above_brand_best": sum(r["above_brand_best_unit"] for r in result.values())}
    # model error for a single site: root mean square of the log ratios (bias included), so a forecast
    # range of exp(+-1.28 sd) holds the real figure about 8 times in 10 if the brands are typical
    sd = float(np.sqrt(np.mean(np.log(ratios) ** 2)))
    summary["site_error_log_sd"] = round(sd, 3)
    summary["site_range_p10_p90"] = [round(float(np.exp(-1.28 * sd)), 2), round(float(np.exp(1.28 * sd)), 2)]
    if not json.loads(city_path.read_text()).get("meta", {}).get("brand_pull"):   # fitted chains say nothing about error
      (DATA_DIR / "forecast_error.json").write_text(json.dumps({
        "_meta": {"what": "Spread of model revenue for a single site against published chain sales per location (docs/unit_sales_check.json). "
                          f"From {len(ratios)} chains only, so a rough measure; written by prep/nfprep/unit_sales_check.py."},
        "log_sd": round(sd, 3), "p10_factor": summary["site_range_p10_p90"][0], "p90_factor": summary["site_range_p10_p90"][1],
        "chains": len(ratios)}, indent=1))
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0]}, "summary": summary, "brands": result}
    (ROOT / "docs" / "unit_sales_check.json").write_text(json.dumps(doc, indent=1))
    for k, r in result.items():
        print(f"{k:13s} {len(r['branches'])} branches  model median ${r['model_median']/1e6:.2f}M  published ${r['published_average']/1e6:.2f}M  ratio {r['ratio']}"
              + (f"  above best unit: {r['above_brand_best_unit']}" if r['above_brand_best_unit'] else ""))
    print(summary)
    return doc


if __name__ == "__main__":
    main()
