"""Niagara Parks lot counts (2000-2004) against the model.

1. Split between the Falls lots and Rapidsview: the model's parking sales at the two against the
   measured vehicles. This is fitted (data/niagara_parks_places.json 'pull'), so it is reported, not scored.
2. SARS year: Falls lot vehicles 2003 against 2002 against the Conditions panel's health-scare card,
   blended by the model's visitor mix. The card's US figure is measured from crossings; its domestic
   figure was a seed (0.95) and is now set from this lot drop (0.85), so this is reported, not scored.
3. Highway 420 in 2003: if visitors were a fifth of its traffic it should have fallen about 4% when
   the Falls lot fell 19%. It is the test of the replay's implied visitor share.

Usage: python3 -m nfprep.parking_check   (runs one model year, about a minute)
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
from pathlib import Path

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]


def main(city_path: Path = ROOT / "build" / "city_2025" / "city.json") -> dict:
    hist = json.loads((DATA_DIR / "niagara_parks_parking_history.json").read_text())
    falls, rapids = hist["lots"]["falls"], hist["lots"]["rapids_view"]
    years = sorted(falls)
    measured = {y: round(falls[y] / rapids[y], 2) for y in years}

    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = city_path.with_name("parking_check_revenue.csv")
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "flow", "--days", "365",
                    "--beta", str(beta), "--businesses", str(out)], check=True, stdout=subprocess.DEVNULL)
    rev = {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(out))}
    names = json.loads(city_path.read_text())["businesses"]["name"]
    lots = {n: rev.get(i, 0.0) for i, n in enumerate(names) if n.startswith("Niagara Parks") and "Parking" in n}
    m_falls = sum(v for n, v in lots.items() if "Lot A" in n or "Overflow" in n)
    m_rapids = sum(v for n, v in lots.items() if "Rapidsview" in n)
    model_ratio = round(m_falls / m_rapids, 2)

    policy = json.loads((DATA_DIR / "policy_levers.json").read_text())
    card = policy["events"]["health"]
    mix = json.loads(city_path.with_name("report.json").read_text())["tourism_checks"]
    w = {"domestic": mix["regional_domestic"], "us": mix["regional_us"], "overseas": mix["regional_overseas"]}
    blend = lambda c: sum(w[k] * c[k] for k in w) / sum(w.values())
    card_mult = round(blend(card), 3)
    card_before = round(blend({**card, "domestic": 0.95}), 3)
    lot_drop = round(falls["2003"] / falls["2002"], 3)
    sars_err = round(abs(math.log(card_mult / lot_drop)), 3)
    # domestic multiplier that fits the lot, with today's mix and with a mix 40% US / 8% overseas (closer to 2003)
    fit_today = round((lot_drop * sum(w.values()) - w["us"] * card["us"] - w["overseas"] * card["overseas"]) / w["domestic"], 3)
    fit_2003 = round((lot_drop - 0.4 * card["us"] - 0.08 * card["overseas"]) / 0.52, 3)

    sections = json.loads((DATA_DIR / "traffic_history_mto_1988_2024.json").read_text())["sections"]
    h420 = {}
    for k, v in sections.items():
        if k.startswith("420"):
            a = v["aadt"]
            trend = (a["2002"] / a["1999"]) ** (1 / 3)               # usual yearly growth before 2003
            r = a["2003"] / a["2002"] / trend
            h420[v["description"]] = {"2003_vs_2002": round(a["2003"] / a["2002"], 3), "usual_year": round(trend, 3),
                                      "visitor_share_that_fits": round((1 - r) / (1 - falls["2003"] / falls["2002"]), 2)}
    gap = sum(x["2003_vs_2002"] / x["usual_year"] for x in h420.values()) / len(h420)
    # visitor share s that fits: (1 - s) + s * lot_drop = gap
    implied_share = round((1 - gap) / (1 - lot_drop), 2)

    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "data": "data/niagara_parks_parking_history.json"},
           "split_falls_to_rapidsview": {"measured_by_year": measured, "model": model_ratio,
                                         "model_lot_sales": {n: round(v) for n, v in lots.items()}, "fitted": True},
           "sars_2003": {"falls_lot_2003_vs_2002": lot_drop, "all_lots_2003_vs_2002": round(hist["totals"]["2003"] / hist["totals"]["2002"], 3),
                         "health_card_blended": card_mult, "abs_log_error": sars_err,
                         "health_card_before_with_domestic_seed_0.95": card_before,
                         "domestic_multiplier_that_fits": {"todays_mix": fit_today, "mix_40pct_us": fit_2003},
                         "reading": "SARS was centred in Toronto, so Ontario visitors stayed away too: domestic about x0.83-0.86, not the seed x0.95."},
           "highway_420_2003": {"sections": h420, "average_vs_usual_year": round(gap, 3),
                                "visitor_share_that_fits": implied_share,
                                "reading": "Averaged over its sections Highway 420 grew at its usual rate in 2003 while the Falls lot fell 19%. "
                                           "The Stanley Avenue end, nearest the Falls, fell 2.7% below its usual year, which fits visitors at about "
                                           "15% of its traffic; the model has 15%. The replay's 20-30% is not supported. MTO between-count years "
                                           "are partly estimated, so this is loose."}}
    (ROOT / "docs" / "parking_check.json").write_text(json.dumps(doc, indent=1))
    print("Falls:Rapidsview measured", measured, "model", model_ratio)
    print("SARS: Falls lot", lot_drop, "health card", card_mult, "error", sars_err)
    print("Hwy 420 2003 vs usual year", round(gap, 3), "visitor share that fits", implied_share)
    for k, x in h420.items():
        print("  ", k, x)
    return doc


if __name__ == "__main__":
    main()
