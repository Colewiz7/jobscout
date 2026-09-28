"""Career Connect alerts, against a real saved-search alert."""
import datetime
import email
import email.policy
import pathlib

import pytest

from jobscout.sources.careerconnect import parse, search_term

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "careerconnect_infrastructure.eml"
RAW = FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def jobs():
    return parse(RAW)


def test_every_job_in_the_alert_is_found(jobs):
    assert len(jobs) == 17
    assert all(j.title and j.company and j.provider_id for j in jobs)


def test_the_identifier_comes_from_the_redirect(jobs):
    """The link wraps /app/jobs/detail/<hex> in base64; the hex is the id."""
    ibm = next(j for j in jobs if j.company == "IBM")
    assert ibm.provider_id == "careerconnect:29b5a5e3825be070a4c4fccd3e032e0c"
    assert all(len(j.provider_id.split(":")[1]) == 32 for j in jobs)


def test_the_same_job_is_not_counted_twice(jobs):
    """A job matches several saved searches and recurs across alerts."""
    ids = [j.provider_id for j in jobs]
    assert len(ids) == len(set(ids))


def test_fields_come_from_their_own_cells(jobs):
    ibm = next(j for j in jobs if j.company == "IBM")
    assert ibm.title == "Quantum Infrastructure Intern 2027 - SRE"
    assert ibm.location == "Yorktown Heights, New York, United States"


def test_level_is_taken_from_career_connect_not_guessed(jobs):
    """It states Co-op/Internship, so a title that never says so still counts."""
    assert all(j.employment_type == "INTERN" for j in jobs)


def test_no_board_so_the_close_rule_cannot_fire(jobs):
    """An alert is what was new that morning, not what is still open.

    Treating it as a board would retire every posting that simply did not
    recur in the next alert.
    """
    assert all(j.board is None for j in jobs)


def test_age_is_the_age_of_the_alert(jobs):
    sent = email.utils.parsedate_to_datetime(
        email.message_from_bytes(RAW, policy=email.policy.default)["Date"]
    )
    expected = (datetime.datetime.now(datetime.timezone.utc) - sent).days
    assert all(j.age_days == max(expected, 0) for j in jobs)


@pytest.mark.parametrize("subject,expected", [
    ('Cole, the latest "infrastructure" jobs are here', "infrastructure"),
    ('Cole, the latest ""infrastructure"" jobs are here', "infrastructure"),
    ('Cole, the latest "systems administration" jobs are here', "systems administration"),
    ("no quotes at all", ""),
])
def test_search_term_from_the_subject(subject, expected):
    assert search_term(subject) == expected


def test_the_alert_survives_the_real_filters(jobs, config):
    """Six of the seventeen are this work; the rest are finance and sales."""
    from jobscout.filters import keep

    kept = [j for j in jobs if keep(j, config)]
    titles = {j.title for j in kept}
    assert "Quantum Infrastructure Intern 2027 - SRE" in titles
    assert "Co-Op – IT – ServiceNow Platform Administration (Spring-Summer 2027)" in titles
    # Principal's is infrastructure the asset class.
    assert not any("Private Infrastructure" in t for t in titles)
    assert not any("Sales" in t for t in titles)
    assert len(kept) == 6
