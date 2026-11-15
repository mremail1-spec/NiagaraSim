"""Phase 1 proof: every file loads and the census totals add up."""
import pytest

from nfprep.loader import load_pack, validate


@pytest.fixture(scope="session")
def pack():
    return load_pack()


def test_all_validation_checks_pass(pack):
    failed = [(name, detail) for name, ok, detail in validate(pack) if not ok]
    assert not failed, failed


def test_census_totals(pack):
    prof = pack.tables["tract_profiles.csv"]
    assert sum(float(r["population_2021"]) for r in prof) == 94_415
    assert sum(float(r["households"]) for r in prof) == 37_785
