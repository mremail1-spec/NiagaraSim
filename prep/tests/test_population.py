"""Phase 2 proof: the synthetic population reproduces the calibration targets.

Targets are from HANDOVER.md section 6 and the 2021 Census city totals.
"""
import collections

import numpy as np
import pytest

from nfprep.loader import load_pack
from nfprep.population import fingerprint, generate, summarise, tract_population_targets


@pytest.fixture(scope="session")
def pack():
    return load_pack()


@pytest.fixture(scope="session")
def pop(pack):
    return generate(pack, year=2025, seed=1)


@pytest.fixture(scope="session")
def report(pop):
    return summarise(*pop)


def within(value, target, pct):
    return abs(value - target) <= abs(target) * pct / 100


def test_population_2025(report):
    assert within(report["population"], 113_983, 0.5), report["population"]


def test_median_household_income_2020(report):
    assert within(report["median_household_income_2020"], 74_500, 2.0), report["median_household_income_2020"]


def test_owner_share(report):
    assert abs(report["pct_owner"] - 70.4) <= 1.0, report["pct_owner"]


def test_owners_with_mortgage(report):
    assert abs(report["pct_owners_with_mortgage"] - 59.1) <= 1.5, report["pct_owners_with_mortgage"]


def test_vehicles_per_household(report):
    assert abs(report["vehicles_per_household"] - 1.75) <= 0.02, report["vehicles_per_household"]


def test_age_and_sex_match_census(report):
    # 2021 Census city shares: 13,880 aged 0-14 and 20,655 aged 65+ of 94,415; 48,535 female of 94,420
    assert abs(report["pct_age_0_14"] - 14.70) <= 1.0, report["pct_age_0_14"]
    assert abs(report["pct_age_65_plus"] - 21.88) <= 1.0, report["pct_age_65_plus"]
    assert abs(report["pct_female"] - 51.40) <= 0.7, report["pct_female"]


def test_heating_shares(report):
    assert abs(report["pct_heating_gas"] - 70) <= 1.5
    assert abs(report["pct_heating_electric"] - 18) <= 1.5


def test_household_size_distribution(pop):
    # 2021 Census: 10,550 / 12,930 / 6,010 / 5,195 / 3,150 households of size 1 / 2 / 3 / 4 / 5+
    target = np.array([10550, 12930, 6010, 5195, 3150]) / 37835
    sizes = collections.Counter(min(h["size"], 5) for h in pop[0])
    got = np.array([sizes[k] for k in range(1, 6)]) / len(pop[0])
    assert np.abs(got - target).max() <= 0.015, got


def test_household_types(pop):
    # 2021 Census: one-person 10,550, couples with children 9,095, one-parent 4,225 of 37,785
    n = len(pop[0])
    types = collections.Counter(h["type"] for h in pop[0])
    assert abs(types["one_person"] / n - 10550 / 37785) <= 0.015
    assert abs(types["couple_with_children"] / n - 9095 / 37785) <= 0.015
    assert abs(types["one_parent"] / n - 4225 / 37785) <= 0.015


def test_every_tract_hits_its_population_target(pack, pop):
    targets = tract_population_targets(pack, 2025)
    assert abs(sum(targets.values()) - 113_983) < 1
    got = collections.Counter(p["census_tract"] for p in pop[1])
    worst = max(abs(got[t] - targets[t]) / targets[t] for t in targets)
    assert worst <= 0.03, worst


def test_growth_goes_where_housing_was_built(pack):
    t21, t25 = tract_population_targets(pack, 2021), tract_population_targets(pack, 2025)
    growth = {t: t25[t] / t21[t] - 1 for t in t21}
    # 0202.00 took 24% of new homes on 3% of the city's households; 0212.00 had no permits
    assert max(growth, key=growth.get) == "0202.00"
    assert growth["0202.00"] > 2 * growth["0212.00"]


def test_people_are_consistent(pop):
    households, persons = pop
    size = collections.Counter(p["household_id"] for p in persons)
    assert all(size[h["household_id"]] == h["size"] for h in households)
    assert all(h["adults"] >= 1 for h in households)
    assert all(not p["employed"] or p["age"] >= 15 for p in persons)
    assert all(42.9 < h["lat"] < 43.2 and -79.25 < h["lon"] < -78.95 for h in households)
    assert all(h["has_mortgage"] == 0 for h in households if h["tenure"] == "renter")


def test_same_seed_gives_identical_city(pack, pop):
    assert fingerprint(*generate(pack, year=2025, seed=1)) == fingerprint(*pop)
    assert fingerprint(*generate(pack, year=2025, seed=2)) != fingerprint(*pop)


def test_historical_years(pack):
    # Statistics Canada estimates: 96,078 (2019) and 100,351 (2022)
    for year, target in ((2019, 96_078), (2022, 100_351)):
        _, persons = generate(pack, year=year, seed=1)
        assert within(len(persons), target, 0.7), (year, len(persons))
