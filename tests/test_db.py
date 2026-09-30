"""Persistence, dedupe and the two close rules.

Needs a Postgres. CI provides one as a service container; locally set
JOBSCOUT_TEST_DSN, e.g.

    docker run -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=jobscout \
        -p 55432:5432 postgres:16-alpine
    JOBSCOUT_TEST_DSN=postgresql://postgres:test@localhost:55432/jobscout pytest
"""
import datetime as dt
import os

import pytest

from jobscout import db as database
from jobscout import migrations
from jobscout.models import Posting

DSN = os.environ.get("JOBSCOUT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")


@pytest.fixture
def conn():
    connection = database.connect(DSN)
    with connection.cursor() as cur:
        cur.execute("drop table if exists rule_actions")
        cur.execute("drop table if exists rules")
        cur.execute("drop table if exists interviews")
        cur.execute("drop table if exists reminders")
        cur.execute("drop table if exists eligibility_overrides")
        cur.execute("drop table if exists contacts")
        cur.execute("drop table if exists company_notes")
        cur.execute("drop table if exists companies")
        cur.execute("drop table if exists application_snapshots")
        cur.execute("drop table if exists company_accounts")
        cur.execute("drop table if exists answer_template_overrides")
        cur.execute("drop table if exists quick_fill_copy_state")
        cur.execute("drop table if exists story_bank")
        cur.execute("drop table if exists application_status_history")
        cur.execute("drop table if exists application_states")
        cur.execute("drop table if exists documents")
        cur.execute("drop table if exists answer_templates")
        cur.execute("drop table if exists profile_fields")
        cur.execute("drop table if exists saved_views")
        cur.execute("drop table if exists board_notices")
        cur.execute("drop table if exists postings")
        cur.execute("drop table if exists schema_migrations")
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


def test_migration_is_recorded_and_advisory_lock_is_released(conn):
    with conn.cursor() as cur:
        cur.execute("select version, name from schema_migrations")
        assert cur.fetchall() == [
            {"version": 1, "name": "baseline"},
            {"version": 2, "name": "inbox_descriptions_and_status_history"},
            {"version": 3, "name": "saved_views"},
            {"version": 4, "name": "quick_fill"},
            {"version": 5, "name": "quick_fill_story_bank"},
            {"version": 6, "name": "quick_fill_job_context"},
            {"version": 7, "name": "apply_queue_and_snapshots"},
            {"version": 8, "name": "tracker_companies_and_followups"},
            {"version": 9, "name": "eligibility_overrides"},
            {"version": 10, "name": "inbox_rules"},
        ]
    with database.connect(DSN) as other, other.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(%s) as acquired", (migrations.MIGRATION_LOCK_ID,))
        assert cur.fetchone()["acquired"] is True
        cur.execute("select pg_advisory_unlock(%s)", (migrations.MIGRATION_LOCK_ID,))


def test_runtime_schema_check_is_read_only(conn):
    database.require_schema(conn)


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


def test_dashboard_combines_posting_and_application_state(conn):
    posting = _p(age_days=2)
    database.upsert_open(conn, [posting])
    database.record_scores(conn, [(posting.dedupe_key, 130, {"role_named": 100})])

    assert database.save_application_state(
        conn, posting.dedupe_key, "queued", "Tailor the platform bullets"
    )
    rows = database.dashboard_postings(conn)

    assert len(rows) == 1
    assert rows[0]["score"] == 130
    assert rows[0]["score_detail"] == {"role_named": 100}
    assert rows[0]["status"] == "queued"
    assert rows[0]["notes"] == "Tailor the platform bullets"


def test_application_state_only_accepts_known_postings_and_statuses(conn):
    assert not database.save_application_state(conn, "missing", "saved", "")
    database.upsert_open(conn, [_p()])
    with pytest.raises(ValueError, match="unknown application status"):
        database.save_application_state(conn, _p().dedupe_key, "thinking", "")


