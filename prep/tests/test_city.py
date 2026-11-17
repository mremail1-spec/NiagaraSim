"""Phase 3 proof: businesses match the business register and everything connects to the roads."""
import collections

import pytest

from nfprep.build import build_city, load_full_pack
from nfprep.roads import strongly_reachable


@pytest.fixture(scope="session")
def city():
    return build_city(2025, 1, load_full_pack())


def employers(city, naics):
    return sum(1 for b in city["businesses"] if b["naics"] == naics and b["employer"])


def test_food_service_employers_match_register(city):
    assert employers(city, "722") == 326


@pytest.mark.parametrize("naics,count", [("721", 76), ("713", 48), ("445", 98), ("455", 30), ("456", 65),
                                         ("457", 29), ("458", 30), ("459", 94), ("621", 221), ("811", 77),
                                         ("812", 74), ("532", 20), ("441", 21)])
def test_employer_counts_match_register(city, naics, count):
    assert employers(city, naics) == count


def test_size_bands_match_register_for_food_service(city):
    # register, June 2026: 78 / 82 / 62 / 57 / 39 / 8 food-service employers by size band
    bands = collections.Counter(b["size_band"] for b in city["businesses"] if b["naics"] == "722" and b["employer"])
    assert [bands[k] for k in ("nf_1_4", "nf_5_9", "nf_10_19", "nf_20_49", "nf_50_99", "nf_100_plus")] == [78, 82, 62, 57, 39, 8]


def test_all_mapped_places_are_kept(city):
    ids = {b["osm_id"] for b in city["businesses"]}
    original = [r["osm_id"] for r in city["pack"].tables["places.csv"]]
    assert len(original) == 1128 and all(i in ids for i in original)


def test_places_outside_the_city_are_not_added(city):
    # the wider map download reaches into Niagara Falls, New York (Pine Avenue)
    assert not any("Pine Av" in b["address"] for b in city["businesses"])


def test_most_real_businesses_have_a_name_and_address(city):
    real = [b for b in city["businesses"] if b["source"] != "synthetic"]
    assert sum(1 for b in real if not b["address"]) / len(real) < 0.03
    assert sum(1 for b in real if not b["name"].strip()) / len(real) < 0.03


def test_businesses_are_complete(city):
    tracts = {h["census_tract"] for h in city["households"]}
    for b in city["businesses"]:
        assert b["census_tract"] in tracts
        assert b["in_tourist_core"] in (0, 1)
        assert 0 <= b["open_hour"] < b["close_hour"] <= 30
        assert b["staff"] >= (1 if b["employer"] else 0)
        if b["employer"] and b["naics"] in ("722", "721", "713", "445", "459"):
            assert b["revenue_prior"] >= 30_000


def test_tourist_core_businesses_sit_in_upper_revenue(city):
    food = [b for b in city["businesses"] if b["naics"] == "722" and b["employer"]]
    core = sorted(b["revenue_prior"] for b in food if b["in_tourist_core"])
    rest = sorted(b["revenue_prior"] for b in food if not b["in_tourist_core"])
    assert core[len(core) // 2] > rest[len(rest) // 2]


def test_road_network_is_fully_connected(city):
    n = len(city["road_nodes"])
    assert n > 3000
    assert strongly_reachable(city["road_nodes"], city["road_edges"]) == (n, n)


def test_every_home_and_business_reaches_a_road(city):
    n = len(city["road_nodes"])
    homes = sorted(h["road_snap_m"] for h in city["households"])
    assert all(0 <= h["road_node"] < n for h in city["households"])
    assert all(0 <= b["road_node"] < n for b in city["businesses"])
    assert homes[len(homes) // 2] < 100
    assert homes[int(len(homes) * 0.99)] < 400
    assert max(b["road_snap_m"] for b in city["businesses"]) < 800


def test_road_speeds_and_lengths_are_sane(city):
    for e in city["road_edges"]:
        assert e["length_m"] > 0 and 10 <= e["speed_kmh"] <= 110   # lowest posted limit on the map is 15 km/h
        assert e["from_node"] != e["to_node"]


def test_outlook_ranges_come_from_monthly_data():
    import json
    from nfprep import outlook
    sc = json.loads((outlook.DATA / "scenario_ranges.json").read_text())
    for k in ["us", "domestic", "overseas", "base_stays", "resident"]:
        assert sc["low"][k] <= sc["expected"][k] <= sc["high"][k], k
        assert sc["calibrated"][k] == 1.0
    d = outlook.load()
    last = sc["_meta"]["latest_month"]
    assert abs(sc["expected"]["us"] - outlook.same_months(d, "us_overnight", int(last[:4]), last)) < 0.001
    # the US low end allows a repeat of 2025's political drop
    assert sc["low"]["us"] <= sc["expected"]["us"] * outlook.year_change(d, "us_overnight", 2025) + 1e-3
    # out of sample, the 10-90 band should hold the next year about 8 times in 10
    rows = outlook.check(d, "us_overnight")
    assert len(rows) >= 8 and sum(r["inside"] for r in rows) / len(rows) >= 0.6


def test_statcan_closing_rates_cover_the_survival_years():
    import json
    from nfprep.survival import statcan_rates, DATA_DIR, YEARS
    sc = json.loads((DATA_DIR / "business_dynamics_statcan.json").read_text())["st_catharines_niagara_cma"]
    r = statcan_rates({k.split(" | ")[1]: v for k, v in sc.items()})
    assert set(YEARS) <= set(r)
    assert r["2020"] > r["2019"] + 0.1 and 0.3 < r["2019"] < 0.7
