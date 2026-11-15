"""Phase 1: load and validate the Niagara Falls data pack.

`load_pack()` reads every file the model uses and returns a `Pack`.
`validate(pack)` returns a list of (check name, passed, detail) so the same
checks drive both the command-line report and the test suite.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# file -> expected data rows (from HANDOVER.md section 3 and the top-up README)
EXPECTED_ROWS = {
    "tract_profiles.csv": 21,
    "tract_demographics.csv": 21,
    "tract_growth_2021_2025.csv": 21,
    "tract_distances.csv": 441,
    "tract_place_access.csv": 651,
    "census_tract_long.csv": 155_917,
    "places.csv": 1_128,
    "places_tract_lookup.csv": 1_128,
    "licensed_tourism_businesses.csv": 915,
    "business_revenue_benchmarks.csv": 107,
    "cra_tax_by_postal_area_2021.csv": 360,
    "wages_by_occupation_hamilton_niagara_2025.csv": 516,
    "avg_weekly_earnings_by_industry_ontario.csv": 165,
    "insolvencies_by_postal_area_2023_2025.csv": 12,
}
EXPECTED_FEATURES = {
    "census_tracts_boundaries.geojson": 21,
    "neighbourhood_boundaries.geojson": 67,
    "tourist_core_boundary.geojson": 1,
    "places.geojson": 1_128,
}
JSON_FILES = [
    "sim_parameters.json", "behaviour_parameters.json", "energy_parameters.json",
    "spending_by_demographic.json", "earnings_index_ontario.json", "labour_rates_ontario_2025.json",
]
CENSUS_POPULATION_2021 = 94_415
CENSUS_HOUSEHOLDS_2021 = 37_785
POPULATION_2025 = 113_983


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Pack:
    tables: dict[str, list[dict]] = field(default_factory=dict)
    geo: dict[str, dict] = field(default_factory=dict)
    params: dict[str, dict] = field(default_factory=dict)
    census: dict[str, dict[str, str]] = field(default_factory=dict)  # tract -> field code -> value

    @property
    def tracts(self) -> list[str]:
        return sorted(r["census_tract"] for r in self.tables["tract_profiles.csv"])

    def by_tract(self, table: str) -> dict[str, dict]:
        return {r["census_tract"]: r for r in self.tables[table]}

    def census_num(self, tract: str, code: str) -> float:
        v = self.census[tract].get(code, "")
        try:
            return float(v)
        except ValueError:
            return float("nan")


def load_pack(data_dir: Path = DATA_DIR) -> Pack:
    pack = Pack()
    for name in list(EXPECTED_ROWS) + ["address_points.csv"]:
        pack.tables[name] = read_csv(data_dir / name)
    for name in EXPECTED_FEATURES:
        pack.geo[name] = json.loads((data_dir / name).read_text(encoding="utf-8"))
    for name in JSON_FILES:
        pack.params[name] = json.loads((data_dir / name).read_text(encoding="utf-8"))
    for r in pack.tables["census_tract_long.csv"]:
        pack.census.setdefault(r["census_tract"], {})[r["field"]] = r["value"]
    return pack


def validate(pack: Pack) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        out.append((name, bool(ok), detail))

    for name, n in EXPECTED_ROWS.items():
        got = len(pack.tables[name])
        check(f"rows {name}", got == n, f"{got} (expected {n})")
    for name, n in EXPECTED_FEATURES.items():
        got = len(pack.geo[name]["features"])
        check(f"features {name}", got == n, f"{got} (expected {n})")

    prof = pack.tables["tract_profiles.csv"]
    demo = pack.tables["tract_demographics.csv"]
    pop = sum(float(r["population_2021"]) for r in prof)
    hh = sum(float(r["households"]) for r in prof)
    check("tract population sums to census", pop == CENSUS_POPULATION_2021, f"{pop:.0f}")
    check("tract households sum to census", hh == CENSUS_HOUSEHOLDS_2021, f"{hh:.0f}")
    check("demographics population matches profiles",
          sum(float(r["population_2021"]) for r in demo) == pop, "")
    sexes = sum(float(r["male_total"]) + float(r["female_total"]) for r in demo)
    # census counts are randomly rounded to 5, so sums differ slightly from the total
    check("male + female within 0.1% of population", abs(sexes - pop) / pop < 0.001, f"{sexes:.0f}")

    tracts = set(pack.tracts)
    check("21 distinct tracts", len(tracts) == 21, str(len(tracts)))
    geo_tracts = {f["properties"]["census_tract"] for f in pack.geo["census_tracts_boundaries.geojson"]["features"]}
    check("boundary file covers the same tracts", geo_tracts == tracts, "")
    check("census long file covers the same tracts", set(pack.census) == tracts, "")
    check("every place has a tract",
          all(r["census_tract"] in tracts for r in pack.tables["places_tract_lookup.csv"]), "")
    bad = [r for r in pack.tables["places.csv"]
           if not (42.9 < float(r["lat"]) < 43.2 and -79.25 < float(r["lon"]) < -78.95)]
    check("place coordinates inside the city box", not bad, f"{len(bad)} outside")

    est = pack.params["sim_parameters.json"]["residents"]["population"]
    check("2025 population estimate present", est["statcan_estimate_july_2025"] == POPULATION_2025, "")
    check("population estimates 2019-2025 present",
          all(str(y) in est["statcan_estimates_by_year"] for y in range(2019, 2026)), "")
    growth = sum(int(r["new_lowrise_units_permits_2021_2023"]) for r in pack.tables["tract_growth_2021_2025.csv"])
    check("new-housing permits total", growth == 1234, str(growth))
    check("address points loaded", len(pack.tables["address_points.csv"]) > 30_000,
          str(len(pack.tables["address_points.csv"])))
    food = [r for r in pack.tables["business_revenue_benchmarks.csv"] if r["naics"] == "722"]
    check("food-service employer count is 326",
          bool(food) and int(float(food[0]["nf_employer_businesses_2026"])) == 326,
          food[0]["nf_employer_businesses_2026"] if food else "NAICS 722 row missing")
    return out


def main() -> int:
    results = validate(load_pack())
    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
