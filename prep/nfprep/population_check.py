"""The simulated population against the 2021 census, tract by tract.

For each of the city's 21 census tracts and for the city as a whole: household size, income
(2020 incomes, as the census asked), owners and renters, mortgages, dwelling types, family types,
children, seniors and car commuting. Cars per household against the Transportation Tomorrow
Survey 2022 (Niagara Region; the census does not ask about cars).

Shares are compared, not counts: the simulated city is grown to 2025.
Usage: python3 -m nfprep.population_check
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
APARTMENT = {"apartment_under_5_storeys", "apartment_5plus_storeys", "duplex_apartment"}


def main(city_dir: Path = ROOT / "build" / "city_2025") -> dict:
    census = {r["census_tract"]: r for r in csv.DictReader(open(DATA_DIR / "tract_profiles.csv"))}
    hh = list(csv.DictReader(open(city_dir / "households.csv")))
    persons = list(csv.DictReader(open(city_dir / "persons.csv")))
    by = defaultdict(list)
    for h in hh:
        by[h["census_tract"]].append(h)
    pby = defaultdict(list)
    for p in persons:
        pby[p["census_tract"]].append(p)

    def sim_measures(hs, ps):
        n = len(hs)
        inc = np.array([float(h["income_2020"]) for h in hs])
        owners = [h for h in hs if h["tenure"] == "owner"]
        ages = np.array([int(p["age"]) for p in ps])
        typ = lambda t: 100 * sum(h["type"] == t for h in hs) / n
        return {"avg_hh_size": float(np.mean([int(h["size"]) for h in hs])),
                "median_hh_income": float(np.median(inc)),
                "pct_hh_income_100k_plus": 100 * float((inc >= 100_000).mean()),
                "pct_hh_income_under_40k": 100 * float((inc < 40_000).mean()),
                "pct_owner": 100 * len(owners) / n,
                "pct_owners_with_mortgage": 100 * sum(h["has_mortgage"] == "1" for h in owners) / max(len(owners), 1),
                "pct_single_detached": 100 * sum(h["dwelling"] == "single_detached" for h in hs) / n,
                "pct_apartments": 100 * sum(h["dwelling"] in APARTMENT for h in hs) / n,
                "pct_couple_with_children": typ("couple_with_children"), "pct_couple_no_children": typ("couple_no_children"),
                "pct_one_parent": typ("one_parent"), "pct_one_person": typ("one_person"),
                "pct_multigenerational": typ("multigenerational"),
                "pct_kids_0_17": 100 * float((ages < 18).mean()), "pct_seniors_65plus": 100 * float((ages >= 65).mean()),
                "pct_female": 100 * sum(p["sex"] == "F" for p in ps) / len(ps)}

    fields = list(sim_measures(hh[:50], persons[:120]).keys())
    rows, sim_all = [], sim_measures(hh, persons)
    for t, c in census.items():
        if t not in by:
            continue
        s = sim_measures(by[t], pby[t])
        rows.append({"tract": t, "community": c["community"], "households_sim": len(by[t]), "households_2021": float(c["households"]),
                     **{f: {"census": float(c[f]), "sim": round(s[f], 1)} for f in fields if c.get(f) not in (None, "")}})
    # city: census tracts weighted by households (persons for person shares)
    w_h = np.array([r["households_2021"] for r in rows])
    w_p = np.array([float(census[r["tract"]]["population_2021"]) for r in rows])
    city = {}
    for f in fields:
        vals = [(r[f]["census"], r[f]["sim"]) for r in rows if f in r]
        if len(vals) != len(rows):
            continue
        cen, sim = np.array(vals).T
        w = w_p if f in ("pct_kids_0_17", "pct_seniors_65plus", "pct_female") else w_h
        c_city = float(np.average(cen, weights=w)) if f != "median_hh_income" else None
        city[f] = {"census_city": None if c_city is None else round(c_city, 1), "sim_city": round(sim_all[f], 1),
                   "tract_correlation": round(float(np.corrcoef(cen, sim)[0, 1]), 3) if np.std(cen) > 0 else None,
                   "tract_mean_abs_diff": round(float(np.mean(np.abs(cen - sim))), 1)}
    # median income for the city: households in each tract placed at the tract median, weighted
    city["median_hh_income"]["census_city_approx"] = round(float(np.median(np.repeat(
        [r["median_hh_income"]["census"] for r in rows], [int(x) for x in w_h]))))
    # cars: TTS 2022 Niagara Region distribution
    tts = json.loads((DATA_DIR / "sim_parameters.json").read_text())["residents"]["transport"]["vehicle_ownership_niagara_region_TTS_2022"]
    veh = np.array([int(h["vehicles"]) for h in hh])
    cars = {"tts_2022_niagara_region": {"per_household": tts["vehicles_per_household"], "pct_by_count": tts["pct_households_by_vehicles"]},
            "sim": {"per_household": round(float(veh.mean()), 2),
                    "pct_by_count": {k: round(100 * float(m.mean()), 1) for k, m in
                                     {"0": veh == 0, "1": veh == 1, "2": veh == 2, "3": veh == 3, "4+": veh >= 4}.items()}},
            "note": "Region-wide survey; Niagara Falls has more renters and apartments than the region, so its true rate is likely a little lower."}
    doc = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "census": "data/tract_profiles.csv (2021 census profile, 21 tracts)"},
           "city": city, "cars": cars, "tracts": rows}
    (ROOT / "docs" / "population_check.json").write_text(json.dumps(doc, indent=1))
    print(f"{'measure':28s} {'census':>9s} {'sim':>9s} {'tract r':>8s} {'tract |diff|':>12s}")
    for f, v in city.items():
        cc = v.get("census_city") if v.get("census_city") is not None else v.get("census_city_approx")
        print(f"{f:28s} {cc!s:>9s} {v['sim_city']!s:>9s} {v['tract_correlation']!s:>8s} {v['tract_mean_abs_diff']!s:>12s}")
    print("cars", cars["tts_2022_niagara_region"], cars["sim"])
    return doc


if __name__ == "__main__":
    main()
