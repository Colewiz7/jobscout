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
import datetime
import email
import email.policy
import email.utils
import logging
import re

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
    """Every job in one alert.

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
        return []

    soup = BeautifulSoup(html, "html.parser")
    postings: list[Posting] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=_REDIRECT):
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
    return postings


INBOX = "careerconnect/inbox/"
FAILED = "careerconnect/failed/"


def drain(bucket) -> tuple[list[Posting], list[str], list[str]]:
    """Read every stored alert.

    Returns the postings, the keys that parsed, and the keys that did not.
    Nothing is deleted here: a key is only safe to remove once its postings
    are committed, and that happens in the caller where the transaction is.

    A message that does not parse is moved aside rather than dropped, so the
    parser can be fixed and the message replayed. The three Google forwarding
    confirmations sitting in the bucket are exactly that case: real messages
    that are not job alerts.
    """
    postings: list[Posting] = []
    parsed: list[str] = []
    failed: list[str] = []

    for key in bucket.keys(INBOX):
        try:
            raw = bucket.get(key)
        except Exception:
            log.exception("careerconnect: could not read %s, leaving it", key)
            continue
        try:
            found = parse(raw)
        except Exception:
            log.exception("careerconnect: %s did not parse, moving aside", key)
            failed.append(key)
            continue
        if not found:
            # Parsed cleanly and held no jobs. A confirmation email, not a
            # broken parser, so it goes aside rather than round again forever.
            log.info("careerconnect: %s held no jobs, moving aside", key)
            failed.append(key)
            continue
        postings.extend(found)
        parsed.append(key)

    log.info("careerconnect: %d alerts parsed, %d set aside, %d jobs",
             len(parsed), len(failed), len(postings))
    return postings, parsed, failed


def retire(bucket, parsed: list[str], failed: list[str]) -> None:
    """Called once the postings are committed, never before."""
    for key in parsed:
        try:
            bucket.delete(key)
        except Exception:
            log.exception("careerconnect: committed %s but could not delete it", key)
    for key in failed:
        try:
            bucket.move(key, FAILED + key.rsplit("/", 1)[-1])
        except Exception:
            log.exception("careerconnect: could not move %s aside", key)
