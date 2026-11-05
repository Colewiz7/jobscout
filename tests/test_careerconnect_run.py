"""The scout run with Career Connect mail in the bucket. Needs a Postgres."""
import argparse
import os
import pathlib

import pytest

from jobscout import __main__ as cli
from jobscout import db as database
from jobscout.sources import careerconnect, r2
from jobscout.sources.careerconnect import FAILED, INBOX, PROCESSED

from test_db import conn  # noqa: F401 - shared schema reset fixture
from test_r2_drain import BROKEN, CONFIRMATION, FIXTURE, FakeBucket

DSN = os.environ.get("JOBSCOUT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def run(monkeypatch):
    def go(bucket):
        monkeypatch.setenv("DATABASE_URL", DSN)
        monkeypatch.setenv("NTFY_URL", "http://ntfy.invalid")
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setattr(cli.simplify, "fetch", lambda fetcher: {})
        monkeypatch.setattr(cli.boards_source, "fetch", lambda *a, **k: ([], set()))
        monkeypatch.setattr(cli.notify, "push", lambda *a, **k: go.pushed.append(a) or True)
        monkeypatch.setattr(r2.Bucket, "from_env", classmethod(lambda cls, client=None, get_timeout=None: go.timeouts.append(get_timeout) or bucket))
        return cli.main(["--config", str(ROOT / "config" / "filters.yaml"), "run"])

    go.pushed = []
    go.timeouts = []
    return go


def _rows(conn):
    with conn.cursor() as cur:
        cur.execute("select count(*) as n from postings where source = 'careerconnect'")
        return cur.fetchone()["n"]


def test_every_job_is_stored_and_misses_are_archived_not_dropped(conn, run):
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE})
    assert run(bucket) == 0
    assert _rows(conn) == 17
    assert PROCESSED + "alert.eml" in bucket.objects
    with conn.cursor() as cur:
        cur.execute("select count(*) as n from application_states where status = 'archived' and notes like 'Held back:%'")
        assert cur.fetchone()["n"] == 11


def test_a_clean_run_exits_zero_and_a_repeat_alert_adds_nothing(conn, run):
    bucket = FakeBucket({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": FIXTURE})
    assert run(bucket) == 0
    assert _rows(conn) == 17


def test_a_failed_list_exits_nonzero(conn, run):
    class NoList(FakeBucket):
        def keys(self, prefix):
            raise r2.R2Error("listing returned 403")

    assert run(NoList({})) == cli.EXIT_MAIL


def test_a_read_error_exits_nonzero_but_good_alerts_still_land(conn, run):
    class Flaky(FakeBucket):
        def get(self, key):
            if "bad" in key:
                raise OSError("read failed")
            return super().get(key)

    bucket = Flaky({INBOX + "bad.eml": b"x", INBOX + "good.eml": FIXTURE})
    assert run(bucket) == cli.EXIT_MAIL
    assert _rows(conn) == 17
    assert INBOX + "bad.eml" in bucket.objects


def test_many_parse_failures_exit_nonzero(conn, run):
    bucket = FakeBucket({INBOX + ("%d.eml" % n): BROKEN for n in range(3)} | {INBOX + "ok.eml": FIXTURE})
    assert run(bucket) == cli.EXIT_MAIL
    assert len([k for k in bucket.objects if k.startswith(FAILED) and not k.endswith(".reason.txt")]) == 3
    assert _rows(conn) == 17


def test_off_mode_leaves_everything_in_place(conn, run, monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_DRAIN_MODE", "off")
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE})
    assert run(bucket) == 0
    assert _rows(conn) == 0 and INBOX + "alert.eml" in bucket.objects


def test_dry_run_writes_nothing(conn, run, monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_DRAIN_MODE", "dry-run")
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE, INBOX + "c.eml": CONFIRMATION})
    assert run(bucket) == 0
    assert _rows(conn) == 0 and bucket.moves == []


def test_held_jobs_are_never_queued_for_a_push(conn, run):
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE})
    run(bucket)
    with conn.cursor() as cur:
        cur.execute("select count(*) as n from postings p join application_states s using (dedupe_key) where s.status = 'archived' and p.notified_at is null")
        assert cur.fetchone()["n"] == 0


def test_a_clean_run_does_push_kept_jobs(conn, run):
    # control: spy sees pushes
    assert run(FakeBucket({INBOX + "alert.eml": FIXTURE})) == 0
    assert run.pushed


def test_a_failing_hold_stores_nothing_sends_nothing_and_exits_nonzero(conn, run, monkeypatch):
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE})

    def boom(cur, held):
        raise RuntimeError("hold failed")

    monkeypatch.setattr(database, "_hold", boom)
    assert run(bucket) == cli.EXIT_MAIL
    assert _rows(conn) == 0 and run.pushed == []
    assert INBOX + "alert.eml" in bucket.objects


def test_a_zero_limit_processes_nothing_and_exits_nonzero(conn, run, monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_DRAIN_LIMIT", "0")
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE})
    assert run(bucket) == cli.EXIT_MAIL
    assert _rows(conn) == 0 and INBOX + "alert.eml" in bucket.objects


def test_an_unknown_mode_processes_nothing_and_exits_nonzero(conn, run, monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_DRAIN_MODE", "maybe")
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE})
    assert run(bucket) == cli.EXIT_MAIL
    assert _rows(conn) == 0


def test_the_run_gives_the_bucket_the_short_read_timeout(conn, run, monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_R2_TIMEOUT_SECONDS", "3.5")
    run(FakeBucket({}))
    assert run.timeouts == [3.5]
