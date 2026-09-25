"""The behaviour contract, run against this repo's own default config.

The same cases run in the gitops repo against the deployed configmap. That is
the drift guard: the two files are not the same and are not meant to be, so
what is pinned is what they do.
"""
import pathlib

import pytest
import yaml

from jobscout.filters import location_matches, title_matches

CASES = yaml.safe_load(
    (pathlib.Path(__file__).parent / "contract_cases.yaml").read_text()
)


@pytest.mark.parametrize("title,expected", CASES["titles"])
def test_title_contract(title, expected, config):
    assert title_matches(title, config) is expected


@pytest.mark.parametrize("location,expected", CASES["locations"])
def test_location_contract(location, expected, config):
    assert location_matches(location, config) is expected


@pytest.mark.parametrize("title,employment,expected", CASES["levels"])
def test_level_contract(title, employment, expected, config):
    from jobscout.filters import posting_level_matches
    from jobscout.models import Posting

    posting = Posting(source="contract", company="", title=title, location="",
                      url="", employment_type=employment)
    assert posting_level_matches(posting, config) is expected
