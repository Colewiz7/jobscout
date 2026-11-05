"""RIT Career Connect alert emails.

Career Connect has no API and its job pages sit behind SSO, so the saved-search
alert is the feed. Everything usable is in the message: the HTML part lists a
title, company, location and type per job, and the link carries an identifier.
The plain-text part is empty, so the HTML is not a fallback, it is the source.

A job appears in as many alerts as it matches saved searches, so the stable
identifier matters more here than anywhere else.
"""
from __future__ import annotations

import base64
import binascii
import dataclasses
import datetime
import email
import email.policy
import email.utils
import logging
import os
import random
import re
import time
from typing import Callable

from bs4 import BeautifulSoup

from ..models import Posting

log = logging.getLogger(__name__)

SOURCE = "careerconnect"

# Symplicity wraps the real path in base64 behind a redirect. The path is
# /app/jobs/detail/<32 hex>, and that hex is the only thing two copies of the
# same posting from two different saved searches agree on.
_REDIRECT = re.compile(r"[?&]jobRedirectUrl=([^&\s\"']+)")
_DETAIL = re.compile(r"/app/jobs/detail/([0-9a-f]{32})")

# `Cole, the latest "infrastructure" jobs are here`. The quotes have been seen
# both singly and doubled, so both are accepted.
_SUBJECT_TERM = re.compile(r'"{1,2}([^"]+)"{1,2}')

_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip()


def _job_id(href: str) -> str | None:
    found = _REDIRECT.search(href or "")
    if not found:
        return None
    blob = found.group(1)
    try:
        path = base64.b64decode(blob + "=" * (-len(blob) % 4)).decode("utf-8", "ignore")
    except (binascii.Error, ValueError):
        return None
    detail = _DETAIL.search(path)
    return detail.group(1) if detail else None


def search_term(subject: str) -> str:
    """Which saved search produced this alert."""
    found = _SUBJECT_TERM.search(subject or "")
    return _clean(found.group(1)) if found else ""


def _age_days(sent: datetime.datetime | None) -> int | None:
    if sent is None:
        return None
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return max((now - sent).days, 0)


def parse(raw: bytes) -> list[Posting]:
    return scan(raw)[0]


JOBS = "jobs"
IGNORE = "ignore"

# marks mail as not an alert
_GOOGLE_SENDER = "forwarding-noreply@google.com"
_JOB_LINK = re.compile(r"jobRedirectUrl|/app/jobs/detail", re.I)
_ALERT_SENDER = re.compile(r"careerconnect\.|symplicity", re.I)
_ALERT_SUBJECT = re.compile(r"jobs are here|job matches", re.I)


def _empty_verdict(message, html: str, linked: bool) -> str:
    sender = "%s %s" % (message.get("From") or "", message.get("Return-Path") or "")
    subject = str(message.get("Subject") or "")
    if _GOOGLE_SENDER in sender.lower() or "forwarding confirmation" in subject.lower():
        return IGNORE
    if not html:
        return "no readable html part"
    if linked or _JOB_LINK.search(html):
        return "job links present but none parsed"
    if _ALERT_SENDER.search(sender) or _ALERT_SUBJECT.search(subject):
        return "alert layout has no job links"
    return IGNORE


def scan(raw: bytes) -> tuple[list[Posting], str]:
    """Every job in one alert, and a verdict: jobs, ignore, or a failure reason.

    There are no dates and no descriptions in the message, so the age of the
    posting is the age of the alert that carried it. That is a ceiling rather
    than a fact, which is the honest reading: the job is at most this old.
    """
    message = email.message_from_bytes(raw, policy=email.policy.default)
    subject = _clean(str(message.get("Subject") or ""))
    term = search_term(subject)
    sent = email.utils.parsedate_to_datetime(message.get("Date")) if message.get("Date") else None
    age = _age_days(sent)

    html = ""
    for part in message.walk():
        if part.get_content_type() == "text/html":
            html = part.get_content()
            break
    if not html:
        log.warning("careerconnect alert %r has no HTML part", subject)
        return [], _empty_verdict(message, "", False)

    soup = BeautifulSoup(html, "html.parser")
    postings: list[Posting] = []
    seen: set[str] = set()
    anchors = soup.find_all("a", href=_REDIRECT)

    for anchor in anchors:
        job_id = _job_id(anchor.get("href", ""))
        if job_id is None or job_id in seen:
            continue
        table = anchor.find_parent("table")
        if table is None:
            continue
        # Four cells in order: title, company, location, type.
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in table.find_all("td")]
        cells += [""] * (4 - len(cells))
        title, company, location, kind = cells[:4]
        if not title:
            continue
        seen.add(job_id)

        postings.append(
            Posting(
                source=SOURCE,
                # No board. A board is something the close rule can ask "what
                # is still open"; an alert is a snapshot of what was new that
                # morning, and a job missing from the next one has not closed.
                board=None,
                company=company or "unknown",
                title=title,
                location=location,
                url=anchor.get("href", ""),
                remote="remote" in location.lower(),
                provider_id=f"{SOURCE}:{job_id}",
                age_days=age,
                # Career Connect states the level itself, so it does not have
                # to be inferred from a title that may never say it.
                employment_type="INTERN" if "intern" in kind.lower() or "co-op" in kind.lower() else None,
            )
        )

    log.info("careerconnect %r: %d jobs", term or subject, len(postings))
    return postings, JOBS if postings else _empty_verdict(message, html, bool(anchors))


