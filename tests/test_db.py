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
from jobscout import migrations, company_icons, overview_jobs
from jobscout.models import Posting

DSN = os.environ.get("JOBSCOUT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")


@pytest.fixture
def conn():
    connection = database.connect(DSN)
    with connection.cursor() as cur:
        cur.execute("drop table if exists posting_edits")
        cur.execute("drop table if exists posting_overrides")
        cur.execute("drop table if exists overview_jobs")
        cur.execute("drop table if exists posting_taglines")
        cur.execute("drop table if exists company_assets")
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
        cur.execute("drop table if exists hidden_jobs")
        cur.execute("drop table if exists profile_meta")
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
            {"version": 11, "name": "company_icons"},
            {"version": 12, "name": "queued_ai_overviews"},
            {"version": 13, "name": "posting_taglines"},
            {"version": 14, "name": "board_positions"},
            {"version": 15, "name": "profile_revision"},
            {"version": 16, "name": "document_default_and_order"},
            {"version": 17, "name": "hidden_jobs"},
            {"version": 18, "name": "posting_edits"},
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


def test_company_icon_cache_is_joined_to_real_companies(conn):
    database.upsert_open(conn, [_p()])
    with conn.cursor() as cur:
        cur.execute(
            """insert into company_assets
               (company_key, company_name, domain, logo_data, media_type, source)
               values (%s, %s, %s, %s, %s, %s)""",
            ("acme", "Acme", "acme.com", b"icon", "image/png", "verified_site"),
        )
    conn.commit()
    assert database.company_icon_keys(conn) == ["acme"]
    assert database.company_icon(conn, "Acme, Inc.")["logo_data"] == b"icon"
    assert database.company_icon(conn, "Other") is None


