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
    ("IT Intern - Summer 2027", True),          # case-sensitive \bIT\b
    ("Intern - IT", True),
    ("Information Technology Intern", True),
    ("Make it Work Intern", False),             # the reason \bIT\b is not re.I
    ("Digital Marketing Intern", False),
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
    # Workday writes the country and state as dash-delimited fields.
    ("US-NY-Rochester", True),
    ("NY-Rochester", True),
    ("US-TX-Austin", True),
    ("Winston-Salem, NC", True),          # dash inside the city name
    ("Remote - US", True),
    ("United States", True),
    ("3 Locations", True),                # Workday collapses multi-site jobs
    ("Multiple Locations", True),
    ("CA-ON-Toronto", False),             # dash-delimited but not a US state
    ("Toronto, Ontario", False),
    ("OK COMPUTER", False),               # undelimited capitals are not a state
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


# --- ranking --------------------------------------------------------------

def _row(company, title, age=None, terms=""):
    return {"company": company, "title": title, "age_days": age, "terms": terms,
            "dedupe_key": f"{company}:{title}", "url": "https://x/1"}


def test_rank_collapses_one_job_posted_per_city(config):
    """Booz Allen lists a single co-op once per office. Eleven rows, one job."""
    from jobscout.filters import rank

    cities = ["Annapolis Junction, MD", "Charleston, SC", "Honolulu, HI",
              "Rome, NY", "San Diego, CA", "McLean, VA"]
    rows = [_row("Booz Allen Hamilton",
                 f"University - 2027 Summer Games Systems Engineer Intern - {c}", 5)
            for c in cities]
    assert len(rank(rows, config)) == 1


def test_rank_puts_the_real_role_above_the_vague_one(config):
    from jobscout.filters import rank

    rows = [
        _row("PNC", "Technology Undergraduate Intern", 9),
        _row("Boeing", "Engineering & Technology Innovation, Aerodynamics Intern", 11),
        _row("M&T Bank", "DevOps Engineer Co-op", 1, "Spring 2027"),
        _row("Disney", "Infrastructure Engineering Intern, Spring 2027", 2, "Spring 2027"),
    ]
    order = [r["company"] for r in rank(rows, config)]
    assert order[0] == "M&T Bank"
    assert order[1] == "Disney"
    assert order[-1] == "Boeing"          # off-target discipline sinks


def test_score_penalises_a_discipline_that_shares_the_words(config):
    from jobscout.filters import score

    infra = _row("Amazon", "Data Center Engineering Operations Intern", 3)
    quantum = _row("Amazon", "Quantum Applied Science Internship, Quantum Technologies", 3)
    assert score(infra, config) > score(quantum, config)


def test_a_collapsed_location_still_checks_the_title(config):
    """Workday hid the country in "3 Locations"; the title still names it."""
    from jobscout.models import Posting
    from jobscout.filters import keep

    singapore = Posting(source="workday", company="Micron",
                        title="Technology Development Internship (Singapore)",
                        location="3 Locations", url="https://x/1")
    usa = Posting(source="workday", company="Micron",
                  title="Data Center SSD Firmware Intern",
                  location="3 Locations", url="https://x/2")
    assert keep(singapore, config) is False
    assert keep(usa, config) is True