INBOX = "careerconnect/inbox/"
PROCESSED = "careerconnect/processed/"
IGNORED = "careerconnect/ignored/"
FAILED = "careerconnect/failed/"
REASON = ".reason.txt"

DEFAULT_LIMIT = 500
MODES = ("on", "dry-run", "off")
# this many 401/403 reads is systemic
SYSTEMIC_STREAK = 5
# tests seed or replace this
RNG = random.Random()
_UTC = datetime.timezone.utc
_LAST = datetime.datetime.max.replace(tzinfo=_UTC)


def _when(text: str) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat((text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=_UTC)


def _read(env, name: str, default, kind):
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return kind(raw.strip())
    except ValueError:
        raise ValueError("%s is not a valid %s" % (name, kind.__name__))


@dataclasses.dataclass
class Settings:
    mode: str = "on"
    limit: int = DEFAULT_LIMIT
    fail_ratio: float = 0.2
    fail_min: int = 3
    drain_seconds: int = 600
    r2_timeout: float = 10.0
    unlimited: bool = False
    error: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        try:
            return cls(
                mode=_read(env, "CAREERCONNECT_DRAIN_MODE", "on", str).lower(),
                limit=_read(env, "CAREERCONNECT_DRAIN_LIMIT", DEFAULT_LIMIT, int),
                fail_ratio=_read(env, "CAREERCONNECT_PARSE_FAIL_RATIO", 0.2, float),
                fail_min=_read(env, "CAREERCONNECT_PARSE_FAIL_MIN", 3, int),
                drain_seconds=_read(env, "CAREERCONNECT_DRAIN_SECONDS", 600, int),
                r2_timeout=_read(env, "CAREERCONNECT_R2_TIMEOUT_SECONDS", 10.0, float),
            )
        except ValueError as error:
            return cls(error=str(error))

    def check(self) -> str:
        if self.error:
            return self.error
        if self.mode not in MODES:
            return "CAREERCONNECT_DRAIN_MODE must be on, dry-run or off"
        if self.limit < 1 and not self.unlimited:
            return "CAREERCONNECT_DRAIN_LIMIT must be at least 1"
        if not 0 <= self.fail_ratio <= 1:
            return "CAREERCONNECT_PARSE_FAIL_RATIO must be between 0 and 1"
        if self.fail_min < 1:
            return "CAREERCONNECT_PARSE_FAIL_MIN must be at least 1"
        if self.drain_seconds < 1:
            return "CAREERCONNECT_DRAIN_SECONDS must be at least 1"
        if not self.r2_timeout > 0 or self.r2_timeout == float("inf"):
            return "CAREERCONNECT_R2_TIMEOUT_SECONDS must be a positive number"
        return ""


@dataclasses.dataclass
class DrainResult:
    mode: str = "on"
    listed: int = 0
    attempted: int = 0      # every key tried, read or not
    read: int = 0           # keys whose content was read
    vanished: int = 0
    streak: int = 0
    processed: int = 0
    ignored: int = 0
    failed: int = 0
    jobs: int = 0
    new: int = 0
    held: int = 0
    remaining: int = 0
    list_failed: bool = False
    errors: list[str] = dataclasses.field(default_factory=list)

    def problems(self, settings: Settings) -> list[str]:
        found = list(self.errors)
        if self.list_failed and not found:
            found.append("could not list the inbox")
        enough = self.failed >= settings.fail_min
        if enough and self.read and self.failed / self.read >= settings.fail_ratio:
            found.append("%d of %d alerts failed to parse" % (self.failed, self.read))
        return found


# stores a batch, returns counts
Ingest = Callable[[list[Posting]], tuple[int, int]]


def _name(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def _where(error: BaseException) -> str:
    # type and line, no text
    tb, last = error.__traceback__, None
    while tb is not None:
        last, tb = tb, tb.tb_next
    if last is None:
        return type(error).__name__
    return "%s at %s:%d" % (type(error).__name__, os.path.basename(last.tb_frame.f_code.co_filename), last.tb_lineno)


def _file_away(bucket, key: str, prefix: str, reason: str | None) -> None:
    # reason first, never missing
    if reason is not None:
        bucket.put(prefix + _name(key) + REASON, reason.encode("utf-8", "replace"))
    bucket.move(key, prefix + _name(key))


def _settle(bucket, key: str, prefix: str, reason: str | None, result: DrainResult, dry: bool) -> bool:
    if dry:
        return True
    try:
        _file_away(bucket, key, prefix, reason)
    except Exception as error:
        log.exception("careerconnect: could not move %s to %s", key, prefix)
        result.errors.append("move %s: %s" % (key, type(error).__name__))
        return False
    return True


def _handle(bucket, ingest: Ingest, key: str, result: DrainResult, dry: bool) -> bool:
    # false means stop the run
    result.attempted += 1
    try:
        raw = bucket.get(key)
    except Exception as error:
        status = getattr(error, "status", None)
        if status == 404:
            result.vanished += 1
            log.info("careerconnect: %s vanished before it was read", key)
            return True
        # untouched, retried, fails run
        log.error("careerconnect: could not read %s: %s", key, type(error).__name__)
        result.errors.append("read %s: %s" % (key, type(error).__name__))
        denied = status in (401, 403)
        result.streak = result.streak + 1 if denied else 0
        if result.streak >= SYSTEMIC_STREAK and result.read == 0:
            log.error("careerconnect: R2 refused %d reads in a row (HTTP %s), stopping", result.streak, status)
            result.errors.append("R2 refused %d reads in a row (HTTP %s)" % (result.streak, status))
            return False
        return True
    result.read += 1
    result.streak = 0

    def fail(reason: str) -> bool:
        if _settle(bucket, key, FAILED, reason, result, dry):
            result.failed += 1
        return True

    if not raw:
        log.warning("careerconnect: %s is empty", key)
        return fail("empty object")
    try:
        found, verdict = scan(raw)
    except Exception as error:
        log.exception("careerconnect: %s did not parse", key)
        return fail("parse error: " + _where(error))
    if verdict not in (JOBS, IGNORE):
        log.error("careerconnect: %s: %s", key, verdict)
        return fail(verdict)
    if verdict == IGNORE:
        log.info("careerconnect: %s is not an alert, ignoring", key)
        if _settle(bucket, key, IGNORED, None, result, dry):
            result.ignored += 1
        return True

    if dry:
        log.info("careerconnect dry-run: %s would store %d jobs and move to %s", key, len(found), PROCESSED)
        result.jobs += len(found)
        result.processed += 1
        return True
    try:
        new, held = ingest(found)
    except Exception as error:
        log.exception("careerconnect: could not store %s, stopping", key)
        result.errors.append("store %s: %s" % (key, _where(error)))
        return False
    # stored first, crash keeps it
    if _settle(bucket, key, PROCESSED, None, result, dry):
        result.processed += 1
        result.jobs += len(found)
        result.new += new
        result.held += held
    return True


def drain(bucket, ingest: Ingest, settings: Settings | None = None, prefix: str = INBOX, clock=time.monotonic, rng=None) -> DrainResult:
    settings = settings or Settings()
    result = DrainResult(mode=settings.mode)
    if settings.mode == "off" and not settings.error:
        log.info("careerconnect: drain mode is off, not touching the bucket")
        return result
    bad = settings.check()
    if bad:
        result.errors.append("bad careerconnect settings: " + bad)
        log.error("careerconnect: %s, nothing was processed", result.errors[-1])
        return result
    dry = settings.mode == "dry-run"

    try:
        listing = bucket.entries(prefix) if hasattr(bucket, "entries") else [(k, "") for k in bucket.keys(prefix)]
        # oldest first, undated last
        keys = [k for k, _ in sorted((e for e in listing if not e[0].endswith(REASON)), key=lambda e: (_when(e[1]) or _LAST, e[0]))]
    except Exception as error:
        log.exception("careerconnect: could not list %s", prefix)
        result.list_failed = True
        result.errors.append("list %s: %s" % (prefix, error))
        return result
    result.listed = len(keys)
    deadline = clock() + settings.drain_seconds
    timed_out = False
    order = keys
    if keys and not settings.unlimited:
        # random start, wrapping around
        start = (rng or RNG).randrange(len(keys))
        order = keys[start:] + keys[:start]
    # errors cost time, not slots
    for key in order:
        if not settings.unlimited and result.read >= settings.limit:
            break
        if not settings.unlimited and clock() >= deadline:
            timed_out = True
            break
        if not _handle(bucket, ingest, key, result, dry):
            break

    if result.vanished and result.vanished == result.attempted:
        # listing and reads disagree
        result.errors.append("every key tried returned 404, check the prefix and key encoding")
        log.error("careerconnect: %s", result.errors[-1])
    if timed_out and result.read == 0:
        result.errors.append("time budget spent before anything was read")
        log.error("careerconnect: %s", result.errors[-1])
    # only keys that still need work
    done = 0 if dry else result.processed + result.ignored + result.failed
    result.remaining = len(keys) - done - result.vanished
    if timed_out:
        log.warning("careerconnect: time budget reached, %d remaining", result.remaining)
    log.info(
        "careerconnect%s: %d listed, %d processed, %d ignored, %d failed, %d jobs (%d new, %d held back), %d remaining",
        " dry-run" if dry else "", result.listed, result.processed, result.ignored, result.failed,
        result.jobs, result.new, result.held, result.remaining,
    )
    return result
