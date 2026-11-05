"""Draining the Career Connect bucket."""
import pathlib
import random

import httpx
import pytest

from jobscout.sources import careerconnect, r2
from jobscout.sources.careerconnect import FAILED, IGNORED, INBOX, PROCESSED, REASON, Settings

FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "careerconnect_infrastructure.eml").read_bytes()

CONFIRMATION = (b"From: forwarding-noreply@google.com\r\n"
                b"Subject: Forwarding Confirmation\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"Please confirm forwarding.\r\n")

# links but a moved layout
BROKEN = (b"From: a@example.com\r\nSubject: jobs\r\nContent-Type: text/html\r\n\r\n"
          b"<html><body><a href='https://x.example/?jobRedirectUrl=Zm9v'>x</a></body></html>")


class FakeBucket:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.moves = []

    def keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key):
        return self.objects[key]

    def put(self, key, body):
        self.objects[key] = body

    def delete(self, key):
        self.objects.pop(key, None)

    def move(self, key, new_key):
        self.moves.append((key, new_key))
        self.objects[new_key] = self.objects.pop(key)


class First:
    # no rotation, so order stays predictable
    def randrange(self, n):
        return 0


class Recorder:
    def __init__(self):
        self.batches = []

    def __call__(self, postings):
        self.batches.append(postings)
        return len(postings), 0


def test_a_parsed_alert_is_stored_then_moved_to_processed():
    bucket = FakeBucket({INBOX + "alert.eml": FIXTURE})
    ingest = Recorder()
    result = careerconnect.drain(bucket, ingest)
    assert len(ingest.batches[0]) == 17
    assert result.processed == 1 and result.jobs == 17 and result.new == 17
    assert PROCESSED + "alert.eml" in bucket.objects
    assert INBOX + "alert.eml" not in bucket.objects