def test_icon_refresh_persists_only_verified_site(conn, monkeypatch, tmp_path):
    database.upsert_open(conn, [_p(
        company="New Brand", url="https://boards.greenhouse.io/newbrand/jobs/123",
    )])
    domains = tmp_path / "domains.json"
    domains.write_text("{}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    monkeypatch.setattr(company_icons, "DOMAIN_PATH", domains)
    monkeypatch.setattr(company_icons, "STATIC_MANIFEST", manifest)
    monkeypatch.setattr(company_icons, "_read", lambda *args, **kwargs: b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x20" * 2)
    monkeypatch.setattr(company_icons, "_site_html", lambda *args: "<title>New Brand</title>")
    monkeypatch.setattr(company_icons, "_favicon", lambda *args: (b"real-icon", "image/png"))
    now = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
    result = company_icons.refresh(conn, client=object(), now=now)
    assert result == {"checked": 1, "added": 1, "unverified": 0}
    assert database.company_icon_keys(conn) == ["new brand"]
    assert database.company_icon(conn, "New Brand")["logo_data"] == b"real-icon"
    assert company_icons.refresh(conn, client=object(), now=now + dt.timedelta(days=1)) == {
        "checked": 0, "added": 0, "unverified": 0,
    }


def test_icon_refresh_skips_historical_company_without_postings(conn, monkeypatch, tmp_path):
    with conn.cursor() as cur:
        cur.execute("insert into companies (key, name) values (%s, %s)", ("orphan", "Orphan"))
    conn.commit()
    domains = tmp_path / "domains.json"
    domains.write_text("{}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    monkeypatch.setattr(company_icons, "DOMAIN_PATH", domains)
    monkeypatch.setattr(company_icons, "STATIC_MANIFEST", manifest)
    monkeypatch.setattr(company_icons, "_read", lambda *args, **kwargs: b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x20" * 2)
    assert company_icons.refresh(conn, client=object()) == {
        "checked": 0, "added": 0, "unverified": 0,
    }


def test_icon_refresh_uses_curated_domain_when_homepage_blocks_robots(conn, monkeypatch, tmp_path):
    database.upsert_open(conn, [_p(
        company="Gartner", url="https://jobs.gartner.com/jobs/123",
    )])
    domains = tmp_path / "domains.json"
    domains.write_text('{"Gartner": "gartner.com"}')
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    monkeypatch.setattr(company_icons, "DOMAIN_PATH", domains)
    monkeypatch.setattr(company_icons, "STATIC_MANIFEST", manifest)
    marker = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x20" * 2
    monkeypatch.setattr(company_icons, "_read", lambda *args, **kwargs: marker)
    monkeypatch.setattr(company_icons, "_site_html", lambda *args: "")
    monkeypatch.setattr(company_icons, "_favicon", lambda *args: (b"verified-icon", "image/png"))
    assert company_icons.refresh(conn, client=object())["added"] == 1
    assert database.company_icon(conn, "Gartner")["logo_data"] == b"verified-icon"


def test_overview_queue_is_persistent_and_idempotent(conn):
    database.upsert_open(conn, [_p()])
    key = _p().dedupe_key
    assert database.enqueue_overviews(conn, [key, key]) == 1
    assert database.overview_job(conn, key)["status"] == "queued"
    assert database.claim_overview(conn, priority_only=True) is None
    assert database.claim_overview(conn) == key
    assert database.overview_job(conn, key)["attempts"] == 1
    database.finish_overview(conn, key, [{"kind": "skills", "terms": ["Python"]}], "ai", "hash")
    assert database.overview_job(conn, key)["items"][0]["terms"] == ["Python"]
    assert database.enqueue_overviews(conn, [key]) == 0
    assert database.overview_job(conn, key)["status"] == "ready"


def test_priority_overview_retry_and_failure(conn):
    database.upsert_open(conn, [_p()])
    key = _p().dedupe_key
    assert database.enqueue_overviews(conn, [key], priority=True) == 1
    assert database.claim_overview(conn, priority_only=True) == key
    database.fail_overview(conn, key, "model unavailable")
    assert database.overview_job(conn, key)["status"] == "queued"
    assert database.claim_overview(conn, priority_only=True) is None  # backoff
    database.fail_overview(conn, key, "no posting", terminal=True)
    assert database.overview_job(conn, key)["status"] == "failed"
    assert database.enqueue_overviews(conn, [key], priority=True) == 1
    assert database.claim_overview(conn, priority_only=True) == key


def test_overview_worker_reads_cached_posting_and_persists_result(conn):
    database.upsert_open(conn, [_p()])
    key = _p().dedupe_key
    with conn.cursor() as cur:
        cur.execute("select id from postings where dedupe_key = %s", (key,))
        posting_id = cur.fetchone()["id"]
    description = "Responsibilities\nBuild reliable Python deployment tooling for internal teams."
    database.save_description(conn, posting_id, html=None, text=description)
    database.enqueue_overviews(conn, [key], priority=True)

    class FakeService:
        def overview(self, sections):
            assert "Python" in str(sections)
            return [{"kind": "skills", "terms": ["Python"], "text": "Python"}]

        def is_fallback(self, sections):
            return False

        def fingerprint(self, sections):
            return "source-v1"

    assert overview_jobs.process(DSN, FakeService(), limit=1, priority_only=True) == {
        "ready": 1, "retry": 0, "failed": 0,
    }
    row = database.overview_job(conn, key)
    assert row["status"] == "ready"
    assert row["source_hash"] == "source-v1"
    database.save_description(conn, posting_id, html=None, text=description + " Now with Go.")
    assert database.overview_job(conn, key)["status"] == "queued"


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


def _visible(conn):
    return sorted(row["dedupe_key"] for row in database.dashboard_postings(conn, include_closed=True))


def test_hidden_job_stays_hidden_when_rescraped(conn):
    board = _p(source="greenhouse", board="acme", provider_id="greenhouse:1",
               url="https://boards.greenhouse.io/acme/jobs/1")
    other = _p(title="Data Intern", url="https://acme.example/data")
    database.upsert_open(conn, [board, other])
    database.save_application_state(conn, board.dedupe_key, "applied", "kept")
    database.add_interview(conn, dedupe_key=board.dedupe_key,
                           starts_at=dt.datetime.now(dt.timezone.utc), ends_at=None,
                           location="", notes="")

    assert [entry["dedupe_key"] for entry in database.hide_jobs(conn, [board.dedupe_key, "missing"])] == [board.dedupe_key]
    assert _visible(conn) == [other.dedupe_key]

    # same id, other source, same title
    database.upsert_open(conn, [
        board,
        _p(source="simplify-s27", title="Cloud Engineering Intern (Summer 2027)",
           url="https://boards.greenhouse.io/acme/jobs/1?src=simplify"),
        _p(source="lever", company="Acme, Inc.", title="Cloud Intern!",
           url="https://jobs.lever.co/acme/00000000-0000-0000-0000-000000000002"),
    ])
    assert _visible(conn) == [other.dedupe_key]
    assert [row["dedupe_key"] for row in database.pending(conn)] == [other.dedupe_key]
    support = database.tracker_support(conn)
    assert support["reminders"] == support["interviews"] == support["history"] == []
    with conn.cursor() as cur:
        cur.execute("select status, notes from application_states where dedupe_key = %s", (board.dedupe_key,))
        assert cur.fetchone() == {"status": "interviewing", "notes": "kept"}
        cur.execute("select count(*) as n from postings where hidden_by is not null")
        assert cur.fetchone()["n"] == 3


def test_unhiding_restores_only_that_job(conn):
    first = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    second = _p(title="Data Intern", url="https://boards.greenhouse.io/acme/jobs/2")
    database.upsert_open(conn, [first, second])
    [entry] = database.hide_jobs(conn, [first.dedupe_key])
    database.hide_jobs(conn, [second.dedupe_key])
    assert _visible(conn) == []

    assert database.unhide_jobs(conn, [entry["id"]]) == 1
    assert _visible(conn) == [first.dedupe_key]
    database.upsert_open(conn, [first, second])
    assert _visible(conn) == [first.dedupe_key]


def test_hiding_a_workday_copy_hides_its_requisition_twins(conn):
    tenant = "https://acme.wd5.myworkdayjobs.com"
    first = _p(title="Cloud Intern", url=f"{tenant}/en-US/External/job/Austin-TX/Cloud-Intern_R12345")
    second = _p(source="simplify-s27", title="Cloud Infrastructure Intern",
                url=f"{tenant}/Careers/job/Remote/Cloud-Infrastructure-Intern_R12345")
    other = _p(title="Data Intern", url=f"{tenant}/External/job/Austin-TX/Data-Intern_R99999")
    database.upsert_open(conn, [first, second, other])
    assert first.dedupe_key != second.dedupe_key
    assert len(_visible(conn)) == 2  # the twins collapse

    shown = next(key for key in _visible(conn) if key != other.dedupe_key)
    database.hide_jobs(conn, [shown])
    assert _visible(conn) == [other.dedupe_key]

    third = _p(source="greenhouse", title="Platform Intern",
               url=f"{tenant}/fr-CA/External/job/Remote/Platform-Intern_R12345")
    database.upsert_open(conn, [third])
    assert _visible(conn) == [other.dedupe_key]


def test_a_repeat_hide_gives_the_second_tab_nothing_to_undo(conn):
    posting = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [posting])
    first_tab = database.hide_jobs(conn, [posting.dedupe_key])
    second_tab = database.hide_jobs(conn, [posting.dedupe_key])
    assert len(first_tab) == 1 and second_tab == []

    assert database.unhide_jobs(conn, [entry["id"] for entry in second_tab]) == 0
    assert _visible(conn) == []
    assert database.unhide_jobs(conn, [first_tab[0]["id"]]) == 1
    assert _visible(conn) == [posting.dedupe_key]


def test_queue_order_skips_hidden_jobs(conn):
    kept = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    gone = _p(title="Data Intern", url="https://boards.greenhouse.io/acme/jobs/2")
    database.upsert_open(conn, [kept, gone])
    for posting in (kept, gone):
        database.save_application_state(conn, posting.dedupe_key, "queued", "")
    database.hide_jobs(conn, [gone.dedupe_key])
    assert database.hidden_keys(conn, [kept.dedupe_key, gone.dedupe_key]) == {gone.dedupe_key}
    assert database.save_queue_order(conn, [kept.dedupe_key]) == [kept.dedupe_key]


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


def test_field_only_profile_save_keeps_resume_references(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    profile = database.replace_profile_data(conn, {
        "fields": [{"key": "email", "group": "Contact", "label": "Email", "value": "old@example.invalid"}],
        "answer_templates": [{"name": "Why", "body": "Because"}],
        "documents": [{"name": "Platform resume", "date": "2026-09-01", "url": ""}],
    })
    document_id = profile["documents"][0]["id"]
    template_id = profile["answer_templates"][0]["id"]
    database.mark_application_applied(conn, posting.dedupe_key, document_id)

    profile["fields"][0]["value"] = "new@example.invalid"
    saved = database.replace_profile_data(conn, profile)

    assert saved["documents"][0]["id"] == document_id
    assert saved["answer_templates"][0]["id"] == template_id
    with conn.cursor() as cur:
        cur.execute("select resume_document_id from application_states where dedupe_key = %s", (posting.dedupe_key,))
        assert cur.fetchone()["resume_document_id"] == document_id
        cur.execute("select resume_document_id from application_snapshots where dedupe_key = %s", (posting.dedupe_key,))
        assert cur.fetchone()["resume_document_id"] == document_id


def test_profile_fields_come_back_in_hand_order(conn):
    saved = database.replace_profile_data(conn, {"fields": [
        {"key": "email", "group": "Contact", "label": "Email", "value": "", "pinned": False, "sort_order": 0},
        {"key": "name", "group": "Identity", "label": "Name", "value": "", "pinned": True, "sort_order": 10},
    ]})

    assert [row["key"] for row in saved["fields"]] == ["email", "name"]


def test_profile_save_updates_inserts_and_deletes_by_id(conn):
    profile = database.replace_profile_data(conn, {
        "answer_templates": [{"name": "A", "body": "1"}, {"name": "B", "body": "2"}],
        "documents": [{"name": "One"}, {"name": "Two"}],
    })
    first, second = profile["answer_templates"]
    # swapped names trip the unique index
    first["name"], second["name"] = "B", "A"
    profile["documents"] = [profile["documents"][1], {"id": 999999, "name": "Three"}]

    saved = database.replace_profile_data(conn, profile)

    assert {row["id"]: row["name"] for row in saved["answer_templates"]} == {first["id"]: "B", second["id"]: "A"}
    assert sorted(row["name"] for row in saved["documents"]) == ["Three", "Two"]
    assert profile["documents"][0]["id"] in {row["id"] for row in saved["documents"]}
    assert 999999 not in {row["id"] for row in saved["documents"]}


def test_profile_save_checks_and_bumps_the_revision(conn):
    start = database.profile_data(conn)["revision"]
    saved = database.replace_profile_data(conn, {"fields": [{"key": "email", "group": "Contact", "label": "Email", "value": "a"}]}, revision=start)
    assert saved["revision"] == start + 1

    with pytest.raises(database.ProfileConflict):
        database.replace_profile_data(conn, {"fields": []}, revision=start)
    assert database.profile_data(conn)["fields"][0]["value"] == "a"
    assert database.profile_data(conn)["revision"] == start + 1


def _override(conn, dedupe_key, overrides):
    return database.save_quick_fill_context(conn, dedupe_key=dedupe_key, company="Acme", answer_overrides=overrides, company_account={})


def test_template_rename_carries_job_overrides(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    profile = database.replace_profile_data(conn, {"answer_templates": [{"name": "Why", "body": "x"}, {"name": "Fit", "body": "y"}]})
    _override(conn, posting.dedupe_key, {"Why": "acme why", "Fit": "acme fit"})
    why, fit = sorted(profile["answer_templates"], key=lambda row: row["name"], reverse=True)
    # a swap needs both parked first
    why["name"], fit["name"] = "Fit", "Why"

    database.replace_profile_data(conn, profile)

    overrides = database.profile_data(conn, dedupe_key=posting.dedupe_key)["answer_overrides"]
    assert overrides == {"Fit": "acme why", "Why": "acme fit"}


def test_deleted_template_overrides_return_with_the_name(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    profile = database.replace_profile_data(conn, {"answer_templates": [{"name": "Why", "body": "x"}]})
    _override(conn, posting.dedupe_key, {"Why": "acme why"})
    removed = profile["answer_templates"].pop()

    database.replace_profile_data(conn, profile)
    assert database.profile_data(conn, dedupe_key=posting.dedupe_key)["answer_templates"] == []
    profile["answer_templates"].append({"name": removed["name"], "body": removed["body"]})
    restored = database.replace_profile_data(conn, profile)

    assert [row["name"] for row in restored["answer_templates"]] == ["Why"]
    assert database.profile_data(conn, dedupe_key=posting.dedupe_key)["answer_overrides"] == {"Why": "acme why"}


def test_one_default_document_survives_reorder_and_clears_on_delete(conn):
    posting = _p()
    database.upsert_open(conn, [posting])
    profile = database.replace_profile_data(conn, {"documents": [{"name": "One"}, {"name": "Two", "default": True}]})
    assert [(row["name"], row["default"]) for row in profile["documents"]] == [("One", False), ("Two", True)]
    database.mark_application_applied(conn, posting.dedupe_key, profile["documents"][0]["id"])

    one, two = profile["documents"]
    one["default"], two["default"] = True, False
    one["sort_order"], two["sort_order"] = 10, 0
    moved = database.replace_profile_data(conn, profile)
    assert [(row["name"], row["default"], row["application_count"]) for row in moved["documents"]] == [("Two", False, 0), ("One", True, 1)]

    moved["documents"] = [row for row in moved["documents"] if row["name"] == "Two"]
    left = database.replace_profile_data(conn, moved)
    assert [(row["name"], row["default"]) for row in left["documents"]] == [("Two", False)]
    with pytest.raises(ValueError, match="one document"):
        database.normalize_profile_data({"documents": [{"name": "A", "default": True}, {"name": "B", "default": True}]})


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


def test_queued_description_texts_only_returns_cached_queued_roles(conn):
    queued = _p(url="https://acme.example/queued")
    saved = _p(title="Saved Intern", url="https://acme.example/saved")
    database.upsert_open(conn, [queued, saved])
    database.save_application_state(conn, queued.dedupe_key, "queued", "")
    database.save_application_state(conn, saved.dedupe_key, "saved", "")
    for posting in (queued, saved):
        target = database.description_target(conn, posting.dedupe_key)
        database.save_description(
            conn, target["id"], html=None,
            text="Pay range: $28-$30 per hour.",
        )
    assert database.queued_description_texts(conn) == {
        queued.dedupe_key: "Pay range: $28-$30 per hour.",
    }


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


def test_board_order_and_status_changes(conn):
    first = _p(url="https://acme.example/one")
    second = _p(title="Systems Intern", url="https://acme.example/two")
    third = _p(title="Data Intern", url="https://acme.example/three")
    database.upsert_open(conn, [first, second, third])
    for posting in (first, second, third):
        database.save_application_state(conn, posting.dedupe_key, "interviewing", "")
    assert database.save_board_order(conn, "interviewing", [third.dedupe_key, first.dedupe_key]) == [
        third.dedupe_key, first.dedupe_key,
    ]
    rows = {row["dedupe_key"]: row for row in database.dashboard_postings(conn)}
    assert rows[third.dedupe_key]["board_position"] == 0
    assert rows[first.dedupe_key]["board_position"] == 1
    assert rows[second.dedupe_key]["board_position"] is None

    database.save_application_state(conn, first.dedupe_key, "interviewing", "notes only")
    database.save_application_state(conn, third.dedupe_key, "offer", "")
    database.mark_application_applied(conn, second.dedupe_key, None)
    rows = {row["dedupe_key"]: row for row in database.dashboard_postings(conn)}
    assert rows[first.dedupe_key]["board_position"] == 1
    assert rows[third.dedupe_key]["board_position"] is None
    assert rows[second.dedupe_key]["board_position"] is None


def test_board_order_rejects_other_columns_and_duplicates(conn):
    posting = _p()
    other = _p(title="Systems Intern", url="https://acme.example/two")
    database.upsert_open(conn, [posting, other])
    database.save_application_state(conn, posting.dedupe_key, "offer", "")
    database.save_application_state(conn, other.dedupe_key, "applied", "")
    with pytest.raises(ValueError, match="only contain jobs in that column"):
        database.save_board_order(conn, "offer", [posting.dedupe_key, other.dedupe_key])
    with pytest.raises(ValueError, match="duplicates"):
        database.save_board_order(conn, "offer", [posting.dedupe_key, posting.dedupe_key])
    with pytest.raises(ValueError, match="not a board column"):
        database.save_board_order(conn, "queued", [])


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


def test_posting_edits_survive_a_scout_refresh_and_undo_in_order(conn):
    from jobscout.descriptions import parse_description
    posting = _p(source="greenhouse", url="https://boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [posting])
    target = database.description_target(conn, posting.dedupe_key)
    database.save_description(conn, target["id"], html="<p>Original text from the provider.</p>",
                              text="Original text from the provider.", sections=())
    database.add_interview(conn, dedupe_key=posting.dedupe_key, starts_at=dt.datetime.now(dt.timezone.utc),
                           ends_at=None, location="", notes="")
    corrected = parse_description("Corrected text that the parser had split badly.\nRequirements\nPython")

    first = database.edit_posting(conn, posting.dedupe_key, {"company": "Acme Robotics", "title": "Robotics Intern"}, corrected)
    second = database.edit_posting(conn, posting.dedupe_key, {"url": "https://acme.example/robotics"})
    assert database.edit_posting(conn, posting.dedupe_key, {"url": "https://acme.example/robotics"}) == {"id": None}
    assert database.edit_posting(conn, "missing", {"title": "x"}) is None
    database.upsert_open(conn, [posting])  # a refresh keeps the edit
    [job] = database.dashboard_postings(conn)
    assert (job["company"], job["title"], job["url"]) == ("Acme Robotics", "Robotics Intern", "https://acme.example/robotics")
    assert database.tracker_support(conn)["interviews"][0]["company"] == "Acme Robotics"
    assert database.cached_description(conn, posting.dedupe_key)["description_html"] is None
    with conn.cursor() as cur:
        cur.execute("select name from companies where key = 'acme robotics'")
        assert cur.fetchone() == {"name": "Acme Robotics"}

    with pytest.raises(database.EditConflict):
        database.undo_posting_edit(conn, first["id"])
    database.undo_posting_edit(conn, second["id"])
    assert database.dashboard_postings(conn)[0]["url"] == posting.url
    assert database.undo_posting_edit(conn, first["id"]) == {"id": first["id"], "dedupe_key": posting.dedupe_key}
    [job] = database.dashboard_postings(conn)
    assert (job["company"], job["title"]) == ("Acme", "Cloud Intern")
    detail = database.cached_description(conn, posting.dedupe_key)
    assert (detail["description_html"], detail["description_text"]) == ("<p>Original text from the provider.</p>", "Original text from the provider.")
    with conn.cursor() as cur:
        cur.execute("select count(*) as n from posting_overrides")
        assert cur.fetchone()["n"] == 0
    with pytest.raises(database.EditConflict):
        database.undo_posting_edit(conn, first["id"])
    assert database.undo_posting_edit(conn, 999) is None


def test_first_posting_edits_wait_for_each_other(conn):
    import threading
    posting = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [posting])
    blocker = database.connect(DSN)
    with blocker.cursor() as cur:  # another first edit in flight
        cur.execute("select id from postings where dedupe_key = %s for update", (posting.dedupe_key,))
    edits = {}

    def edit():
        with database.connect(DSN) as other:
            edits["first"] = database.edit_posting(other, posting.dedupe_key, {"title": "First"})

    thread = threading.Thread(target=edit)
    thread.start()
    thread.join(0.5)
    assert thread.is_alive()
    blocker.commit()
    blocker.close()
    thread.join(5)
    second = database.edit_posting(conn, posting.dedupe_key, {"company": "Acme Robotics"})
    database.undo_posting_edit(conn, second["id"])
    [job] = database.dashboard_postings(conn)
    assert (job["company"], job["title"]) == ("Acme", "First")
    with pytest.raises(database.EditConflict):
        database.undo_posting_edit(conn, second["id"])
    database.undo_posting_edit(conn, edits["first"]["id"])
    assert database.dashboard_postings(conn)[0]["title"] == "Cloud Intern"


def test_a_corrected_description_survives_a_refetch_and_a_recapture(conn, monkeypatch):
    from jobscout.capture import CapturedPosting
    from jobscout.dashboard import PostgresStore
    from jobscout.descriptions import parse_description
    monkeypatch.setenv("DATABASE_URL", DSN)
    store = PostgresStore(DSN)
    posting = _p(source="manual", url="https://acme.example/jobs/1")
    provider = parse_description("Provider text that the parser split in the wrong places.")
    monkeypatch.setattr(store._capture, "capture", lambda payload: CapturedPosting(posting, provider))
    store.capture({"url": posting.url})
    corrected = parse_description("Corrected text, written by hand.\nRequirements\nPython\nLinux")
    edit = database.edit_posting(conn, posting.dedupe_key, {}, corrected)
    database.save_application_state(conn, posting.dedupe_key, "queued", "")

    store.capture({"url": posting.url})  # a repeat capture
    assert store.description(posting.dedupe_key)["description_text"] == corrected.text
    fresh = parse_description("A fresh provider fetch with different words in it.")
    monkeypatch.setattr(store._descriptions, "fetch", lambda target: fresh)
    detail = store.description(posting.dedupe_key, force=True)
    assert detail["description_text"] == corrected.text and detail["description_html"] is None
    assert [section["key"] for section in detail["sections"]] == [section["key"] for section in corrected.sections]
    assert database.queued_description_texts(conn) == {posting.dedupe_key: corrected.text}

    database.undo_posting_edit(conn, edit["id"])
    assert store.description(posting.dedupe_key)["description_text"] == fresh.text


def test_a_description_edit_and_its_undo_regenerate_the_tagline(conn):
    from jobscout.descriptions import parse_description
    posting = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [posting])
    target = database.description_target(conn, posting.dedupe_key)
    database.save_description(conn, target["id"], html=None, text="Original provider text.", sections=())
    database.save_tagline(conn, posting.dedupe_key, "Builds things", "hash")
    pending = lambda: database.pending_taglines(conn, [posting.dedupe_key], "hash", 10)
    assert pending() == []

    edit = database.edit_posting(conn, posting.dedupe_key, {}, parse_description("Corrected text written by hand for this role."))
    assert pending() == [posting.dedupe_key]
    database.save_tagline(conn, posting.dedupe_key, "Corrected", "hash")
    database.edit_posting(conn, posting.dedupe_key, {"title": "Robotics Intern"})
    assert pending() == []  # a title edit keeps it
    database.undo_posting_edit(conn, edit["id"] + 1)
    database.undo_posting_edit(conn, edit["id"])
    assert pending() == [posting.dedupe_key]


def test_a_hand_written_description_queues_a_tagline_before_any_fetch(conn):
    from jobscout.descriptions import parse_description
    posting = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [posting])
    assert database.pending_taglines(conn, [posting.dedupe_key], "hash", 10) == []
    database.edit_posting(conn, posting.dedupe_key, {}, parse_description("Corrected text written by hand for this role."))
    assert database.pending_taglines(conn, [posting.dedupe_key], "hash", 10) == [posting.dedupe_key]
    assert database.cached_description(conn, posting.dedupe_key)["description_text"].startswith("Corrected text")


def test_a_corrected_description_owns_the_deadline(conn):
    from jobscout.descriptions import parse_description
    posting = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [posting])
    target = database.description_target(conn, posting.dedupe_key)
    database.save_description(conn, target["id"], html=None, text="Apply by Dec 1.", sections=(),
                              deadline=dt.date(2026, 12, 1), deadline_source="provider")
    edit = database.edit_posting(conn, posting.dedupe_key, {}, parse_description("Corrected text with no closing date at all."))
    assert database.dashboard_postings(conn)[0]["deadline"] is None
    detail = database.cached_description(conn, posting.dedupe_key)
    assert (detail["deadline"], detail["deadline_source"]) == (None, None)
    database.undo_posting_edit(conn, edit["id"])
    assert database.dashboard_postings(conn)[0]["deadline"] == dt.date(2026, 12, 1)


def test_hiding_an_edited_job_also_hides_its_edited_title(conn):
    posting = _p(url="https://boards.greenhouse.io/acme/jobs/1")
    database.upsert_open(conn, [posting])
    database.edit_posting(conn, posting.dedupe_key, {"title": "Robotics Intern"})
    database.hide_jobs(conn, [posting.dedupe_key])
    database.upsert_open(conn, [_p(source="lever", title="Robotics Intern",
                                   url="https://jobs.lever.co/acme/00000000-0000-0000-0000-000000000009")])
    assert _visible(conn) == []


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


def test_quickfill_merge_adds_only_missing_rows(conn):
    from jobscout.quickfill_import import merge_missing, parse_snippets

    database.replace_profile_data(conn, {
        "fields": [{"key": "email", "group": "Contact", "label": "Email", "value": "keep@example.invalid", "sort_order": 10}],
        "answer_templates": [{"name": "Summary", "body": "keep me", "sort_order": 10}],
    })
    incoming, _ = parse_snippets("Email :: other\nStart date (Spring) :: 01/04/2027\nSummary :: other\nArea of interest :: DevOps")

    assert merge_missing(conn, incoming) == (1, 1)
    assert merge_missing(conn, incoming) == (0, 0)
    profile = database.profile_data(conn)
    assert {row["label"]: row["value"] for row in profile["fields"]} == {
        "Email": "keep@example.invalid", "Start date (Spring)": "01/04/2027",
    }
    assert {row["name"]: row["body"] for row in profile["answer_templates"]} == {
        "Summary": "keep me", "Area of interest": "DevOps",
    }


def _fetched(conn, **kw):
    """An open posting whose description has been cached, as the overview worker leaves it."""
    posting = _p(**{k: v for k, v in kw.items() if k in {"title", "url"}})
    database.upsert_open(conn, [posting])
    with conn.cursor() as cur:
        cur.execute("select id from postings where dedupe_key = %s", (posting.dedupe_key,))
        posting_id = cur.fetchone()["id"]
    database.save_description(
        conn, posting_id, html=None, text=kw.get("text"), error=kw.get("error"),
        sections=({"key": "about", "text": kw["text"]},) if kw.get("text") else (),
    )
    return posting.dedupe_key


def test_tagline_queue_orders_retries_and_regenerates_on_prompt_change(conn):
    keys = ["a", "b", "c"]
    for key in keys:
        with conn.cursor() as cur:
            cur.execute(
                "insert into postings (source, source_id, company, title, location, url, dedupe_key, fallback_key, description_fetched_at)"
                " values ('t', %s, 'Acme', %s, 'Austin, TX', %s, %s, %s, now())",
                (key, f"Intern {key}", f"https://acme.example/{key}", key, f"fb-{key}"),
            )
    conn.commit()
    assert database.pending_taglines(conn, keys, "p1", 10) == keys
    assert database.pending_taglines(conn, keys, "p1", 2) == ["a", "b"]

    database.save_tagline(conn, "a", "🟢 High · x · y", "p1")
    database.fail_tagline(conn, "b", "boom", "p1")
    # b backs off for ten minutes, a is done, c is still waiting.
    assert database.pending_taglines(conn, keys, "p1", 10) == ["c"]
    assert database.posting_taglines(conn) == {"a": "🟢 High · x · y"}

    # A new prompt regenerates the finished tagline and gives b a fresh start.
    assert database.pending_taglines(conn, keys, "p2", 10) == keys


def test_tagline_failures_stop_after_three_attempts(conn):
    for _ in range(3):
        database.fail_tagline(conn, "a", "boom", "p1")
        with conn.cursor() as cur:
            cur.execute("update posting_taglines set available_at = now() - interval '1 minute'")
        conn.commit()
    assert database.pending_taglines(conn, ["a"], "p1", 10) == []


def test_failed_regeneration_drops_the_stale_tagline_so_backoff_applies(conn):
    database.save_tagline(conn, "a", "🟢 High · old · line", "p1")
    database.fail_tagline(conn, "a", "boom", "p2")
    assert database.posting_taglines(conn) == {}
    assert database.pending_taglines(conn, ["a"], "p2", 10) == []


def test_tagline_queue_waits_for_a_fetched_description(conn):
    with conn.cursor() as cur:
        cur.execute(
            "insert into postings (source, source_id, company, title, location, url, dedupe_key, fallback_key)"
            " values ('t', 'x', 'Acme', 'Intern', 'Austin, TX', 'https://acme.example/x', 'x', 'fb-x')"
        )
    conn.commit()
    assert database.pending_taglines(conn, ["x"], "p1", 10) == []
    with conn.cursor() as cur:
        cur.execute("update postings set description_fetched_at = now() where dedupe_key = 'x'")
    conn.commit()
    assert database.pending_taglines(conn, ["x"], "p1", 10) == ["x"]


class _FakeTagger:
    hash = "h1"

    def __init__(self):
        self.calls = []

    def generate(self, job, text):
        self.calls.append(job["dedupe_key"])
        return "🟢 High · platform work · Summer 2027 · Austin, onsite"


def _tag(dsn, tagger, **kw):
    from jobscout import tagline

    return tagline.process(dsn, tagger, force=True, pause=0, sleep=lambda _s: None, **kw)


def test_tagline_batch_marks_empty_descriptions_unclear_and_skips_unfetched(conn):
    good = _fetched(conn, title="Platform Intern", url="https://acme.example/good", text="Run platform tooling. " * 20)
    empty = _fetched(conn, title="Cloud Intern", url="https://acme.example/empty", error="This provider did not return a readable description.")
    unfetched = _p(title="DevOps Intern", url="https://acme.example/unfetched")
    database.upsert_open(conn, [unfetched])
    tagger = _FakeTagger()

    counts = _tag(DSN, tagger)

    assert (counts["ready"], counts["unclear"], counts["retry"]) == (1, 1, 0)
    assert tagger.calls == [good]
    taglines = database.posting_taglines(conn)
    assert taglines[empty] == "unclear, read posting" and unfetched.dedupe_key not in taglines
    # Nothing left to retry forever.
    assert _tag(DSN, _FakeTagger())["ready"] == 0


def test_tagline_batch_stops_when_the_node_is_busy_or_the_budget_is_spent(conn):
    _fetched(conn, title="Platform Intern", url="https://acme.example/one", text="Run platform tooling. " * 20)
    _fetched(conn, title="Cloud Intern", url="https://acme.example/two", text="Run cloud tooling. " * 20)
    tagger = _FakeTagger()

    busy = _tag(DSN, tagger, loadavg=lambda: (50.0, 40.0, 30.0))
    assert busy["stopped"] == 2 and busy["ready"] == 0 and tagger.calls == []

    ticks = iter(range(0, 1000, 100))
    spent = _tag(DSN, tagger, max_seconds=150, clock=lambda: next(ticks))
    assert spent["ready"] >= 1 and spent["stopped"] >= 1 and len(tagger.calls) == spent["ready"]


def test_tagline_batch_does_nothing_outside_the_morning_window(conn):
    from jobscout import tagline

    _fetched(conn, title="Platform Intern", url="https://acme.example/one", text="Run platform tooling. " * 20)
    afternoon = dt.datetime(2026, 10, 2, 20, 0, tzinfo=dt.timezone.utc)
    tagger = _FakeTagger()
    assert tagline.process(DSN, tagger, now=afternoon, pause=0)["ready"] == 0 and tagger.calls == []
