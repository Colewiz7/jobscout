"""Title, location and term rules."""
import pytest

from jobscout.filters import extract_terms, keep, location_matches, title_matches
from jobscout.models import Posting


@pytest.mark.parametrize("title,expected", [
    ("Cloud Applications Development Intern", True),
    ("Platform System Engineering Co-op", True),
    ("Site Reliability Engineering Intern", True),
    ("DevOps Co-Op", True),
    ("SRE Intern", True),
    ("Software Engineer Intern", False),          # no infrastructure signal
    ("Cloud Solutions Architect", False),         # not an internship
    ("Internal Platform Engineer", False),        # "Internal" is not "Intern"
    ("International Cloud Analyst", False),
])
def test_title_rules(title, expected, config):
    assert title_matches(title, config) is expected


@pytest.mark.parametrize("location,expected", [
    ("Austin, TX", True),
    ("McLean, Virginia", True),
    ("Indianapolis, IN", True),      # "india" is a substring of "Indianapolis"
    ("Remote in USA", True),
    ("SF", True),
    ("Middletown, RI ; Manassas, VA", True),
    ("Montreal, QC, Canada", False),
    ("Remote in Canada", False),
    ("London", False),
    ("Bangalore, India", False),
    ("Toronto, ON, Canada; Remote in USA", True),
])
def test_location_rules(location, expected, config):
    assert location_matches(location, config) is expected


def test_remote_flag_still_respects_the_deny_list(config):
    assert location_matches("Remote (Canada)", config, remote=True) is False
    assert location_matches("Remote", config, remote=True) is True


@pytest.mark.parametrize("text,expected", [
    ("Winter 2027, Spring 2027", {"winter 2027", "spring 2027"}),
    ("Electrical Engineer Internship (Fall Term 2026)", {"fall 2026"}),
    ("Software Engineering Intern (Summer '27)", {"summer 2027"}),
    ("Autumn 2027", {"fall 2027"}),
    ("Software Engineering Intern", set()),
])
def test_extract_terms(text, expected):
    assert extract_terms(text) == expected


def _p(**kw):
    base = dict(source="simplify-off", company="Acme", title="Cloud Intern",
                location="Austin, TX", url="https://x/1")
    base.update(kw)
    return Posting(**base)


def test_off_season_requires_a_wanted_term(config):
    assert keep(_p(terms="Spring 2027"), config) is True
    assert keep(_p(terms="Fall 2026, Winter 2026"), config) is False
    assert keep(_p(terms="Fall 2026, Spring 2027"), config) is True


def test_s27_has_no_terms_column_so_terms_are_not_required(config):
    assert keep(_p(source="simplify-s27", terms=""), config) is True


def test_board_without_a_named_season_passes_by_default(config):
    """17% of real board internship titles name a season. Requiring one would
    discard the rest, so an unnamed term is not treated as the wrong cycle."""
    assert keep(_p(source="greenhouse", title="Cloud Infrastructure Intern"), config) is True


def test_board_with_the_wrong_named_season_is_rejected(config):
    posting = _p(source="greenhouse", title="Cloud Infrastructure Intern (Fall Term 2026)")
    assert keep(posting, config) is False


def test_board_strict_mode_requires_the_season(config):
    strict = config.__class__(**{**config.__dict__, "board_require_explicit_terms": True})
    assert keep(_p(source="greenhouse", title="Cloud Infrastructure Intern"), strict) is False
    assert keep(_p(source="greenhouse", title="Cloud Infra Intern Summer 2027"), strict) is True