def test_nothing_is_ever_deleted():
    class NoDelete(FakeBucket):
        def delete(self, key):
            raise AssertionError("drain must not delete %s" % key)

    bucket = NoDelete({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": CONFIRMATION, INBOX + "c.eml": BROKEN})
    careerconnect.drain(bucket, Recorder())
    assert PROCESSED + "a.eml" in bucket.objects


def test_a_message_with_no_jobs_goes_to_ignored_not_failed():
    bucket = FakeBucket({INBOX + "confirm.eml": CONFIRMATION})
    ingest = Recorder()
    result = careerconnect.drain(bucket, ingest)
    assert ingest.batches == []
    assert result.ignored == 1 and result.failed == 0
    assert IGNORED + "confirm.eml" in bucket.objects
    assert FAILED + "confirm.eml" + REASON not in bucket.objects


def test_job_links_with_no_parsed_jobs_is_a_failure_with_a_reason():
    bucket = FakeBucket({INBOX + "odd.eml": BROKEN})
    result = careerconnect.drain(bucket, Recorder())
    assert result.failed == 1 and result.ignored == 0
    assert FAILED + "odd.eml" in bucket.objects
    assert b"none parsed" in bucket.objects[FAILED + "odd.eml" + REASON]


def test_a_parse_exception_goes_to_failed_with_a_reason(monkeypatch):
    def boom(raw):
        raise ValueError("bad layout")

    monkeypatch.setattr(careerconnect, "scan", boom)
    bucket = FakeBucket({INBOX + "x.eml": b"junk"})
    result = careerconnect.drain(bucket, Recorder())
    assert result.failed == 1
    reason = bucket.objects[FAILED + "x.eml" + REASON].decode()
    assert reason.startswith("parse error: ValueError at test_r2_drain.py:")
    assert "bad layout" not in reason
    assert FAILED + "x.eml" in bucket.objects


def test_an_empty_object_is_failed_not_retried_forever():
    bucket = FakeBucket({INBOX + "empty.eml": b""})
    result = careerconnect.drain(bucket, Recorder())
    assert result.failed == 1
    assert bucket.objects[FAILED + "empty.eml" + REASON] == b"empty object"
    assert bucket.keys(INBOX) == []


def test_a_crash_while_storing_leaves_the_alert_in_the_inbox():
    def broken_db(postings):
        raise RuntimeError("db down")

    bucket = FakeBucket({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": FIXTURE})
    result = careerconnect.drain(bucket, broken_db)
    assert bucket.keys(INBOX) == [INBOX + "a.eml", INBOX + "b.eml"]
    assert bucket.moves == []
    assert result.remaining == 2
    assert any("RuntimeError" in e for e in result.errors)


def test_each_alert_is_stored_before_it_moves():
    order = []

    class Spy(FakeBucket):
        def move(self, key, new_key):
            order.append("move")
            super().move(key, new_key)

    bucket = Spy({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": FIXTURE})
    careerconnect.drain(bucket, lambda postings: (order.append("store"), (0, 0))[1])
    assert order == ["store", "move", "store", "move"]


def test_the_per_run_cap_leaves_the_rest_and_says_how_many():
    bucket = FakeBucket({INBOX + ("%02d.eml" % n): FIXTURE for n in range(5)})
    result = careerconnect.drain(bucket, Recorder(), Settings(limit=2), rng=First())
    assert result.processed == 2 and result.listed == 5 and result.remaining == 3
    assert bucket.keys(INBOX) == [INBOX + "02.eml", INBOX + "03.eml", INBOX + "04.eml"]


def test_dry_run_reads_but_writes_and_moves_nothing():
    bucket = FakeBucket({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": CONFIRMATION, INBOX + "c.eml": BROKEN})
    ingest = Recorder()
    result = careerconnect.drain(bucket, ingest, Settings(mode="dry-run"))
    assert ingest.batches == [] and bucket.moves == []
    assert len(bucket.keys(INBOX)) == 3
    assert (result.processed, result.ignored, result.failed, result.jobs) == (1, 1, 1, 17)


def test_mode_off_does_not_touch_the_bucket():
    class Untouchable:
        def keys(self, prefix):
            raise AssertionError("listed")

    result = careerconnect.drain(Untouchable(), Recorder(), Settings(mode="off"))
    assert result.listed == 0 and result.errors == []


def test_an_unknown_mode_is_an_error_and_touches_nothing():
    bucket = FakeBucket({INBOX + "a.eml": FIXTURE})
    result = careerconnect.drain(bucket, Recorder(), Settings(mode="maybe"))
    assert result.errors and bucket.moves == []


def test_reason_files_are_not_drained_as_alerts():
    bucket = FakeBucket({FAILED + "a.eml" + REASON: b"why"})
    result = careerconnect.drain(bucket, Recorder(), prefix=FAILED)
    assert result.listed == 0


def test_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_DRAIN_MODE", "Dry-Run")
    monkeypatch.setenv("CAREERCONNECT_DRAIN_LIMIT", "7")
    monkeypatch.setenv("CAREERCONNECT_PARSE_FAIL_RATIO", "0.5")
    monkeypatch.setenv("CAREERCONNECT_PARSE_FAIL_MIN", "4")
    assert Settings.from_env() == Settings(mode="dry-run", limit=7, fail_ratio=0.5, fail_min=4)


def test_bad_settings_do_not_raise(monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_DRAIN_LIMIT", "lots")
    settings = Settings.from_env()
    assert careerconnect.drain(FakeBucket({}), Recorder(), settings).errors


# --- problems: what makes the job exit nonzero ---

def test_a_clean_drain_has_no_problems():
    bucket = FakeBucket({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": CONFIRMATION})
    settings = Settings()
    assert careerconnect.drain(bucket, Recorder(), settings).problems(settings) == []


def test_a_failed_list_is_a_problem():
    class NoList(FakeBucket):
        def keys(self, prefix):
            raise r2.R2Error("listing returned 403")

    settings = Settings()
    result = careerconnect.drain(NoList({}), Recorder(), settings)
    assert result.list_failed
    assert "403" in result.problems(settings)[0]


def test_an_unreadable_object_is_a_problem_and_stays_put():
    class Flaky(FakeBucket):
        def get(self, key):
            if "bad" in key:
                raise OSError("read failed")
            return super().get(key)

    bucket = Flaky({INBOX + "bad.eml": b"x", INBOX + "alert.eml": FIXTURE})
    settings = Settings()
    result = careerconnect.drain(bucket, Recorder(), settings)
    assert result.processed == 1
    assert INBOX + "bad.eml" in bucket.objects
    assert result.problems(settings)


def test_parse_failures_need_both_the_ratio_and_the_minimum():
    settings = Settings(fail_ratio=0.2, fail_min=3)
    two_of_two = careerconnect.DrainResult(read=2, failed=2)
    assert two_of_two.problems(settings) == []
    three_of_ten = careerconnect.DrainResult(read=10, failed=3)
    assert three_of_ten.problems(settings)
    three_of_thirty = careerconnect.DrainResult(read=30, failed=3)
    assert three_of_thirty.problems(settings) == []


# --- r2 listing ---

def _listing(keys, token=None):
    body = "<ListBucketResult>%s<IsTruncated>%s</IsTruncated>%s</ListBucketResult>" % (
        "".join("<Contents><Key>%s</Key></Contents>" % k for k in keys),
        "true" if token else "false",
        "<NextContinuationToken>%s</NextContinuationToken>" % token if token else "",
    )
    return httpx.Response(200, text=body)


def test_listing_follows_every_page():
    pages = {"": (["a", "b"], "t1"), "t1": (["c", "d"], "t2"), "t2": (["e"], None)}
    seen = []

    def handler(request):
        token = request.url.params.get("continuation-token", "")
        seen.append(token)
        return _listing(*pages[token])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", client)
    assert bucket.keys("careerconnect/inbox/") == ["a", "b", "c", "d", "e"]
    assert seen == ["", "t1", "t2"]


def test_a_failed_listing_raises():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", client)
    with pytest.raises(r2.R2Error):
        bucket.keys("x/")


# --- read errors: left alone, loud, bounded by the cap ---

class Down(FakeBucket):
    def get(self, key):
        if key.startswith(INBOX):
            raise OSError("body text that must not leak")
        return super().get(key)


def test_a_read_error_leaves_the_object_and_fails_the_run_without_leaking_text():
    bucket = Down({INBOX + "a.eml": FIXTURE})
    settings = Settings()
    result = careerconnect.drain(bucket, Recorder(), settings)
    assert bucket.keys(INBOX) == [INBOX + "a.eml"] and bucket.moves == []
    assert result.problems(settings) == ["read careerconnect/inbox/a.eml: OSError"]
    assert not [k for k in bucket.objects if k.startswith("careerconnect/attempts/")]


def test_a_vanished_object_is_skipped_not_an_error():
    class OneGone(FakeBucket):
        def get(self, key):
            if key.endswith("a.eml"):
                raise r2.R2Error("get returned 404", 404)
            return super().get(key)

    settings = Settings()
    result = careerconnect.drain(OneGone({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": FIXTURE}), Recorder(), settings)
    assert result.errors == [] and result.problems(settings) == [] and result.processed == 1
    assert result.attempted == 2 and result.read == 1


def test_every_key_returning_404_is_an_error_not_a_quiet_success():
    class AllGone(FakeBucket):
        def get(self, key):
            raise r2.R2Error("get returned 404", 404)

    settings = Settings()
    result = careerconnect.drain(AllGone({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": FIXTURE}), Recorder(), settings)
    assert result.problems(settings) and "404" in result.errors[0]


def test_other_http_statuses_are_errors():
    class Denied(FakeBucket):
        def get(self, key):
            raise r2.R2Error("get returned 403", 403)

    result = careerconnect.drain(Denied({INBOX + "a.eml": FIXTURE}), Recorder())
    assert result.errors


class Counter:
    # clock moving one second per look
    def __init__(self):
        self.now = 0

    def __call__(self):
        self.now += 1
        return self.now


def _stuck_bucket(stuck_count, good_count, status=503):
    stuck = {INBOX + ("0%03d.eml" % n) for n in range(stuck_count)}

    class Partly(FakeBucket):
        def get(self, key):
            if key in stuck:
                raise r2.R2Error("get returned %d" % status, status)
            return super().get(key)

    return Partly({k: FIXTURE for k in stuck} | {INBOX + ("1%03d.eml" % n): FIXTURE for n in range(good_count)}), stuck


def test_stuck_keys_cost_time_never_cap_slots_and_the_backlog_drains():
    bucket, stuck = _stuck_bucket(6, 10)
    settings = Settings(limit=5)
    done = []
    for _ in range(3):
        result = careerconnect.drain(bucket, Recorder(), settings, rng=First())
        done.append(result.processed)
        assert len(result.errors) == 6 and result.problems(settings)
    assert done == [5, 5, 0]
    assert sorted(bucket.keys(INBOX)) == sorted(stuck) and result.remaining == 6


def test_six_hundred_stuck_keys_do_not_hide_the_good_ones_behind_the_cap():
    bucket, _ = _stuck_bucket(600, 10)
    settings = Settings(limit=5)
    result = careerconnect.drain(bucket, Recorder(), settings, clock=lambda: 0, rng=First())
    assert result.processed == 5 and result.attempted == 605 and result.problems(settings)


def test_five_hundred_error_statuses_never_stop_the_run():
    bucket, _ = _stuck_bucket(8, 3, status=500)
    result = careerconnect.drain(bucket, Recorder(), Settings(limit=5), clock=lambda: 0)
    assert result.processed == 3 and not any("refused" in e for e in result.errors)


def test_the_time_budget_stops_the_run_and_reports_what_is_left():
    bucket = FakeBucket({INBOX + ("%02d.eml" % n): FIXTURE for n in range(20)})
    settings = Settings(limit=500, drain_seconds=5)
    result = careerconnect.drain(bucket, Recorder(), settings, clock=Counter())
    assert 0 < result.processed < 20 and result.errors == []
    assert result.remaining == 20 - result.processed


def test_the_time_budget_with_errors_still_fails_the_run():
    bucket, _ = _stuck_bucket(50, 0)
    settings = Settings(drain_seconds=5)
    result = careerconnect.drain(bucket, Recorder(), settings, clock=Counter())
    assert result.attempted < 50 and result.problems(settings) and result.remaining == 50


def test_keys_are_taken_oldest_first_when_dates_are_listed():
    class Dated(FakeBucket):
        def entries(self, prefix):
            stamps = {INBOX + "a.eml": "2026-03-01", INBOX + "b.eml": "2026-01-01", INBOX + "c.eml": "2026-02-01"}
            return [(k, stamps[k]) for k in self.keys(prefix)]

    bucket = Dated({INBOX + n: FIXTURE for n in ("a.eml", "b.eml", "c.eml")})
    careerconnect.drain(bucket, Recorder(), Settings(limit=2), rng=First())
    assert [m[0] for m in bucket.moves] == [INBOX + "b.eml", INBOX + "c.eml"]


def test_listing_reads_last_modified_from_the_bucket():
    body = ("<ListBucketResult><Contents><Key>a</Key><LastModified>2026-02-01T00:00:00Z</LastModified></Contents>"
            "<Contents><Key>b</Key></Contents><IsTruncated>false</IsTruncated></ListBucketResult>")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", client)
    assert bucket.entries("x/") == [("a", "2026-02-01T00:00:00Z"), ("b", "")]
    assert bucket.keys("x/") == ["a", "b"]


def test_a_get_error_carries_its_status():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", client)
    with pytest.raises(r2.R2Error) as caught:
        bucket.get("x")
    assert caught.value.status == 404


# --- listing fails closed ---

def test_a_truncated_listing_without_a_token_is_an_error():
    body = "<ListBucketResult><Contents><Key>a</Key></Contents><IsTruncated>true</IsTruncated></ListBucketResult>"
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", client)
    with pytest.raises(r2.R2Error, match="truncated"):
        bucket.keys("x/")


def test_a_repeated_continuation_token_cannot_loop_forever():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: _listing(["a"], "same")))
    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", client)
    with pytest.raises(r2.R2Error, match="repeated"):
        bucket.keys("x/")


# --- settings fail closed ---

class Untouchable(FakeBucket):
    def keys(self, prefix):
        raise AssertionError("must not list")


@pytest.mark.parametrize("name,value", [
    ("CAREERCONNECT_DRAIN_LIMIT", "0"),
    ("CAREERCONNECT_DRAIN_LIMIT", "-5"),
    ("CAREERCONNECT_DRAIN_LIMIT", "lots"),
    ("CAREERCONNECT_PARSE_FAIL_RATIO", "2"),
    ("CAREERCONNECT_PARSE_FAIL_RATIO", "nan"),
    ("CAREERCONNECT_PARSE_FAIL_MIN", "0"),
    ("CAREERCONNECT_DRAIN_SECONDS", "0"),
    ("CAREERCONNECT_DRAIN_SECONDS", "soon"),
    ("CAREERCONNECT_DRAIN_MODE", "maybe"),
])
def test_bad_settings_stop_the_drain_before_it_touches_anything(name, value, monkeypatch):
    monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    result = careerconnect.drain(Untouchable({}), Recorder(), settings)
    assert result.errors and "settings" in result.errors[0]
    assert result.problems(settings)


def test_mode_off_still_stays_quiet():
    assert careerconnect.drain(Untouchable({}), Recorder(), Settings(mode="off")).errors == []


# --- what counts as not an alert ---

ALERT_NO_HTML = (b"From: careerservices@careerconnect.rit.edu\r\nSubject: Student, the latest \"devops\" jobs are here\r\n"
                 b"Content-Type: text/plain\r\n\r\nplain only\r\n")
HTML_LINKS_NO_JOBS = (b"From: a@example.com\r\nSubject: hi\r\nContent-Type: text/html\r\n\r\n"
                      b"<html><body><a href='https://x.example/app/jobs/detail/abc'>x</a></body></html>")
UNRELATED = (b"From: friend@example.com\r\nSubject: lunch\r\nContent-Type: text/html\r\n\r\n"
             b"<html><body><p>lunch?</p></body></html>")
ALERT_EMPTY_HTML = (b"From: careerservices@careerconnect.example.org\r\nSubject: Student, here are your job matches\r\n"
                    b"Content-Type: text/html\r\n\r\n<html><body><p>nothing</p></body></html>")


@pytest.mark.parametrize("raw,outcome", [
    (CONFIRMATION, "ignored"),
    (UNRELATED, "ignored"),
    (ALERT_NO_HTML, "failed"),
    (HTML_LINKS_NO_JOBS, "failed"),
    (ALERT_EMPTY_HTML, "failed"),
])
def test_only_positively_non_alert_mail_is_ignored(raw, outcome):
    result = careerconnect.drain(FakeBucket({INBOX + "m.eml": raw}), Recorder())
    assert getattr(result, outcome) == 1


def test_an_alert_without_html_counts_toward_the_failure_threshold():
    bucket = FakeBucket({INBOX + ("%d.eml" % n): ALERT_NO_HTML for n in range(3)})
    settings = Settings()
    result = careerconnect.drain(bucket, Recorder(), settings)
    assert result.failed == 3 and result.problems(settings)
    assert bucket.objects[FAILED + "0.eml" + REASON] == b"no readable html part"


# --- read errors do not end the run ---

def test_stuck_oldest_keys_do_not_stop_the_good_ones_behind_them():
    stuck = {INBOX + ("0%d.eml" % n) for n in range(6)}

    class Partly(FakeBucket):
        def get(self, key):
            if key in stuck:
                raise OSError("stuck")
            return super().get(key)

    bucket = Partly({k: FIXTURE for k in stuck} | {INBOX + ("1%d.eml" % n): FIXTURE for n in range(10)})
    settings = Settings(limit=20)
    result = careerconnect.drain(bucket, Recorder(), settings)
    assert result.processed == 10 and len(result.errors) == 6
    assert result.problems(settings)


def test_five_forbidden_reads_in_a_row_stop_the_run_early():
    gets = []

    class Forbidden(FakeBucket):
        def get(self, key):
            gets.append(key)
            raise r2.R2Error("get returned 403", 403)

    bucket = Forbidden({INBOX + ("%02d.eml" % n): FIXTURE for n in range(30)})
    settings = Settings(limit=20)
    result = careerconnect.drain(bucket, Recorder(), settings)
    assert len(gets) == 5
    assert any("refused 5 reads in a row (HTTP 403)" in e for e in result.errors)
    assert result.problems(settings) and result.remaining == 30


def test_mixed_error_statuses_do_not_make_a_streak():
    statuses = iter([500, 500, 403, 500, 500, 500, 500])

    class Flappy(FakeBucket):
        def get(self, key):
            raise r2.R2Error("get failed", next(statuses))

    result = careerconnect.drain(Flappy({INBOX + ("%02d.eml" % n): FIXTURE for n in range(7)}), Recorder(), Settings(limit=20))
    assert result.attempted == 7 and not any("refused" in e for e in result.errors)


def test_a_streak_after_good_reads_does_not_stop_the_run():
    class LateForbidden(FakeBucket):
        def get(self, key):
            if key > INBOX + "01.eml":
                raise r2.R2Error("get returned 403", 403)
            return super().get(key)

    bucket = LateForbidden({INBOX + ("%02d.eml" % n): FIXTURE for n in range(12)})
    result = careerconnect.drain(bucket, Recorder(), Settings(limit=20))
    assert result.processed == 2 and result.attempted == 12


def test_undated_and_malformed_dates_sort_last_by_name():
    class Dated(FakeBucket):
        def entries(self, prefix):
            stamps = {INBOX + "a.eml": "", INBOX + "b.eml": "garbage", INBOX + "c.eml": "2026-03-01T00:00:00Z",
                      INBOX + "d.eml": "2026-01-01T00:00:00.000Z", INBOX + "e.eml": "2026-02-01T00:00:00+00:00"}
            return [(k, stamps[k]) for k in self.keys(prefix)]

    bucket = Dated({INBOX + n: FIXTURE for n in ("a.eml", "b.eml", "c.eml", "d.eml", "e.eml")})
    careerconnect.drain(bucket, Recorder(), Settings(), rng=First())
    assert [m[0].rsplit("/", 1)[-1] for m in bucket.moves] == ["d.eml", "e.eml", "c.eml", "a.eml", "b.eml"]


# --- keys with awkward characters survive a real listing ---

class FakeS3:
    # in-memory store, real client
    def __init__(self, objects):
        self.objects = dict(objects)
        self.paths = []

    def __call__(self, request):
        import urllib.parse
        from xml.sax.saxutils import escape

        raw_path, _, query = request.url.raw_path.decode().partition("?")
        self.paths.append(raw_path)
        key = urllib.parse.unquote(raw_path).split("/", 2)[2] if raw_path.count("/") >= 2 else ""
        if request.method == "GET" and not key:
            prefix = urllib.parse.parse_qs(query)["prefix"][0]
            body = "".join("<Contents><Key>%s</Key></Contents>" % escape(k) for k in sorted(self.objects) if k.startswith(prefix))
            return httpx.Response(200, text='<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">%s<IsTruncated>false</IsTruncated></ListBucketResult>' % body)
        if request.method == "GET":
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)


def test_keys_with_ampersands_spaces_plus_and_unicode_round_trip():
    names = ["a&b.eml", "sp ace.eml", "plus+.eml", "caf\u00e9.eml", "a<b>\"q\".eml"]
    fake = FakeS3({INBOX + n: FIXTURE for n in names})
    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", httpx.Client(transport=httpx.MockTransport(fake)))
    assert sorted(bucket.keys(INBOX)) == sorted(INBOX + n for n in names)
    result = careerconnect.drain(bucket, Recorder())
    assert result.processed == len(names) and result.errors == []
    assert sorted(k for k in fake.objects) == sorted(PROCESSED + n for n in names)
    joined = " ".join(fake.paths)
    assert "a%26b.eml" in joined and "sp%20ace.eml" in joined and "plus%2B.eml" in joined and "caf%C3%A9.eml" in joined


def test_the_listing_query_is_sorted_so_the_signature_matches():
    queries = []

    def handler(request):
        queries.append(request.url.query.decode())
        token = "t1" if len(queries) == 1 else None
        return _listing(["a"], token)

    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", httpx.Client(transport=httpx.MockTransport(handler)))
    bucket.keys("careerconnect/inbox/")
    names = [part.split("=")[0] for part in queries[1].split("&")]
    assert names == sorted(names) == ["continuation-token", "list-type", "max-keys", "prefix"]


def test_a_listing_with_a_dtd_is_refused():
    body = '<!DOCTYPE x [<!ENTITY e "boom">]><ListBucketResult><Contents><Key>&e;</Key></Contents></ListBucketResult>'
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    with pytest.raises(r2.R2Error, match="DTD"):
        r2.Bucket("https://r2.example", "mail", "id", "secret", client).keys("x/")


def test_a_namespaced_listing_parses():
    body = ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Contents><Key>a&amp;b</Key>'
            '<LastModified>2026-02-01T00:00:00.000Z</LastModified></Contents><IsTruncated>false</IsTruncated></ListBucketResult>')
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    assert r2.Bucket("https://r2.example", "mail", "id", "secret", client).entries("x/") == [("a&b", "2026-02-01T00:00:00.000Z")]


# --- rotation, timeouts, budget errors, remaining ---

class SlowStuck(FakeBucket):
    # stuck keys burn fake seconds
    def __init__(self, objects, stuck, clock):
        super().__init__(objects)
        self.stuck, self.clock = stuck, clock

    def get(self, key):
        if key in self.stuck:
            self.clock.now += 10
            raise OSError("timed out")
        self.clock.now += 0.1
        return super().get(key)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _slow_world():
    clock = FakeClock()
    stuck = {INBOX + ("0%d.eml" % n) for n in range(8)}
    objects = {k: FIXTURE for k in stuck} | {INBOX + ("1%d.eml" % n): FIXTURE for n in range(10)}
    return SlowStuck(objects, stuck, clock), clock, stuck


def test_seeded_rotation_reaches_every_good_key_while_every_run_still_fails():
    bucket, clock, stuck = _slow_world()
    settings = Settings(drain_seconds=50)
    for seed in range(1, 13):
        clock.now = 0.0
        result = careerconnect.drain(bucket, Recorder(), settings, clock=clock, rng=random.Random(seed))
        assert result.problems(settings), "run %d exited clean" % seed
    assert sorted(bucket.keys(INBOX)) == sorted(stuck)


def test_without_rotation_the_same_world_starves_the_good_keys():
    bucket, clock, stuck = _slow_world()
    settings = Settings(drain_seconds=50)
    for _ in range(12):
        clock.now = 0.0
        careerconnect.drain(bucket, Recorder(), settings, clock=clock, rng=First())
    assert len(bucket.keys(INBOX)) == 18


def test_unlimited_runs_keep_a_deterministic_order():
    class Boom:
        def randrange(self, n):
            raise AssertionError("no rotation when unlimited")

    bucket = FakeBucket({INBOX + n: FIXTURE for n in ("b.eml", "a.eml")})
    careerconnect.drain(bucket, Recorder(), Settings(unlimited=True), rng=Boom())
    assert [m[0].rsplit("/", 1)[-1] for m in bucket.moves] == ["a.eml", "b.eml"]


def test_dry_run_uses_the_same_rotation():
    bucket = FakeBucket({INBOX + n: FIXTURE for n in ("a.eml", "b.eml", "c.eml")})
    seen = []

    class Spy(Recorder):
        pass

    class Rng:
        def randrange(self, n):
            seen.append(n)
            return 2

    careerconnect.drain(bucket, Spy(), Settings(mode="dry-run"), rng=Rng())
    assert seen == [3]


def test_a_budget_spent_before_any_attempt_is_an_error():
    ticks = iter([0, 10_000, 10_000, 10_000])
    settings = Settings()
    result = careerconnect.drain(FakeBucket({INBOX + "a.eml": FIXTURE}), Recorder(), settings, clock=lambda: next(ticks), rng=First())
    assert result.attempted == 0 and result.problems(settings)
    assert any("time budget" in e for e in result.errors)


def test_a_budget_stop_after_real_progress_stays_clean():
    bucket = FakeBucket({INBOX + ("%02d.eml" % n): FIXTURE for n in range(20)})
    settings = Settings(drain_seconds=5)
    clock = Counter()
    result = careerconnect.drain(bucket, Recorder(), settings, clock=clock, rng=First())
    assert 0 < result.processed < 20 and result.problems(settings) == []


def test_remaining_does_not_count_keys_that_vanished():
    class OneGone(FakeBucket):
        def get(self, key):
            if key.endswith("a.eml"):
                raise r2.R2Error("get returned 404", 404)
            return super().get(key)

    result = careerconnect.drain(OneGone({INBOX + "a.eml": FIXTURE, INBOX + "b.eml": FIXTURE}), Recorder(), rng=First())
    assert result.processed == 1 and result.remaining == 0


def test_dry_run_remaining_is_everything_still_to_do():
    bucket = FakeBucket({INBOX + n: FIXTURE for n in ("a.eml", "b.eml", "c.eml")})
    result = careerconnect.drain(bucket, Recorder(), Settings(mode="dry-run"), rng=First())
    assert result.attempted == 3 and result.remaining == 3 and bucket.moves == []


def test_dry_run_remaining_still_leaves_out_vanished_keys():
    class OneGone(FakeBucket):
        def get(self, key):
            if key.endswith("a.eml"):
                raise r2.R2Error("get returned 404", 404)
            return super().get(key)

    bucket = OneGone({INBOX + n: FIXTURE for n in ("a.eml", "b.eml", "c.eml")})
    assert careerconnect.drain(bucket, Recorder(), Settings(mode="dry-run"), rng=First()).remaining == 2


def test_reads_use_the_short_timeout_and_the_rest_keep_the_default():
    timeouts = {}

    def handler(request):
        timeouts[request.method] = request.extensions["timeout"]["read"]
        return httpx.Response(200, content=b"x")

    bucket = r2.Bucket("https://r2.example", "mail", "id", "secret", httpx.Client(transport=httpx.MockTransport(handler), timeout=30.0), get_timeout=7.5)
    bucket.get("k")
    bucket.put("k", b"x")
    assert timeouts == {"GET": 7.5, "PUT": 30.0}


def test_the_default_read_timeout_is_ten_seconds(monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_R2_TIMEOUT_SECONDS", "4")
    assert Settings.from_env().r2_timeout == 4.0
    monkeypatch.delenv("CAREERCONNECT_R2_TIMEOUT_SECONDS")
    assert Settings.from_env().r2_timeout == 10.0


@pytest.mark.parametrize("value", ["0", "-1", "fast", "nan", "inf"])
def test_a_bad_read_timeout_stops_the_drain(value, monkeypatch):
    monkeypatch.setenv("CAREERCONNECT_R2_TIMEOUT_SECONDS", value)
    settings = Settings.from_env()
    result = careerconnect.drain(Untouchable({}), Recorder(), settings)
    assert result.errors and "settings" in result.errors[0]