def test_application_status_changes_are_timestamped(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    database.save_application_state(conn, posting.dedupe_key, "saved", "")
    database.save_application_state(conn, posting.dedupe_key, "queued", "")
    database.save_application_state(conn, posting.dedupe_key, "queued", "note only")

    with conn.cursor() as cur:
        cur.execute(
            "select from_status, to_status from application_status_history "
            "where dedupe_key = %s order by id",
            (posting.dedupe_key,),
        )
        assert cur.fetchall() == [
            {"from_status": "new", "to_status": "saved"},
            {"from_status": "saved", "to_status": "queued"},
        ]


def test_saved_views_are_server_backed_and_upsert_by_name(conn):
    first = database.save_view(
        conn,
        name="Remote new",
        filters={"status": "new", "remote": True, "ignored": "no"},
        sort="newest",
    )
    second = database.save_view(
        conn,
        name="Remote new",
        filters={"status": "saved", "remote": True},
        sort="score",
    )
    assert first["id"] == second["id"]
    assert database.saved_views(conn)[0]["filters"] == {"status": "saved", "remote": True}
    assert database.delete_saved_view(conn, first["id"])
    assert database.saved_views(conn) == []


def test_dashboard_warns_about_another_recent_company_application(conn):
    first = _p(title="Cloud Intern", url="https://acme.example/one")
    second = _p(title="Platform Intern", url="https://acme.example/two")
    database.upsert_open(conn, [first, second])
    database.save_application_state(conn, first.dedupe_key, "applied", "")

    rows = {row["dedupe_key"]: row for row in database.dashboard_postings(conn)}
    assert rows[second.dedupe_key]["recent_company_application_at"] is not None
    assert rows[first.dedupe_key]["recent_company_application_at"] is None


def test_profile_replacement_round_trips_story_bank(conn):
    profile = database.replace_profile_data(conn, {
        "fields": [{"key": "email", "group": "Contact", "label": "Email", "value": "me@example.invalid", "pinned": True}],
        "answer_templates": [{"name": "Why", "body": "Because {company}"}],
        "documents": [{"name": "Resume", "date": "2026-09-01", "url": "/resume.pdf"}],
        "stories": [{
            "id": "recovery", "title": "Recovered a service", "situation": "It failed",
            "task": "Restore it", "action": "Used evidence", "result": "Recovered",
            "reflection": "Preserve logs", "competencies": ["Incident response"],
        }],
    })

    assert profile["fields"][0]["value"] == "me@example.invalid"
    assert profile["stories"][0]["id"] == "recovery"
    assert profile["stories"][0]["competencies"] == ["Incident response"]


def test_quick_fill_job_context_is_server_backed(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    database.mark_quick_fill_copy(conn, posting.dedupe_key, "field:email")
    profile = database.save_quick_fill_context(
        conn,
        dedupe_key=posting.dedupe_key,
        company="Acme",
        answer_overrides={"Why this role": "Because the work is concrete."},
        company_account={
            "account_exists": True,
            "sign_in_email": "me@example.invalid",
            "password_manager_url": "https://passwords.example.invalid/acme",
        },
    )

    assert profile["copied_fields"] == ["field:email"]
    assert profile["answer_overrides"] == {"Why this role": "Because the work is concrete."}
    assert profile["company_account"]["account_exists"] is True


def test_queue_order_liveness_and_application_snapshot(conn):
    first = _p(url="https://acme.example/one")
    second = _p(title="Systems Intern", url="https://acme.example/two")
    database.upsert_open(conn, [first, second])
    database.save_application_state(conn, first.dedupe_key, "queued", "")
    database.save_application_state(conn, second.dedupe_key, "queued", "")

    database.save_liveness(conn, first.dedupe_key, "live", "Provider returned the posting")
    target = database.liveness_target(conn, first.dedupe_key)
    assert target["liveness_status"] == "live"
    assert target["liveness_evidence"] == "Provider returned the posting"

    assert database.save_queue_order(conn, [second.dedupe_key, first.dedupe_key]) == [
        second.dedupe_key, first.dedupe_key,
    ]
    rows = {row["dedupe_key"]: row for row in database.dashboard_postings(conn)}
    assert rows[second.dedupe_key]["queue_position"] == 0
    assert rows[first.dedupe_key]["queue_position"] == 1

    target = database.description_target(conn, first.dedupe_key)
    database.save_description(
        conn,
        target["id"],
        html="<h2>Requirements</h2><p>Linux</p>",
        text="Requirements\nLinux",
        sections=({"key": "requirements", "text": "Linux"},),
    )
    with conn.cursor() as cur:
        cur.execute(
            "insert into documents (name, document_date, url) values ('Platform resume', '2026-09-01', '') returning id"
        )
        document_id = cur.fetchone()["id"]
    conn.commit()
    snapshot = database.mark_application_applied(conn, first.dedupe_key, document_id)
    assert snapshot["description_text"] == "Requirements\nLinux"
    assert snapshot["description_sections"] == [{"key": "requirements", "text": "Linux"}]
    assert snapshot["resume_name"] == "Platform resume"
    applied = {row["dedupe_key"]: row for row in database.dashboard_postings(conn)}[first.dedupe_key]
    assert applied["status"] == "applied"
    assert applied["applied_at"] is not None
    assert applied["queue_position"] is None

    first_capture = snapshot["captured_at"]
    repeated = database.mark_application_applied(conn, first.dedupe_key, None)
    assert repeated["captured_at"] == first_capture
    assert repeated["resume_name"] == "Platform resume"
    reapplied = {row["dedupe_key"]: row for row in database.dashboard_postings(conn)}[first.dedupe_key]
    assert reapplied["resume_document_id"] == document_id
    assert reapplied["resume_name"] == "Platform resume"


def test_queue_order_rejects_missing_or_duplicate_jobs(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    database.save_application_state(conn, posting.dedupe_key, "queued", "")
    with pytest.raises(ValueError, match="exactly once"):
        database.save_queue_order(conn, [])
    with pytest.raises(ValueError, match="duplicates"):
        database.save_queue_order(conn, [posting.dedupe_key, posting.dedupe_key])


def test_tracker_followups_interviews_and_company_records(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    with conn.cursor() as cur:
        cur.execute("select key, name from companies")
        assert cur.fetchall() == [{"key": "acme", "name": "Acme"}]
    database.save_application_state(conn, posting.dedupe_key, "applied", "")
    support = database.tracker_support(conn)
    assert len(support["reminders"]) == 1
    assert support["reminders"][0]["kind"] == "follow_up"

    starts_at = dt.datetime.now(dt.timezone.utc)
    interview = database.add_interview(
        conn,
        dedupe_key=posting.dedupe_key,
        starts_at=starts_at,
        ends_at=starts_at + dt.timedelta(hours=1),
        location="Video call",
        notes="Bring architecture examples",
    )
    assert interview["location"] == "Video call"
    assert database.tracker_support(conn)["reminders"] == []
    tracked = database.dashboard_postings(conn)[0]
    assert tracked["status"] == "interviewing"
    assert tracked["next_step"].startswith("Interview ")

    contact = database.save_contact(conn, {
        "company": "Acme, Inc.",
        "name": "Ada Example",
        "title": "Platform Engineer",
        "linkedin_url": "https://www.linkedin.com/in/ada-example",
    }, source="linkedin_csv")
    database.save_company_note(conn, "Acme", "Met at the fall career fair.")
    assert contact["company_key"] == "acme"
    assert database.contact_counts(conn) == {"acme": 1}
    records = database.company_records(conn, "Acme Corporation")
    assert records["contacts"][0]["name"] == "Ada Example"
    assert records["note"]["body"] == "Met at the fall career fair."
    with pytest.raises(ValueError, match="company not found"):
        database.save_contact(conn, {"company": "Unknown", "name": "No One"})
    with pytest.raises(ValueError, match="company not found"):
        database.save_company_note(conn, "Unknown", "Not persisted")


def test_eligibility_overrides_are_append_only_evidence_records(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    saved = database.record_eligibility_overrides(conn, posting.dedupe_key, [{
        "key": "sponsorship",
        "evidence": "We will not provide visa sponsorship.",
        "comparison": "Profile says sponsorship is required.",
    }], "Reviewed manually")
    assert len(saved) == 1
    assert saved[0]["blocker_key"] == "sponsorship"
    assert saved[0]["note"] == "Reviewed manually"


def test_archive_rule_is_logged_and_reversibly_applied(conn):
    posting = _p(title="Senior Cloud Intern")
    database.upsert_open(conn, [posting])
    rule = database.save_rule(conn, {
        "name": "Skip senior roles", "kind": "archive_title", "pattern": "Senior",
    })
    archived = database.dashboard_postings(conn)[0]
    assert archived["status"] == "archived"
    assert archived["archived_by_rule"] == "Skip senior roles"
    assert archived["rule_action_id"] is not None

    action = database.undo_rule_action(conn, archived["rule_action_id"])
    assert action["undone_at"] is not None
    restored = database.dashboard_postings(conn)[0]
    assert restored["status"] == "new"
    assert restored["archived_by_rule"] is None

    paused = database.set_rule_enabled(conn, rule["id"], False)
    assert paused["enabled"] is False
    database.set_rule_enabled(conn, rule["id"], True)
    assert database.dashboard_postings(conn)[0]["status"] == "new"
