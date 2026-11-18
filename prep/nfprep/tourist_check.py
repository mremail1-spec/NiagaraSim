"""Simulated visitors against the travel surveys and the hotel market.

Runs one agent-mode year and compares who actually arrived (after hotels fill and parties are
turned away) with:
  - origin and overnight shares: Statistics Canada, Niagara Region 2017 (sim_parameters.json);
  - nights per overnight trip and where overnight visitors stay: 2022 domestic travel survey;
  - party size, parties with children, adults' ages: the survey's party profile;
  - season: 2022 domestic visits by quarter;
  - hotel occupancy by month: CBRE monthly reports for Niagara Falls (data/hotel_monthly_cbre.json).

Usage: python3 -m nfprep.tourist_check   (one agent year, a few minutes)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]


def share(d: dict) -> dict:
    tot = sum(d.values())
    return {k: round(100 * v / tot, 1) for k, v in d.items()}


def main(city_path: Path = ROOT / "build" / "city_2025" / "city.json") -> dict:
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = city_path.with_name("tourist_check_run.json")
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "agent", "--days", "365",
                    "--beta", str(beta), "--out", str(out)], check=True, stdout=subprocess.DEVNULL)
    rep = json.loads(out.read_text())["tourism"]
    city = json.loads(city_path.read_text())
    segs = [s["name"] for s in city["tourism"]["segments"]]
    p = rep["parties"]
    t = json.loads((DATA_DIR / "sim_parameters.json").read_text())["tourism"]

    persons = dict(zip(segs, p["persons_by_segment"]))
    origin_sim = {"domestic": persons["domestic_same_day"] + persons["domestic_overnight"],
                  "us": persons["us_same_day"] + persons["us_overnight"],
                  "overseas": persons["overseas_same_day"] + persons["overseas_overnight"]}
    d17 = t["annual_visitors_niagara_region"]["2017_detail"]
    origin_pub = {"domestic": d17["ontario"] + d17["other_canada"], "us": d17["usa"], "overseas": d17["overseas"]}
    overnight_sim = 100 * sum(v for k, v in persons.items() if k.endswith("overnight")) / sum(persons.values())

    nights = np.array(p["nights"])
    nb = {"1": nights[0], "2": nights[1], "3": nights[2], "4": nights[3], "5": nights[4], "6-9": nights[5:9].sum(), "10+": nights[9:].sum()}
    los = t["length_of_stay_2022_domestic"]
    nights_pub = {k: los[k] for k in ("1", "2", "3", "4", "5", "6-9", "10+")}

    gp = dict(zip(["hotel", "rental", "camping", "friends_relatives", "day"], p["group_persons"]))
    acc = t["accommodation_2022_domestic_overnight_visitors"]
    stay_pub = {"hotel": acc["hotels"] + acc["motels"], "rental": acc["other_roofed_commercial"], "camping": acc["camping_rv"],
                "friends_relatives": acc["private_homes_friends"] + acc["other"]}
    stay_sim = {k: gp[k] for k in stay_pub}

    size = np.array(p["size"])
    parties = size.sum()
    party = {"avg_size": {"published": t["party"]["avg_party_size"], "sim": round(float((size * np.arange(1, 13)).sum() / parties), 2)},
             "pct_with_children": {"published": t["party"]["pct_parties_with_children"], "sim": round(100 * p["with_children"] / parties, 1)},
             "adult_age_pct": {"published": t["party"]["adult_age_pct"],
                               "sim": dict(zip(["18-24", "25-34", "35-44", "45-54", "55-64", "65+"],
                                               [round(100 * x / parties, 1) for x in p["age_band"]]))}}

    monthly = np.array(rep["arrivals_by_segment_month"])
    dom = monthly[[segs.index("domestic_same_day"), segs.index("domestic_overnight")]].sum(axis=0)
    q = {f"Q{k + 1}": float(dom[3 * k:3 * k + 3].sum()) for k in range(4)}
    season = {"published_2022_domestic": t["seasonality_2022_domestic_visits_pct"], "sim_domestic": share(q)}

    cbre = json.loads((DATA_DIR / "hotel_monthly_cbre.json").read_text())["monthly"]
    occ = rep["hotel_occupancy_by_month"]
    hotel = {"rooms_in_model": rep["hotel_rooms"], "occupancy_year_sim": rep["hotel_occupancy"],
             "parties_turned_away": rep["parties_turned_away_from_full_lodging"],
             "by_month": {k: {"published": v["occupancy"], "sim": occ[int(k[5:]) - 1]} for k, v in sorted(cbre.items()) if k >= "2024"}}
    pub_m, sim_m = np.array([v["published"] for v in hotel["by_month"].values()]), np.array([v["sim"] for v in hotel["by_month"].values()])
    hotel["median_ratio_sim_to_published"] = round(float(np.median(sim_m / pub_m)), 3)

    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0]},
           "origin_pct_of_visitors": {"published_2017": share(origin_pub), "sim": share(origin_sim)},
           "overnight_pct_of_visitors": {"published_2017": round(100 * d17["overnight"] / d17["total"], 1), "sim": round(overnight_sim, 1)},
           "nights_pct_of_overnight_trips": {"published_2022_domestic": share(nights_pub), "sim": share(nb)},
           "where_overnight_visitors_stay_pct": {"published_2022_domestic": share(stay_pub), "sim": share(stay_sim)},
           "party": party, "season": season, "hotels": hotel}
    (ROOT / "docs" / "tourist_check.json").write_text(json.dumps(doc, indent=1))
    for k, v in doc.items():
        if k != "_meta":
            print(k, json.dumps(v))
    return doc


if __name__ == "__main__":
    main()
