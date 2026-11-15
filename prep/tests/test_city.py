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
    assert sum(1 for b in city["businesses"] if b["source"] == "osm") == 1128


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
