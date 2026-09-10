"""Persistence, dedupe and the two close rules.

Needs a Postgres. CI provides one as a service container; locally set
JOBSCOUT_TEST_DSN, e.g.

    docker run -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=jobscout \
        -p 55432:5432 postgres:16-alpine
    JOBSCOUT_TEST_DSN=postgresql://postgres:test@localhost:55432/jobscout pytest
"""
import os

import pytest

from jobscout import db as database
from jobscout.models import Posting

DSN = os.environ.get("JOBSCOUT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")


@pytest.fixture
def conn():
    connection = database.connect(DSN)
    with connection.cursor() as cur:
        cur.execute("drop table if exists postings")
    connection.commit()
    database.ensure_schema(connection)
    yield connection
    connection.close()


def _p(**kw):
    base = dict(source="simplify-s27", company="Acme", title="Cloud Intern",
                location="Austin, TX", url="https://acme.example/1")
    base.update(kw)
    return Posting(**base)


def test_ensure_schema_is_idempotent(conn):
    database.ensure_schema(conn)
    database.ensure_schema(conn)


def test_upsert_counts_only_new_rows(conn):
    assert database.upsert_open(conn, [_p()]) == 1
    assert database.upsert_open(conn, [_p()]) == 0


def test_upsert_refreshes_mutable_fields(conn):
    database.upsert_open(conn, [_p(url="https://old")])
    database.upsert_open(conn, [_p(url="https://new")])
    with conn.cursor() as cur:
        cur.execute("select url from postings")
        assert cur.fetchone()["url"] == "https://new"


def test_pending_returns_unnotified_then_stops_after_marking(conn):
    database.upsert_open(conn, [_p()])
    rows = database.pending(conn)
    assert len(rows) == 1
    database.mark_notified(conn, [r["dedupe_key"] for r in rows])
    assert database.pending(conn) == []


def test_same_job_from_two_sources_notifies_once(conn):
    """The README row and the board row share a provider id, so only one of
    them is ever pending, and marking it silences its twin."""
    url = "https://acme.example/careers?gh_jid=999"
    database.upsert_open(conn, [
        _p(url=url),
        _p(source="greenhouse", board="acme", provider_id="greenhouse:999",
           url="https://job-boards.greenhouse.io/acme/jobs/999"),
    ])
    rows = database.pending(conn)
    assert len(rows) == 1
    database.mark_notified(conn, [r["dedupe_key"] for r in rows])
    assert database.pending(conn) == []
    with conn.cursor() as cur:
        cur.execute("select count(*) as n from postings where notified_at is not null")
        assert cur.fetchone()["n"] == 2


def test_mark_closed_updates_but_never_inserts(conn):
    database.upsert_open(conn, [_p()])
    key = _p().dedupe_key
    assert database.mark_closed(conn, "simplify-s27", [key]) == 1
    assert database.mark_closed(conn, "simplify-s27", ["sha256:never-seen"]) == 0
    with conn.cursor() as cur:
        cur.execute("select count(*) as n from postings")
        assert cur.fetchone()["n"] == 1


def test_closed_rows_are_never_pending(conn):
    database.upsert_open(conn, [_p()])
    database.mark_closed(conn, "simplify-s27", [_p().dedupe_key])
    assert database.pending(conn) == []


def test_board_posting_closes_after_two_consecutive_misses(conn):
    job = _p(source="greenhouse", board="acme", provider_id="greenhouse:1",
             url="https://job-boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [job])
    fetched = {("greenhouse", "acme")}

    database.close_missing_boards(conn, fetched, {("greenhouse", "acme"): set()})
    assert len(database.pending(conn)) == 1, "one miss must not close it"

    database.close_missing_boards(conn, fetched, {("greenhouse", "acme"): set()})
    assert database.pending(conn) == []


def test_reappearing_before_the_second_miss_resets_the_counter(conn):
    job = _p(source="greenhouse", board="acme", provider_id="greenhouse:1",
             url="https://job-boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [job])
    fetched = {("greenhouse", "acme")}
    database.close_missing_boards(conn, fetched, {("greenhouse", "acme"): set()})
    database.upsert_open(conn, [job])          # back on the board
    database.close_missing_boards(conn, fetched, {("greenhouse", "acme"): {job.source_id}})
    database.close_missing_boards(conn, fetched, {("greenhouse", "acme"): set()})
    assert len(database.pending(conn)) == 1


def test_a_board_that_did_not_answer_closes_nothing(conn):
    """The rule that stops a 404 from wiping out a whole board."""
    job = _p(source="greenhouse", board="acme", provider_id="greenhouse:1",
             url="https://job-boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [job])
    for _ in range(3):
        database.close_missing_boards(conn, set(), {})
    assert len(database.pending(conn)) == 1


def test_seed_marks_everything_without_notifying(conn):
    database.upsert_open(conn, [_p(), _p(title="Platform Intern", url="https://acme.example/2")])
    assert database.seed(conn) == 2
    assert database.pending(conn) == []


def test_locked_row_closes_a_job_stored_under_its_provider_id(conn):
    """The regression that matters: while open, a Simplify row carries an apply
    URL and is stored under `greenhouse:N`. Once locked it has no URL at all, so
    the close path has only the hash to go on."""
    open_row = _p(url="https://job-boards.greenhouse.io/acme/jobs/777")
    assert open_row.dedupe_key == "greenhouse:777"
    database.upsert_open(conn, [open_row])

    locked = _p(url="", closed=True)
    assert locked.dedupe_key != open_row.dedupe_key
    assert database.mark_closed(conn, "simplify-s27", [locked.fallback_key]) == 1
    assert database.pending(conn) == []


def test_three_cities_collapse_to_one_row_keeping_every_location(conn):
    """The same job listed in three cities is one posting, but location often
    decides whether it is worth applying, so all three survive the merge."""
    cities = ["Austin, TX", "Seattle, WA", "Boston, MA"]
    database.upsert_open(conn, [_p(location=c) for c in cities])
    rows = database.pending(conn)
    assert len(rows) == 1
    assert set(rows[0]["location"].split("; ")) == set(cities)


def test_location_union_survives_across_runs(conn):
    database.upsert_open(conn, [_p(location="Austin, TX")])
    database.upsert_open(conn, [_p(location="Remote in USA")])
    assert set(database.pending(conn)[0]["location"].split("; ")) == {
        "Austin, TX", "Remote in USA"}


def test_locations_union_across_sources_too(conn):
    """Simplify says Austin, the Greenhouse board says Austin and Remote."""
    url = "https://acme.example/careers?gh_jid=42"
    database.upsert_open(conn, [
        _p(url=url, location="Austin, TX"),
        _p(source="greenhouse", board="acme", provider_id="greenhouse:42",
           url="https://job-boards.greenhouse.io/acme/jobs/42",
           location="Austin, TX; Remote in USA"),
    ])
    rows = database.pending(conn)
    assert len(rows) == 1
    assert set(rows[0]["location"].split("; ")) == {"Austin, TX", "Remote in USA"}


def test_age_is_stored_and_open_postings_can_be_exported(conn):
    database.upsert_open(conn, [
        _p(age_days=3),
        _p(title="Platform Intern", url="https://acme.example/2", age_days=40),
    ])
    exported = database.open_postings(conn, only_unnotified=False)
    assert {r["age_days"] for r in exported} == {3, 40}
    database.seed(conn)
    # Seeding silences notifications but must not hide rows from an export.
    assert database.pending(conn) == []
    assert len(database.open_postings(conn, only_unnotified=False)) == 2
