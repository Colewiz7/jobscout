"""Dependency-free HTTP boundary for the JobSeer application workspace."""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import pathlib
import random
import re
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import yaml

from . import db as database
from .config import Config
from .filters import keep as keep_posting
from .models import Posting, workday_req_from_url
from .capture import PostingCapture, clean_url, manual_capture
from .descriptions import DescriptionAccessBlocked, ProviderDescriptionFetcher, extract_pay, parse_description, parse_sections
from .overview import OverviewService
from .eligibility import ELIGIBILITY_FIELDS, analyze as analyze_eligibility
from .http import Fetcher
from .liveness import LivenessResult, PostingLivenessChecker

log = logging.getLogger("jobscout.dashboard")
STATIC_ROOT = pathlib.Path(__file__).with_name("static")
DEFAULT_PROFILE = pathlib.Path(__file__).resolve().parents[2] / "config" / "profile.seed.json"
MAX_BODY = 64 * 1024
# 100,000 escaped characters fit
POSTING_EDIT_BODY = 640 * 1024
CSRF_COOKIE = "jobseer_csrf"
APP_ROUTE_ROOTS = frozenset({"inbox", "queue", "tracker", "companies", "profile"})
HIDDEN_CAPTURE = "You hid this job, so it stays hidden."
HIDDEN_WRITE = "This job is hidden, so it can't change."


def _queue_sort_key(job: dict) -> tuple:
    position = job.get("queue_position")
    deadline = job.get("deadline")
    if isinstance(deadline, str):
        try:
            deadline = dt.date.fromisoformat(deadline)
        except ValueError:
            deadline = None
    return (
        position is None,
        position if position is not None else 0,
        deadline is None,
        deadline or dt.date.max,
        -(job.get("score") or 0),
        str(job.get("company") or "").casefold(),
    )


def _session_keys(payload: dict) -> list[str] | None:
    keys = payload.get("dedupe_keys")
    if keys is None:
        return None
    if not isinstance(keys, list) or not keys or len(keys) > 500 or not all(isinstance(key, str) and key for key in keys):
        raise ValueError("dedupe_keys must be a list of job keys")
    return keys


def _session_pick(jobs: list[dict], dedupe_keys: list[str] | None) -> list[dict]:
    # a chosen run keeps its order
    if dedupe_keys is None:
        return jobs
    queued = {job["dedupe_key"]: job for job in jobs}
    return [queued[key] for key in dict.fromkeys(dedupe_keys) if key in queued]

def _visible_scoped_job(job: dict, config: Config) -> bool:
    """Retain tracked history, but hide old out-of-scope rows from Inbox."""
    sources = [part.strip() for part in str(job.get("sources") or "").split(",") if part.strip()]
    if job.get("status") != "new" or "manual" in sources:
        return True
    source = next((item for item in sources if item != "simplify-s27"), "simplify-s27")
    location = str(job.get("location") or "")
    return keep_posting(Posting(
        source=source, company=str(job.get("company") or ""),
        title=str(job.get("title") or ""), location=location,
        url=str(job.get("url") or ""), terms=str(job.get("terms") or ""),
        remote="remote" in location.lower(),
    ), config)


def _parse_client_datetime(value, label: str, *, optional: bool = False) -> dt.datetime | None:
    if value in {None, ""} and optional:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be a valid date and time") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _posting_edit(payload: dict) -> tuple[dict[str, str], Any]:
    unknown = sorted(set(payload) - {"company", "title", "url", "description"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be edited")
    fields = {}
    for name, label in (("company", "Company"), ("title", "Role title")):
        if name not in payload:
            continue
        value = payload[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} can't be empty. Add one and save again.")
        if len(value.strip()) > 500:
            raise ValueError(f"{label} must be under 500 characters")
        fields[name] = value.strip()
    if "url" in payload:
        if not isinstance(payload["url"], str):
            raise ValueError("Posting URL must be a public HTTPS address")
        fields["url"], _ = clean_url(payload["url"])
    description = None
    if "description" in payload:
        text = payload["description"].strip() if isinstance(payload["description"], str) else ""
        if not 40 <= len(text) <= 100_000:
            raise ValueError("Paste at least 40 and no more than 100,000 characters from the posting")
        description = parse_description(text)
    if not fields and description is None:
        raise ValueError("Change the company, role title, URL or description, then save")
    return fields, description


def _expected_revision(payload: dict) -> int | None:
    expected = payload.pop("expected_revision", None)
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError("expected_revision must be a whole number")
    return expected


def _session_changes(payload: dict) -> tuple[dict, int]:
    expected = _expected_revision(payload)
    if expected is None:
        raise ValueError("expected_revision is required")
    unknown = sorted(set(payload) - {"applied", "skipped", "ended"})
    if unknown:
        raise ValueError("unknown session fields: %s" % ", ".join(unknown))
    changes = {}
    if "applied" in payload:
        applied = payload["applied"]
        if not isinstance(applied, list) or len(applied) > 500 or not all(isinstance(key, str) and key for key in applied) or len(set(applied)) != len(applied):
            raise ValueError("applied must be a list of job keys")
        changes["applied"] = applied
    if "skipped" in payload:
        skipped = payload["skipped"]
        if not isinstance(skipped, list) or len(skipped) > 500:
            raise ValueError("skipped must be a list")
        changes["skipped"] = []
        for item in skipped:
            if not isinstance(item, dict) or not isinstance(item.get("dedupe_key"), str) or not isinstance(item.get("reason", ""), str):
                raise ValueError("each skipped job needs a dedupe_key and a reason")
            changes["skipped"].append({"dedupe_key": item["dedupe_key"], "reason": item.get("reason", "")[:200]})
    if "ended" in payload:
        if not isinstance(payload["ended"], bool):
            raise ValueError("ended must be true or false")
        changes["ended"] = payload["ended"]
    if not changes:
        raise ValueError("nothing to change")
    return changes, expected


# only the hash is stored
def _hide_undo_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def _hide_undo_hash(token) -> str:
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        raise ValueError("undo_token must be the token the hide returned")
    return hashlib.sha256(token.encode()).hexdigest()


def _view_changes(payload: dict) -> dict:
    unknown = sorted(set(payload) - {"name", "filters", "sort"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be edited")
    return payload


def _interview_changes(payload: dict) -> dict:
    unknown = sorted(set(payload) - {"starts_at", "ends_at", "location", "notes", "cancelled"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be edited")
    changes: dict[str, Any] = {}
    if "starts_at" in payload:
        changes["starts_at"] = _parse_client_datetime(payload["starts_at"], "interview start")
    if "ends_at" in payload:
        changes["ends_at"] = _parse_client_datetime(payload["ends_at"], "interview end", optional=True)
    for name, limit in (("location", 500), ("notes", 20_000)):
        if name not in payload:
            continue
        if not isinstance(payload[name], str) or len(payload[name]) > limit:
            raise ValueError(f"{name} must be text under {limit:,} characters")
        changes[name] = payload[name].strip()
    if "cancelled" in payload:
        if not isinstance(payload["cancelled"], bool):
            raise ValueError("cancelled must be true or false")
        changes["cancelled"] = payload["cancelled"]
    if not changes:
        raise ValueError("Change the time, location or notes, then save")
    return changes


def _contact_changes(payload: dict) -> dict:
    unknown = sorted(set(payload) - {"name", "title", "email", "linkedin_url"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be edited")
    changes = {}
    for name, limit in (("name", 500), ("title", 500), ("email", 500), ("linkedin_url", 2_000)):
        if name not in payload:
            continue
        if not isinstance(payload[name], str) or len(payload[name].strip()) > limit:
            raise ValueError(f"{name} must be text under {limit:,} characters")
        changes[name] = payload[name].strip()
    if "name" in changes and not changes["name"]:
        raise ValueError("A contact needs a name. Add one and save again.")
    if changes.get("linkedin_url") and not changes["linkedin_url"].startswith("https://"):
        raise ValueError("LinkedIn URL must use HTTPS")
    if not changes:
        raise ValueError("Change the name, role, email or LinkedIn URL, then save")
    return changes


def _reminder_change(payload: dict) -> tuple[str, dt.datetime | None, int | None]:
    payload = dict(payload)
    expected = _expected_revision(payload)
    unknown = sorted(set(payload) - {"action", "until"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be changed")
    action = payload.get("action")
    if action not in database.REMINDER_ACTIONS:
        raise ValueError("reminder action must be done, reopen, snooze or unsnooze")
    until = _parse_client_datetime(payload.get("until"), "snooze date", optional=True)
    if until is not None and action != "snooze":
        raise ValueError("only a snooze takes a date")
    _within_a_year(until, "snooze date")
    return action, until, expected


# undo may send a past date
def _within_a_year(value: dt.datetime | None, label: str) -> None:
    if value is not None and abs(value - dt.datetime.now(dt.timezone.utc)) > dt.timedelta(days=366):
        raise ValueError(f"{label} must be within a year of today")


def _new_reminder(payload: dict) -> tuple[str, dt.datetime, str]:
    unknown = sorted(set(payload) - {"dedupe_key", "due_at", "note"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be set")
    key, note = payload.get("dedupe_key"), payload.get("note", "")
    if not isinstance(key, str) or not key or not isinstance(note, str):
        raise ValueError("a follow-up needs a job and a text note")
    if len(note.strip()) > 500:
        raise ValueError("A follow-up note must be under 500 characters")
    due_at = _parse_client_datetime(payload.get("due_at"), "follow-up date")
    _within_a_year(due_at, "follow-up date")
    return key, due_at, note.strip()


def _rule_changes(payload: dict) -> dict:
    unknown = sorted(set(payload) - {"name", "kind", "pattern", "value"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be edited")
    if not all(isinstance(value, str) for value in payload.values()):
        raise ValueError("rule fields must be text")
    if not payload:
        raise ValueError("Change the name, action, match or value, then save")
    return dict(payload)


def _overview_payload(row: dict | None) -> dict:
    return {
        "status": row["status"] if row else "missing",
        "items": row["items"] or [] if row else [],
        "source": row["source"] if row else None,
        "error": row["error"] if row and row["status"] == "failed" else None,
        "dismissed": bool(row and row.get("dismissed_at")),
        "revision": row.get("revision") if row else None,
        "can_undo": bool(row and row.get("has_previous")),
    }


# the ui shows and revokes these
def _with_overrides(result: dict, overrides: list[dict]) -> dict:
    result["overrides"] = [
        {name: item.get(name) for name in ("id", "blocker_key", "note", "created_at")} for item in overrides
    ]
    return result


def _override_ids(payload: dict, *, required: bool) -> list[int] | None:
    ids = payload.get("ids")
    if ids is None and not required:
        return None
    if not isinstance(ids, list) or not ids or len(ids) > 20 or not all(type(item) is int for item in ids):
        raise ValueError("ids must be a list of override ids")
    return ids


def _company_name(payload: dict) -> str:
    unknown = sorted(set(payload) - {"name"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be edited")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A company needs a name. Add one and save again.")
    if len(name.strip()) > 500:
        raise ValueError("Company name must be under 500 characters")
    return name.strip()


def _link_fields(payload: dict, *, partial: bool = False) -> dict:
    unknown = sorted(set(payload) - {"label", "url"})
    if unknown:
        raise ValueError(f"{unknown[0]} can't be edited")
    fields = {}
    if "label" in payload or not partial:
        label = payload.get("label", "")
        if not isinstance(label, str) or len(label.strip()) > 200:
            raise ValueError("Link label must be text under 200 characters")
        fields["label"] = label.strip()
    if "url" in payload or not partial:
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("A link needs a URL. Add one and save again.")
        url = url.strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or len(url) > 2_000:
            raise ValueError("Link URL must be an https:// address under 2,000 characters")
        fields["url"] = url
    if not fields:
        raise ValueError("Change the label or URL, then save")
    return fields


def _merge_target(payload: dict) -> str:
    target = payload.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Pick a company to merge into.")
    return target


def _import_connections(store, contacts: list[dict]) -> dict:
    companies = {
        key: item["name"] for item in store.companies()["companies"] for key in item["match_keys"]
    }
    matched = []
    for contact in contacts:
        key = database.company_key(str(contact.get("company") or ""))
        if key not in companies:
            continue
        matched.append({**contact, "company": companies[key]})
    saved = [store.save_contact(item, source="linkedin_csv") for item in matched]
    return {"imported": len(saved), "unmatched": len(contacts) - len(matched)}


def _company_view(jobs: list[dict], companies: dict[str, dict]) -> list[dict]:
    """Point each job at the company it now belongs to."""
    for job in jobs:
        posted = str(job.get("company") or "")
        raw = database.company_key(posted)
        key = database.resolve_company(companies, posted) or raw
        job["company_key"] = key
        job["posting_company"] = posted
        row = companies.get(key)
        # untouched companies keep posting names
        if row and (key != raw or row.get("display_name")):
            job["company"] = database.company_name(row)
    return jobs


def _company_groups(jobs: list[dict], companies: dict[str, dict] | None = None, counts: dict[str, dict] | None = None) -> list[dict]:
    companies, counts = companies or {}, counts or {}
    merged: dict[str, list[str]] = {}
    for key in companies:
        target = database.canonical_key(companies, key)
        if target != key:
            merged.setdefault(target, []).append(key)
    grouped: dict[str, dict] = {}
    tracked = {"applying", "applied", "interviewing", "offer", "rejected"}

    def group(key: str, name: str) -> dict:
        if key in grouped:
            return grouped[key]
        row = companies.get(key, {})
        aliases = sorted(merged.get(key, []))
        extra = counts.get(key, {})
        grouped[key] = {
            "key": key,
            "name": row.get("display_name") or name,
            "postings": [],
            "applications": [],
            "connections_count": 0,
            "links": [],
            "hidden": bool(row.get("hidden_at")),
            "manual": bool(row.get("manual")),
            "revision": row.get("revision", 0),
            "aliases": [database.company_name(companies[other]) for other in aliases],
            "match_keys": sorted({key, *aliases, *([database.company_key(row["display_name"])] if row.get("display_name") else [])}),
            "contacts_count": extra.get("contacts", 0),
            "saved_links_count": extra.get("links", 0),
            "has_note": bool(extra.get("note")),
        }
        return grouped[key]

    for job in jobs:
        key = job.get("company_key") or database.company_key(str(job.get("company") or ""))
        if not key:
            continue
        found = group(key, job["company"])
        found["postings"].append(job)
        if job.get("status") in tracked:
            found["applications"].append(job)
        found["connections_count"] = max(
            found["connections_count"], int(job.get("connections_count") or 0)
        )
        url = str(job.get("url") or "")
        if url and not any(item["url"] == url for item in found["links"]):
            found["links"].append({"label": job["title"], "url": url})
    # hand-made ones have no jobs yet
    for key, row in companies.items():
        if row.get("manual") and not row.get("merged_into"):
            group(key, database.company_name(row))
    return sorted(
        grouped.values(),
        key=lambda item: (-len(item["applications"]), item["name"].casefold()),
    )


def _repost_title_key(value: str) -> str:
    words = database.normalise(value).split()
    ignored = {"spring", "summer", "fall", "autumn", "winter"}
    return " ".join(word for word in words if word not in ignored and not re.fullmatch(r"20\d{2}", word))


def _annotate_reposts(jobs: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for job in jobs:
        key = (database.company_key(str(job.get("company") or "")), _repost_title_key(str(job.get("title") or "")))
        if all(key):
            groups.setdefault(key, []).append(job)
    for appearances in groups.values():
        appearances.sort(key=lambda job: job.get("first_seen") or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
        for index, job in enumerate(appearances):
            seen = job.get("first_seen")
            if not isinstance(seen, dt.datetime):
                continue
            window = [
                item for item in appearances[:index + 1]
                if isinstance(item.get("first_seen"), dt.datetime)
                and seen - item["first_seen"] <= dt.timedelta(days=90)
            ]
            if len(window) >= 2:
                job["repost_count"] = len(window)
                job["repost_dates"] = [item["first_seen"].date().isoformat() for item in window]
                job["ghost_job"] = len(window) >= 3
    return jobs


def _annotate_rules(jobs: list[dict], rules: list[dict]) -> list[dict]:
    active = [rule for rule in rules if rule.get("enabled")]
    for job in jobs:
        title = database.normalise(str(job.get("title") or ""))
        company = database.company_key(str(job.get("company") or ""))
        tags: list[str] = []
        boost = 0
        for rule in active:
            pattern = database.normalise(str(rule.get("pattern") or ""))
            if rule.get("kind") == "tag_title" and pattern and pattern in title:
                tag = str(rule.get("value") or "")
                if tag and tag not in tags:
                    tags.append(tag)
            elif rule.get("kind") == "boost_company" and pattern and pattern in company:
                boost += int(rule.get("value") or 0)
        # stored apart, so reruns keep them
        manual = job.get("manual_tags") or []
        job["rule_tags"] = tags
        job["manual_tags"] = manual
        job["tags"] = tags + [tag for tag in manual if tag.casefold() not in {item.casefold() for item in tags}]
        job["rule_boost"] = min(boost, 100)
    return jobs


def _calendar_text(interviews: list[dict]) -> str:
    def stamp(value) -> str:
        parsed = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def clean(value) -> str:
        return (
            str(value or "").replace("\\", "\\\\").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;")
        )

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//JobSeer//Interviews//EN", "CALSCALE:GREGORIAN",
    ]
    for interview in interviews:
        start = interview["starts_at"]
        end = interview.get("ends_at") or (
            (start if isinstance(start, dt.datetime) else dt.datetime.fromisoformat(
                str(start).replace("Z", "+00:00")
            ))
            + dt.timedelta(hours=1)
        )
        summary = f"Interview — {interview.get('company', '')} — {interview.get('title', '')}"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:jobseer-interview-{interview['id']}@local",
            f"DTSTAMP:{now}",
            f"DTSTART:{stamp(start)}",
            f"DTEND:{stamp(end)}",
            f"SUMMARY:{clean(summary)}",
            f"LOCATION:{clean(interview.get('location'))}",
            f"DESCRIPTION:{clean(interview.get('notes'))}",
            *(["STATUS:CANCELLED"] if interview.get("cancelled_at") else []),
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def load_profile(path: str | os.PathLike | None = None) -> dict:
    """Load the non-secret seed and overlay private scalar fields from env."""
    profile_path = pathlib.Path(path or os.environ.get("JOBSCOUT_PROFILE") or DEFAULT_PROFILE)
    content = profile_path.read_text()
    raw = json.loads(content) if profile_path.suffix == ".json" else (yaml.safe_load(content) or {})
    if not isinstance(raw, dict):
        raise ValueError("profile YAML must contain a mapping")
    for field in ("name", "email", "phone", "location"):
        value = os.environ.get(f"JOBSCOUT_PROFILE_{field.upper()}")
        if value is not None:
            raw[field] = value
            for item in raw.get("fields", []):
                if item.get("key") == field:
                    item["value"] = value
    return raw


def _with_checks(profile: dict) -> dict:
    # deleting these needs a warning
    return {**profile, "eligibility_checks": ELIGIBILITY_FIELDS}


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class PostgresStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._filter_config = Config.load()
        self._fetcher = Fetcher(timeout=15, retry_seconds=20)
        self._capture = PostingCapture(self._fetcher)
        self._descriptions = ProviderDescriptionFetcher(self._fetcher)
        self._liveness = PostingLivenessChecker(self._fetcher)
        self._prefetch_lock = threading.Lock()
        self._prefetching: set[str] = set()

    def jobs(self, include_closed: bool = False) -> list[dict]:
        return self._jobs(include_closed)[0]

    def _jobs(self, include_closed: bool = False) -> tuple[list[dict], dict[str, dict]]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            jobs = database.dashboard_postings(conn, include_closed=include_closed)
            queue_texts = database.queued_description_texts(conn)
            counts = database.contact_counts(conn)
            active_rules = database.rules(conn)
            taglines = database.posting_taglines(conn)
            companies = database.companies(conn)
            tags = database.manual_tags(conn)
        # Old rows are retained for audit/history, but a changed scout filter
        # must remove newly discovered out-of-scope rows from today's Inbox.
        jobs = _company_view([job for job in jobs if _visible_scoped_job(job, self._filter_config)], companies)
        for job in jobs:
            job["connections_count"] = counts.get(job["company_key"], 0)
            description = queue_texts.get(job["dedupe_key"])
            job["pay"] = extract_pay(description)
            job["pay_checked"] = description is not None
            job["tagline"] = taglines.get(job["dedupe_key"], "")
            job["manual_tags"] = tags.get(job["dedupe_key"], [])
        return _annotate_rules(_annotate_reposts(jobs), active_rules), companies

    def company_icon_keys(self) -> list[str]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.company_icon_keys(conn)

    def company_icon(self, name: str) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.company_icon(conn, name)

    def overview_job(self, key: str) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.overview_job(conn, key)

    def dismiss_overview(self, key: str, dismissed: bool) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.dismiss_overview(conn, key, dismissed)

    def regenerate_overview(self, key: str) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.regenerate_overview(conn, key)

    def undo_regenerate_overview(self, key: str, expected: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.undo_regenerate_overview(conn, key, expected)

    def history_key(self, entry_id: int) -> str | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.history_key(conn, entry_id)

    def dismiss_history(self, entry_id: int, dismissed: bool) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.dismiss_history(conn, entry_id, dismissed)

    def enqueue_overview(self, key: str) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            if not database.description_target(conn, key):
                return False
            database.enqueue_overviews(conn, [key], priority=True)
        return True

    def capture(self, payload: dict) -> dict:
        captured = self._capture.capture(payload)
        posting = captured.posting
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            database.upsert_open(conn, [posting])
            target = database.description_target(conn, posting.dedupe_key)
            if captured.description is not None and target is not None:
                detail = captured.description
                database.save_description(
                    conn,
                    target["id"],
                    html=detail.html,
                    text=detail.text,
                    sections=detail.sections,
                    deadline=detail.deadline,
                    deadline_source=detail.deadline_source,
                    error=None,
                )
        job = next(
            (job for job in self.jobs(include_closed=True) if job["dedupe_key"] == posting.dedupe_key),
            None,
        )
        if job is None:
            raise ValueError(HIDDEN_CAPTURE)
        return job

    def rules(self) -> list[dict]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.rules(conn)

    def save_rule(self, payload: dict) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_rule(conn, payload)

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.set_rule_enabled(conn, rule_id, enabled)

    def undo_rule_action(self, action_id: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.undo_rule_action(conn, action_id)

    def tag_jobs(self, dedupe_keys: list[str], tag: str, *, remove: bool = False) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.tag_jobs(conn, dedupe_keys, tag, remove=remove)

    def tag_row_keys(self, ids: list[int]) -> list[str]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.tag_row_keys(conn, ids)

    def undo_tag_rows(self, ids: list[int], *, remove: bool) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.undo_tag_rows(conn, ids, remove=remove)

    def update_rule(self, rule_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _rule_changes(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.update_rule(conn, rule_id, changes, expected)

    def delete_rule(self, rule_id: int) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.delete_rule(conn, rule_id)

    def restore_rule(self, rule_id: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.restore_rule(conn, rule_id)

    def rules_with_order(self) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.rules_with_order(conn)

    def reorder_rules(self, rule_ids: list[int], expected: int) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_rule_order(conn, rule_ids, expected)

    def check_liveness(self, key: str, *, force: bool = False) -> LivenessResult:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            target = database.liveness_target(conn, key)
        if target is None:
            return LivenessResult("closed", "Posting no longer exists")
        checked_at = target.get("liveness_checked_at")
        if (
            not force and checked_at and target.get("liveness_status")
            and dt.datetime.now(dt.timezone.utc) - checked_at < dt.timedelta(minutes=15)
        ):
            return LivenessResult(target["liveness_status"], target.get("liveness_evidence") or "Cached check")
        result = self._liveness.check(target)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            database.save_liveness(conn, key, result.status, result.evidence)
        return result

    def save(
        self, key: str, status: str | None, notes: str | None,
        expected: str | None = None, expected_notes: str | None = None,
    ) -> dict:
        liveness = None
        effective_status = status
        if status == "queued":
            liveness = self.check_liveness(key)
            if liveness.status == "closed":
                effective_status = "archived"
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            saved = database.save_application_state(conn, key, effective_status, notes, expected, expected_notes)
        return {
            "saved": saved is not None,
            "status": saved or effective_status,
            "liveness": dataclasses.asdict(liveness) if liveness else None,
        }

    def hide(self, dedupe_keys: list[str]) -> dict:
        token, digest = _hide_undo_token()
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            created = database.hide_jobs(conn, dedupe_keys, digest)
        return {"hidden": created, "undo_token": token if created else None}

    def undo_hide(self, token: str) -> int:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.undo_hide(conn, _hide_undo_hash(token))

    def hidden_keys(self, dedupe_keys: list[str]) -> set[str]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.hidden_keys(conn, dedupe_keys)

    def reorder_queue(self, dedupe_keys: list[str]) -> list[str]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_queue_order(conn, dedupe_keys)

    def reorder_board(self, status: str, dedupe_keys: list[str]) -> list[str]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_board_order(conn, status, dedupe_keys)

    def start_session(self, dedupe_keys: list[str] | None = None) -> dict:
        # Do not probe the whole queue before showing the first job. A large
        # queue would make this request take minutes and appear blank to users.
        jobs = [job for job in self.jobs() if job["status"] == "queued"]
        jobs.sort(key=_queue_sort_key)
        return {"jobs": _session_pick(jobs, dedupe_keys), "skipped": []}

    def check_session_job(self, key: str) -> dict:
        job = next((item for item in self.jobs() if item["dedupe_key"] == key), None)
        # a reload finds it opened
        if job is None or job["status"] not in {"queued", "applying"}:
            raise ValueError("job is no longer queued")
        result = self.check_liveness(key, force=True)
        if result.status == "closed":
            with database.connect(self.dsn) as conn:
                database.require_schema(conn)
                database.save_application_state(conn, key, "archived", job.get("notes") or "")
        return dataclasses.asdict(result)

    def start_apply_session(self, keys: list[str]) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.start_apply_session(conn, keys)

    def apply_session(self) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.apply_session(conn)

    def update_apply_session(self, session_id: int, changes: dict, expected: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.update_apply_session(conn, session_id, changes, expected)

    def mark_applied(self, key: str, document_id) -> dict | None:
        self.description(key)
        parsed_document_id = int(document_id) if document_id not in {None, ""} else None
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.mark_application_applied(conn, key, parsed_document_id)

    def documents(self) -> list[dict]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.profile_data(conn)["documents"]

    def tracker(self) -> dict:
        applications = [
            job for job in self.jobs(include_closed=True)
            if job["status"] in {"applying", "applied", "interviewing", "offer", "rejected"}
        ]
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            support = database.tracker_support(conn)
        return {"applications": applications, **support}

    def update_reminder(self, reminder_id: int, payload: dict) -> dict | None:
        action, until, expected = _reminder_change(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_reminder(conn, reminder_id, action, until=until, expected=expected)

    def reminder_key(self, reminder_id: int) -> str | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.reminder_key(conn, reminder_id)

    def add_reminder(self, payload: dict) -> dict | None:
        key, due_at, note = _new_reminder(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.add_reminder(conn, key, due_at, note)

    def delete_reminder(self, reminder_id: int) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.delete_reminder(conn, reminder_id)

    def add_interview(self, payload: dict) -> dict:
        starts_at = _parse_client_datetime(payload.get("starts_at"), "interview start")
        ends_at = _parse_client_datetime(payload.get("ends_at"), "interview end", optional=True)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.add_interview(
                conn,
                dedupe_key=str(payload.get("dedupe_key") or ""),
                starts_at=starts_at,
                ends_at=ends_at,
                location=str(payload.get("location") or ""),
                notes=str(payload.get("notes") or ""),
            )

    def interview_key(self, interview_id: int) -> str | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.interview_key(conn, interview_id)

    def update_interview(self, interview_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _interview_changes(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.update_interview(conn, interview_id, changes, expected)

    def delete_interview(self, interview_id: int) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.delete_interview(conn, interview_id)

    def restore_interview(self, interview_id: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.restore_interview(conn, interview_id)

    def save_next_step(self, key: str, value: str, expected: str | None = None, reopen: list[dict] | None = None) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_next_step(conn, key, value, expected, reopen)

    def companies(self, name: str | None = None, *, include_account: bool = False) -> dict:
        jobs, companies = self._jobs(include_closed=True)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            counts = database.company_counts(conn)
        groups = _company_groups(jobs, companies, counts)
        if not name:
            return {"companies": groups}
        key = database.resolve_company(companies, name)
        company = next((item for item in groups if item["key"] == key), None)
        if company is None:
            return {"company": None}
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            records = database.company_records(conn, key)
            account = database.company_account(conn, key) if include_account else None
        company = {**company, **records, "account": account, "account_protected": not include_account}
        return {"company": company}

    def create_company(self, payload: dict) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.create_company(conn, _company_name(payload))

    def rename_company(self, key: str, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.rename_company(conn, key, _company_name(payload), expected)

    def hide_company(self, key: str, hidden: bool) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.hide_company(conn, key, hidden)

    def merge_company(self, key: str, payload: dict) -> dict | None:
        target = _merge_target(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.merge_companies(conn, key, target)

    def merge_target(self, merge_id: int) -> str | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.merge_target(conn, merge_id)

    def undo_company_merge(self, merge_id: int, payload: dict) -> dict | None:
        expected = _expected_revision(dict(payload))
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.undo_company_merge(conn, merge_id, expected)

    def add_company_link(self, key: str, payload: dict) -> dict | None:
        fields = _link_fields(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.add_company_link(conn, key, fields["label"], fields["url"])

    def update_link(self, link_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _link_fields(payload, partial=True)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.update_company_link(conn, link_id, changes, expected)

    def delete_link(self, link_id: int) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.delete_company_link(conn, link_id)

    def restore_link(self, link_id: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.restore_company_link(conn, link_id)

    def save_company_note(self, name: str, body: str, expected: str | None = None) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_company_note(conn, name, body, expected)

    def save_contact(self, payload: dict, source: str = "manual") -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_contact(conn, payload, source=source)

    def update_contact(self, contact_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _contact_changes(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.update_contact(conn, contact_id, changes, expected)

    def delete_contact(self, contact_id: int) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.delete_contact(conn, contact_id)

    def restore_contact(self, contact_id: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.restore_contact(conn, contact_id)

    def import_connections(self, contacts: list[dict]) -> dict:
        return _import_connections(self, contacts)

    def saved_views(self) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.saved_views(conn)

    def create_view(self, payload: dict) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.create_view(conn, payload)

    def update_view(self, view_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.update_view(conn, view_id, _view_changes(payload), expected)

    def delete_view(self, view_id: int) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.delete_saved_view(conn, view_id)

    def restore_view(self, view_id: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.restore_saved_view(conn, view_id)

    def reorder_views(self, view_ids: list[int], expected: int) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_view_order(conn, view_ids, expected)

    def profile(self, dedupe_key: str | None = None, company: str | None = None) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.profile_data(conn, dedupe_key=dedupe_key, company=company)

    def save_profile(self, payload: dict, revision: int | None = None, merge_documents: bool = False) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.replace_profile_data(conn, payload, revision=revision, merge_documents=merge_documents)

    def mark_copy(self, dedupe_key: str, target_key: str) -> None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            database.mark_quick_fill_copy(conn, dedupe_key, target_key)

    def save_profile_field(self, payload: dict) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_quick_fill_field(
                conn, dedupe_key=str(payload.get("dedupe_key", "")),
                company=str(payload.get("company", "")), field=payload.get("field"),
            )

    def eligibility(self, key: str) -> dict | None:
        description = self.description(key)
        if description is None:
            return None
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            profile = database.profile_data(conn)
            overrides = database.eligibility_overrides(conn, key)
        return _with_overrides(analyze_eligibility(
            text=str(description.get("description_text") or ""),
            sections=description.get("sections") or [],
            profile=profile,
            overrides=overrides,
        ), overrides)

    def override_eligibility(self, key: str, note: str = "") -> dict | None:
        result = self.eligibility(key)
        if result is None:
            return None
        blockers = [item for item in result["blockers"] if not item.get("overridden")]
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            ids = database.record_eligibility_overrides(conn, key, blockers, note)
        return {"eligibility": self.eligibility(key), "override_ids": ids}

    def revoke_eligibility(self, key: str, ids: list[int] | None = None) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            changed = database.revoke_eligibility_overrides(conn, key, ids)
        result = self.eligibility(key)
        return result and {"eligibility": result, "ids": changed}

    def restore_eligibility(self, key: str, ids: list[int]) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            changed = database.restore_eligibility_overrides(conn, key, ids)
        result = self.eligibility(key)
        return result and {"eligibility": result, "ids": changed}

    def healthy(self) -> bool:
        try:
            with database.connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute("select 1")
                return cur.fetchone() is not None
        except Exception:
            log.exception("database health check failed")
            return False

    def description(self, key: str, *, force: bool = False) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            cached = database.cached_description(conn, key)
            if cached and cached.get("description_text") and not force:
                # Improve older cached postings without refetching or a schema change.
                cached["sections"] = parse_sections(
                    cached.get("description_html") or cached["description_text"]
                )
                return cached
            if cached and cached.get("description_error") and not force:
                fetched_at = cached.get("description_fetched_at")
                now = dt.datetime.now(dt.timezone.utc)
                if fetched_at and now - fetched_at < dt.timedelta(minutes=15):
                    return cached
            target = database.description_target(conn, key)
        if target is None:
            return None

        try:
            detail = self._descriptions.fetch(target)
            if detail is None:
                values = dict(
                    html=None,
                    text=None,
                    error="This provider did not return a readable description.",
                )
            else:
                values = dict(
                    html=detail.html,
                    text=detail.text,
                    sections=detail.sections,
                    deadline=detail.deadline,
                    deadline_source=detail.deadline_source,
                    error=None,
                )
        except DescriptionAccessBlocked as exc:
            values = dict(html=None, text=None, error=str(exc))
        except Exception:
            log.exception("description fetch failed for %s", key)
            values = dict(
                html=None,
                text=None,
                error="The posting description could not be fetched. Retry shortly.",
            )
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            database.save_description(conn, target["id"], **values)
            return database.cached_description(conn, key)

    def prefetch_description(self, key: str) -> None:
        with self._prefetch_lock:
            if key in self._prefetching:
                return
            self._prefetching.add(key)

        def load() -> None:
            try:
                self.description(key)
            finally:
                with self._prefetch_lock:
                    self._prefetching.discard(key)

        threading.Thread(target=load, name="jobseer-description", daemon=True).start()

    def save_manual_description(self, key: str, text: str) -> dict | None:
        clean = text.strip()
        if not 40 <= len(clean) <= 100_000:
            raise ValueError("Paste at least 40 and no more than 100,000 characters from the posting")
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            target = database.description_target(conn, key)
            if target is None:
                return None
            detail = parse_description(clean)
            database.save_description(
                conn, target["id"], html=None, text=detail.text,
                sections=detail.sections, deadline=detail.deadline,
                deadline_source=detail.deadline_source, error=None,
            )
            return database.cached_description(conn, key)

    def edit_posting(self, key: str, payload: dict) -> dict | None:
        fields, description = _posting_edit(payload)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            edit = database.edit_posting(conn, key, fields, description)
        return None if edit is None else {"edit_id": edit["id"], **self._edited(key)}

    def posting_edit_key(self, edit_id: int) -> str | None:
        with database.connect(self.dsn) as conn, conn.cursor() as cur:
            database.require_schema(conn)
            cur.execute("select dedupe_key from posting_edits where id = %s", (edit_id,))
            row = cur.fetchone()
        return row["dedupe_key"] if row else None

    def undo_posting_edit(self, edit_id: int) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            undone = database.undo_posting_edit(conn, edit_id)
        return None if undone is None else self._edited(undone["dedupe_key"])

    def _edited(self, key: str) -> dict:
        job = next((item for item in self.jobs(include_closed=True) if item["dedupe_key"] == key), None)
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            detail = database.cached_description(conn, key)
        if detail and detail.get("description_text"):
            detail["sections"] = parse_sections(detail.get("description_html") or detail["description_text"])
        return {"job": job, "description": detail}

    def close(self) -> None:
        self._fetcher.__exit__()


def _demo_rows(count: int, seed: int, now: dt.datetime) -> list[dict]:
    """Deterministic large Inbox fixture for performance and accessibility QA."""
    rng = random.Random(seed)
    companies = [f"Company {index:02d}" for index in range(50)]
    roles = (
        "Platform Engineering Intern", "Site Reliability Co-op",
        "Cloud Infrastructure Intern", "DevOps Engineering Intern",
    )
    statuses = ("new", "new", "new", "saved", "queued", "applied", "archived")
    rows = []
    for index in range(count):
        age = index % 61
        company = companies[index % len(companies)]
        status = statuses[index % len(statuses)]
        applied_at = now - dt.timedelta(days=(index % 27) + 1)
        rows.append({
            "dedupe_key": f"fixture:{index:04d}",
            "company": company,
            "title": f"{roles[index % len(roles)]} {index + 1}",
            "location": "Remote in USA" if index % 4 == 0 else f"City {index % 20}, NY",
            "terms": "Summer 2027" if index % 3 == 0 else "",
            "age_days": age,
            "first_seen": now - dt.timedelta(days=age),
            "last_seen": now,
            "url": f"https://example.com/apply/{index}",
            "sources": "greenhouse, simplify-s27" if index % 9 == 0 else "greenhouse",
            "closed": False,
            "notified": True,
            "score": 200 - (index % 120) + rng.randint(0, 5),
            "score_detail": {"fixture": True},
            "status": status,
            "notes": "",
            "application_updated_at": applied_at if status != "new" else None,
            "recent_company_application_at": applied_at if index >= len(companies) else None,
            "deadline": None, "queue_position": None, "board_position": None,
            "liveness_status": None, "liveness_checked_at": None,
            "liveness_evidence": None, "applied_at": applied_at if status == "applied" else None,
            "resume_document_id": None, "resume_name": None,
        })
    return rows


class DemoStore:
    """Representative local data for visual development; never used by default."""

    def __init__(self, *, count: int = 2, seed: int = 0, profile_path=None):
        now = dt.datetime.now(dt.timezone.utc)
        base_rows = [
            {
                "dedupe_key": "demo:1", "company": "Cloudflare",
                "title": "Site Reliability Engineer Intern — Summer 2027",
                "location": "Austin, TX; Remote in USA", "terms": "Summer 2027",
                "age_days": 1, "first_seen": now - dt.timedelta(days=1),
                "last_seen": now, "url": "https://example.com/apply", "sources": "greenhouse",
                "closed": False, "notified": True, "score": 160,
                "score_detail": {"role_named": 100, "internship": 20, "wanted_term": 30, "fresh": 10},
                "status": "queued", "notes": "Mention the homelab incident response story.",
                "application_updated_at": now,
                "recent_company_application_at": None,
                "deadline": None, "queue_position": 0, "board_position": None,
                "liveness_status": "live", "liveness_checked_at": now,
                "liveness_evidence": "Demo posting is available",
                "applied_at": None, "resume_document_id": None, "resume_name": None,
            },
            {
                "dedupe_key": "demo:2", "company": "Datadog",
                "title": "Cloud Platform Engineering Intern",
                "location": "New York, NY", "terms": "", "age_days": 3,
                "first_seen": now - dt.timedelta(days=3), "last_seen": now,
                "url": "https://example.com/apply", "sources": "greenhouse, simplify-s27",
                "closed": False, "notified": True, "score": 130,
                "score_detail": {"role_named": 100, "internship": 20, "fresh": 10},
                "status": "new", "notes": "", "application_updated_at": None,
                "recent_company_application_at": None,
                "deadline": None, "queue_position": None, "board_position": None,
                "liveness_status": None, "liveness_checked_at": None,
                "liveness_evidence": None,
                "applied_at": None, "resume_document_id": None, "resume_name": None,
            },
        ]
        self.rows = _demo_rows(count, seed, now) if count > 2 else base_rows[:count]
        self._hidden: dict[str, dict] = {}
        self._next_hidden_id = 1
        self._hide_tokens: dict[str, dict] = {}
        self._overrides: dict[str, dict] = {}
        self._posting_edits: list[dict] = []
        self._descriptions: dict[str, dict] = {}
        self._saved_views: list[dict] = []
        self._next_view_id = 1
        self._view_order_revision = 0
        self._profile_path = profile_path or DEFAULT_PROFILE
        self._profile = load_profile(self._profile_path)
        self._next_profile_id = 0
        self._profile_revision = 0
        self._copied_fields: dict[str, set[str]] = {}
        self._answer_overrides: dict[str, dict[str, str]] = {}
        self._company_accounts: dict[str, dict] = {}
        self._reminders = []
        self._interviews = []
        self._contacts = []
        self._company_notes: dict[str, dict] = {}
        self._companies: dict[str, dict] = {}
        self._merges: list[dict] = []
        self._links: list[dict] = []
        self._next_reminder_id = 1
        self._next_interview_id = 1
        self._next_contact_id = 1
        self._eligibility_overrides: dict[str, list[dict]] = {}
        self._next_override_id = 1
        self._rules: list[dict] = []
        self._rule_order_revision = 0
        self._job_tags: list[dict] = []
        self._history: list[dict] = []
        self._dismissed_overviews: set[str] = set()
        self._apply_sessions: list[dict] = []
        self._rule_action_history: set[tuple[int, str]] = set()
        self._next_rule_id = 1
        self._next_rule_action_id = 1
        for row in self.rows:
            if row["status"] == "applied" and row.get("applied_at"):
                self._reminders.append({
                    "id": self._next_reminder_id,
                    "dedupe_key": row["dedupe_key"],
                    "kind": "follow_up",
                    "due_at": row["applied_at"] + dt.timedelta(days=7),
                    "status": "pending",
                    "snoozed_until": None, "completed_at": None, "note": "", "revision": 0,
                    "company": row["company"],
                    "title": row["title"],
                })
                self._next_reminder_id += 1

    def _upsert_demo_followup(self, row: dict) -> None:
        reminder = next(
            (item for item in self._reminders if item["dedupe_key"] == row["dedupe_key"] and item["kind"] == "follow_up"),
            None,
        )
        values = {
            "dedupe_key": row["dedupe_key"], "kind": "follow_up",
            "due_at": row["applied_at"] + dt.timedelta(days=7),
            "status": "pending", "snoozed_until": None, "completed_at": None,
            "company": row["company"], "title": row["title"],
        }
        if reminder:
            reminder.update(values)
        else:
            self._reminders.append({"id": self._next_reminder_id, "note": "", "revision": 0, **values})
            self._next_reminder_id += 1

    def _complete_demo_followup(self, key: str) -> list[dict]:
        closed = []
        for reminder in self._reminders:
            if reminder["dedupe_key"] == key and reminder["kind"] == "follow_up" and reminder["status"] != "done":
                reminder.update(status="done", completed_at=dt.datetime.now(dt.timezone.utc), revision=reminder.get("revision", 0) + 1)
                closed.append({"id": reminder["id"], "revision": reminder["revision"]})
        return closed

    def jobs(self, include_closed: bool = False) -> list[dict]:
        counts: dict[str, int] = {}
        for contact in self._contacts:
            if contact["source"] == "linkedin_csv" and not contact.get("deleted"):
                counts[contact["company_key"]] = counts.get(contact["company_key"], 0) + 1
        jobs = [
            self._with_override(row) for row in self.rows
            if (include_closed or not row["closed"]) and not self._is_hidden(row)
        ]
        for job in _company_view(jobs, self._company_registry()):
            job["connections_count"] = counts.get(job["company_key"], 0)
            job.setdefault("next_step", "")
            job["manual_tags"] = [row["tag"] for row in self._job_tags if row["dedupe_key"] == job["dedupe_key"] and not row["deleted"]]
        return _annotate_rules(_annotate_reposts(jobs), self._live_rules())

    # mirrors the scout's companies upsert
    def _company_registry(self) -> dict[str, dict]:
        renamed = database.renamed_owners(self._companies)
        for row in self.rows:
            name = str(self._with_override(row).get("company") or "").strip()
            key = database.company_key(name)
            if key and key not in self._companies:
                self._companies[key] = {
                    "key": key, "name": name, "display_name": None, "manual": False, "hidden_at": None,
                    "revision": 0, "merged_into": renamed.get(key) if renamed.get(key, key) != key else None,
                }
        return self._companies

    def _public_company(self, key: str) -> dict:
        row = self._companies[key]
        return copy.deepcopy({**{name: row[name] for name in ("key", "display_name", "manual", "hidden_at", "revision")},
                              "name": database.company_name(row), "hidden": row["hidden_at"] is not None})

    def _with_override(self, row: dict) -> dict:
        override = self._overrides.get(row["dedupe_key"], {})
        return {**row, **{name: value for name, value in override.items() if value is not None}}

    def _is_hidden(self, row: dict) -> bool:
        title = database.title_key(row["company"], row["title"])
        req = workday_req_from_url(str(row.get("url") or ""))
        return row["dedupe_key"] in self._hidden or any(
            title in entry["titles"] or (req and req in entry["reqs"]) for entry in self._hidden.values()
        )

    def hide(self, dedupe_keys: list[str]) -> list[dict]:
        created = []
        for key in dict.fromkeys(dedupe_keys):
            rows = [row for row in self.rows if row["dedupe_key"] == key]
            if not rows or any(self._is_hidden(row) for row in rows):
                continue
            self._hidden[key] = {
                "id": self._next_hidden_id,
                "titles": {database.title_key(row["company"], row["title"]) for row in rows} - {""},
                "reqs": {workday_req_from_url(str(row.get("url") or "")) for row in rows} - {None},
            }
            created.append({"id": self._next_hidden_id, "dedupe_key": key})
            self._next_hidden_id += 1
        if not created:
            return {"hidden": [], "undo_token": None}
        token, digest = _hide_undo_token()
        self._hide_tokens[digest] = {
            "ids": [entry["id"] for entry in created], "used": False,
            "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=database.HIDE_UNDO_SECONDS),
        }
        return {"hidden": created, "undo_token": token}

    def undo_hide(self, token: str) -> int:
        entry = self._hide_tokens.get(_hide_undo_hash(token))
        if entry is None:
            raise database.HideUndoRefused("This hide can't be undone from here.", unknown=True)
        if entry["used"]:
            raise database.HideUndoRefused("This hide was already undone.")
        if entry["expires_at"] < dt.datetime.now(dt.timezone.utc):
            raise database.HideUndoRefused("The time to undo this hide has passed.")
        entry["used"] = True
        doomed = [key for key, hidden in self._hidden.items() if hidden["id"] in entry["ids"]]
        for key in doomed:
            del self._hidden[key]
        return len(doomed)

    def hidden_keys(self, dedupe_keys: list[str]) -> set[str]:
        return {row["dedupe_key"] for row in self.rows if row["dedupe_key"] in dedupe_keys and self._is_hidden(row)}

    def company_icon_keys(self) -> list[str]:
        return []

    def company_icon(self, name: str) -> dict | None:
        return None

    def capture(self, payload: dict) -> dict:
        from .capture import clean_url

        url, _ = clean_url(payload.get("url"))
        posting = manual_capture(payload, url).posting
        existing = next(
            (row for row in self.rows if row["dedupe_key"] == posting.dedupe_key), None
        )
        if existing is not None and self._is_hidden(existing):
            raise ValueError(HIDDEN_CAPTURE)
        if existing is not None:
            return copy.deepcopy(existing)
        now = dt.datetime.now(dt.timezone.utc)
        row = {
            "dedupe_key": posting.dedupe_key, "company": posting.company,
            "title": posting.title, "location": posting.location,
            "terms": posting.terms, "age_days": 0, "first_seen": now,
            "last_seen": now, "url": posting.url, "sources": posting.source,
            "closed": False, "notified": False, "score": None,
            "score_detail": {}, "status": "new", "notes": "",
            "application_updated_at": None, "recent_company_application_at": None,
            "deadline": None, "queue_position": None, "board_position": None,
            "liveness_status": None, "liveness_checked_at": None,
            "liveness_evidence": None, "applied_at": None,
            "resume_document_id": None, "resume_name": None,
        }
        if self._is_hidden(row):
            raise ValueError(HIDDEN_CAPTURE)
        self.rows.append(row)
        return copy.deepcopy(row)

    def rules(self) -> list[dict]:
        return copy.deepcopy(self._live_rules())

    def _live_rules(self) -> list[dict]:
        return sorted((rule for rule in self._rules if not rule.get("deleted")), key=lambda rule: (rule["position"], rule["id"]))

    def _public_rule(self, rule: dict) -> dict:
        return copy.deepcopy({key: value for key, value in rule.items() if key != "deleted"})

    def _rule(self, rule_id: int, *, deleted: bool = False) -> dict | None:
        return next((rule for rule in self._rules if rule["id"] == rule_id and bool(rule.get("deleted")) == deleted), None)

    def _apply_demo_rules(self) -> None:
        for rule in self._live_rules():
            self._apply_demo_rule(rule)

    def _apply_demo_rule(self, rule: dict) -> None:
        if not rule.get("enabled") or rule.get("kind") != "archive_title":
            return
        pattern = database.normalise(rule["pattern"])
        for row in self.rows:
            if pattern not in database.normalise(row["title"]) or row["status"] != "new" or self._is_hidden(row):
                continue
            history_key = (rule["id"], row["dedupe_key"])
            if history_key in self._rule_action_history:
                continue
            previous = row["status"]
            self._rule_action_history.add(history_key)
            row.update(
                status="archived", archived_by_rule=rule["name"],
                rule_action_id=self._next_rule_action_id,
                rule_previous_status=previous,
                application_updated_at=dt.datetime.now(dt.timezone.utc),
            )
            self._next_rule_action_id += 1

    def save_rule(self, payload: dict) -> dict:
        clean = database.normalize_rule(payload)
        now = dt.datetime.now(dt.timezone.utc)
        rule = {
            "id": self._next_rule_id, **clean, "enabled": True,
            "position": max((item["position"] for item in self._rules), default=-1) + 1,
            "revision": 0, "created_at": now, "updated_at": now,
        }
        self._next_rule_id += 1
        self._rules.append(rule)
        self._apply_demo_rules()
        return self._public_rule(rule)

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> dict | None:
        rule = self._rule(rule_id)
        if rule is None:
            return None
        rule["enabled"] = bool(enabled)
        rule["updated_at"] = dt.datetime.now(dt.timezone.utc)
        self._apply_demo_rules()
        return self._public_rule(rule)

    def _live_tag(self, key: str, tag: str) -> dict | None:
        return next((row for row in self._job_tags if row["dedupe_key"] == key and not row["deleted"]
                     and row["tag"].casefold() == tag.casefold()), None)

    def tag_jobs(self, dedupe_keys: list[str], tag: str, *, remove: bool = False) -> dict | None:
        tag = database.clean_tag(tag)
        if not set(dedupe_keys) <= {row["dedupe_key"] for row in self.rows if not self._is_hidden(row)}:
            return None
        changed, ids = [], []
        for key in dict.fromkeys(dedupe_keys):
            found = self._live_tag(key, tag)
            if remove and found is not None:
                found["deleted"] = True
            elif not remove and found is None:
                found = {"id": len(self._job_tags) + 1, "dedupe_key": key, "tag": tag, "deleted": False}
                self._job_tags.append(found)
            else:
                continue
            changed.append(key)
            ids.append(found["id"])
        return {"changed": changed, "ids": sorted(ids)}

    def tag_row_keys(self, ids: list[int]) -> list[str]:
        return sorted({row["dedupe_key"] for row in self._job_tags if row["id"] in ids})

    def undo_tag_rows(self, ids: list[int], *, remove: bool) -> dict:
        hidden = {row["dedupe_key"] for row in self.rows if self._is_hidden(row)}
        rows = []
        for row in self._job_tags:
            if row["id"] not in ids or row["deleted"] == remove or row["dedupe_key"] in hidden:
                continue
            if not remove and self._live_tag(row["dedupe_key"], row["tag"]) is not None:
                continue
            row["deleted"] = remove
            rows.append(row)
        return {"changed": sorted({row["dedupe_key"] for row in rows}), "ids": sorted(row["id"] for row in rows)}

    def update_rule(self, rule_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _rule_changes(payload)
        rule = self._rule(rule_id)
        if rule is None:
            return None
        previous = self._public_rule(rule)
        if expected is not None and rule["revision"] != expected:
            raise database.RecordConflict(previous)
        rule.update(database.normalize_rule({**rule, **changes}), revision=rule["revision"] + 1,
                    updated_at=dt.datetime.now(dt.timezone.utc))
        self._apply_demo_rules()
        return {"rule": self._public_rule(rule), "previous": previous}

    def delete_rule(self, rule_id: int) -> bool:
        rule = self._rule(rule_id)
        if rule is not None:
            rule["deleted"] = True
        return rule is not None

    def restore_rule(self, rule_id: int) -> dict | None:
        rule = self._rule(rule_id, deleted=True)
        if rule is None:
            return None
        rule["deleted"] = False
        self._apply_demo_rules()
        return self._public_rule(rule)

    def rules_with_order(self) -> dict:
        return {"rules": self.rules(), "order_revision": self._rule_order_revision}

    def reorder_rules(self, rule_ids: list[int], expected: int) -> dict:
        if len(rule_ids) != len(set(rule_ids)) or len(rule_ids) > 500:
            raise ValueError("rule order contains duplicates or too many rules")
        live = self._live_rules()
        if expected != self._rule_order_revision or {rule["id"] for rule in live} != set(rule_ids):
            raise database.EditConflict("The rules changed in another tab. Reload and retry.")
        for position, rule_id in enumerate(rule_ids):
            self._rule(rule_id)["position"] = position
        self._rule_order_revision += 1
        return {"ids": rule_ids, "order_revision": self._rule_order_revision}

    def undo_rule_action(self, action_id: int) -> dict | None:
        row = next((item for item in self.rows if item.get("rule_action_id") == action_id), None)
        if row is None:
            return None
        if row["status"] != "archived":
            raise ValueError("the job is no longer archived by this rule")
        row.update(
            status=row.pop("rule_previous_status", "new"),
            archived_by_rule=None, rule_action_id=None,
            application_updated_at=dt.datetime.now(dt.timezone.utc),
        )
        return {"id": action_id, "undone_at": dt.datetime.now(dt.timezone.utc)}

    def save(
        self, key: str, status: str | None, notes: str | None,
        expected: str | None = None, expected_notes: str | None = None,
    ) -> dict:
        if status is not None and status not in database.APPLICATION_STATUSES:
            raise ValueError(f"unknown application status: {status}")
        for row in self.rows:
            if row["dedupe_key"] == key:
                previous_status = row["status"]
                if expected is not None and previous_status != expected:
                    raise database.StatusConflict(previous_status)
                previous_notes = row.get("notes") or ""
                if expected_notes is not None and previous_notes != expected_notes:
                    raise database.ValueConflict(previous_notes)
                # a notes edit skips the recheck
                recheck = status == "queued"
                if status is None:
                    status = previous_status
                if notes is None:
                    notes = previous_notes
                effective_status = status
                if recheck:
                    row.update(
                        liveness_status="closed" if row.get("closed") or not row.get("url") else "live",
                        liveness_checked_at=dt.datetime.now(dt.timezone.utc),
                        liveness_evidence="Posting is unavailable" if row.get("closed") or not row.get("url") else "Demo posting is available",
                    )
                    if row["liveness_status"] == "closed":
                        effective_status = "archived"
                self._log_status(key, previous_status, effective_status)
                row.update(
                    status=effective_status,
                    notes=notes,
                    application_updated_at=dt.datetime.now(dt.timezone.utc),
                )
                if effective_status != "archived" and row.get("rule_action_id"):
                    row.update(archived_by_rule=None, rule_action_id=None)
                if effective_status != "queued":
                    row["queue_position"] = None
                if effective_status != previous_status:
                    row["board_position"] = None
                if effective_status == "applied" and row.get("applied_at") is None:
                    row["applied_at"] = dt.datetime.now(dt.timezone.utc)
                    self._upsert_demo_followup(row)
                elif previous_status == "applied" and (
                    effective_status != "applied" or notes != previous_notes
                ):
                    self._complete_demo_followup(key)
                return {
                    "saved": True, "status": effective_status,
                    "liveness": {
                        "status": row.get("liveness_status"),
                        "evidence": row.get("liveness_evidence"),
                    } if recheck else None,
                }
        return {"saved": False, "status": status, "liveness": None}

    def _log_status(self, key: str, previous: str, status: str) -> None:
        if previous != status:
            self._history.append({
                "id": len(self._history) + 1, "dedupe_key": key, "from_status": previous,
                "to_status": status, "changed_at": dt.datetime.now(dt.timezone.utc), "dismissed_at": None,
            })

    def history_key(self, entry_id: int) -> str | None:
        return next((entry["dedupe_key"] for entry in self._history if entry["id"] == entry_id), None)

    def dismiss_history(self, entry_id: int, dismissed: bool) -> dict | None:
        entry = next((item for item in self._history if item["id"] == entry_id), None)
        if entry is None or (entry["dismissed_at"] is not None) == dismissed:
            return None
        entry["dismissed_at"] = dt.datetime.now(dt.timezone.utc) if dismissed else None
        return dict(entry)

    def overview_dismissed(self, key: str) -> bool:
        return key in self._dismissed_overviews

    def dismiss_overview(self, key: str, dismissed: bool) -> dict | None:
        if not any(row["dedupe_key"] == key for row in self.rows):
            return None
        if (key in self._dismissed_overviews) == dismissed:
            raise database.EditConflict("The overview changed in another tab.")
        (self._dismissed_overviews.add if dismissed else self._dismissed_overviews.discard)(key)
        return {"status": "ready", "items": None, "source": None, "error": None,
                "dismissed_at": dt.datetime.now(dt.timezone.utc) if dismissed else None}

    def reorder_queue(self, dedupe_keys: list[str]) -> list[str]:
        queued = {row["dedupe_key"] for row in self.rows if row["status"] == "queued" and not self._is_hidden(row)}
        if queued != set(dedupe_keys) or len(dedupe_keys) != len(set(dedupe_keys)):
            raise ValueError("queue order must contain every queued job exactly once")
        positions = {key: index for index, key in enumerate(dedupe_keys)}
        for row in self.rows:
            if row["dedupe_key"] in positions:
                row["queue_position"] = positions[row["dedupe_key"]]
        return dedupe_keys

    def reorder_board(self, status: str, dedupe_keys: list[str]) -> list[str]:
        if status not in database.BOARD_STATUSES:
            raise ValueError(f"not a board column: {status}")
        if len(dedupe_keys) != len(set(dedupe_keys)):
            raise ValueError("board order contains duplicates or too many jobs")
        column = [row for row in self.rows if row["status"] == status]
        if not set(dedupe_keys) <= {row["dedupe_key"] for row in column}:
            raise ValueError("board order must only contain jobs in that column")
        positions = {key: index for index, key in enumerate(dedupe_keys)}
        for row in column:
            row["board_position"] = positions.get(row["dedupe_key"])
        return dedupe_keys

    def start_session(self, dedupe_keys: list[str] | None = None) -> dict:
        queued = [dict(row) for row in self.rows if row["status"] == "queued"]
        queued.sort(key=_queue_sort_key)
        return {"jobs": _session_pick(queued, dedupe_keys), "skipped": []}

    def check_session_job(self, key: str) -> dict:
        row = next((item for item in self.rows if item["dedupe_key"] == key and item["status"] in {"queued", "applying"}), None)
        if row is None:
            raise ValueError("job is no longer queued")
        closed = bool(row.get("closed") or not row.get("url"))
        row.update(liveness_status="closed" if closed else "live",
                   liveness_checked_at=dt.datetime.now(dt.timezone.utc),
                   liveness_evidence="Posting is unavailable" if closed else "Demo posting is available")
        if closed:
            row["status"] = "archived"
        return {"status": row["liveness_status"], "evidence": row["liveness_evidence"]}

    def _public_session(self, session: dict) -> dict:
        end = session["ended_at"] or dt.datetime.now(dt.timezone.utc)
        return {**session, "keys": list(session["keys"]), "applied": list(session["applied"]),
                "skipped": [dict(item) for item in session["skipped"]],
                "elapsed_seconds": max(0, int((end - session["started_at"]).total_seconds()))}

    def start_apply_session(self, keys: list[str]) -> dict:
        session = {"id": len(self._apply_sessions) + 1, "keys": list(keys), "applied": [], "skipped": [],
                   "started_at": dt.datetime.now(dt.timezone.utc), "ended_at": None, "revision": 0}
        self._apply_sessions.append(session)
        return self._public_session(session)

    def apply_session(self) -> dict | None:
        return self._public_session(self._apply_sessions[-1]) if self._apply_sessions else None

    def update_apply_session(self, session_id: int, changes: dict, expected: int) -> dict | None:
        session = next((item for item in self._apply_sessions if item["id"] == session_id), None)
        if session is None:
            return None
        if session["revision"] != expected:
            raise database.RecordConflict(self._public_session(session))
        database.check_session_changes(session["keys"], changes)
        now = dt.datetime.now(dt.timezone.utc)
        if changes.get("ended") is True and session["ended_at"] is None:
            session["ended_at"] = now
        elif changes.get("ended") is False and session["ended_at"] is not None:
            session["started_at"] += now - session["ended_at"]
            session["ended_at"] = None
        for name in ("applied", "skipped"):
            if name in changes:
                session[name] = changes[name]
        session["revision"] += 1
        return self._public_session(session)

    def mark_applied(self, key: str, document_id) -> dict | None:
        document = next(
            (item for item in self._profile.get("documents", []) if str(item.get("id")) == str(document_id)),
            None,
        ) if document_id not in {None, ""} else None
        if document_id not in {None, ""} and document is None:
            raise ValueError("resume version not found")
        for row in self.rows:
            if row["dedupe_key"] != key:
                continue
            now = dt.datetime.now(dt.timezone.utc)
            if row["status"] != "applied":
                row["board_position"] = None
            self._log_status(key, row["status"], "applied")
            row.update(
                status="applied", application_updated_at=now, applied_at=now,
                queue_position=None, resume_document_id=document_id,
                resume_name=document.get("name") if document else None,
            )
            self._upsert_demo_followup(row)
            return {
                "dedupe_key": key, "captured_at": now, "company": row["company"],
                "title": row["title"], "location": row.get("location", ""),
                "terms": row.get("terms", ""), "url": row.get("url", ""),
                "sources": row.get("sources", ""), "deadline": row.get("deadline"),
                "description_text": self.description(key)["description_text"],
                "description_sections": self.description(key)["sections"],
                "resume_document_id": document_id,
                "resume_name": document.get("name") if document else None,
            }
        return None

    def documents(self) -> list[dict]:
        return self.profile()["documents"]

    def tracker(self) -> dict:
        applications = [
            job for job in self.jobs(include_closed=True)
            if job["status"] in {"applying", "applied", "interviewing", "offer", "rejected"}
        ]
        visible = {row["dedupe_key"] for row in self.rows if not self._is_hidden(row)}
        return {
            "applications": applications,
            "reminders": copy.deepcopy(sorted((
                self._named(reminder) for reminder in self._reminders
                if reminder["status"] != "done" and reminder["dedupe_key"] in visible
            ), key=lambda item: (item["snoozed_until"] or item["due_at"], item["id"]))),
            "done_reminders": copy.deepcopy(sorted((
                self._named(reminder) for reminder in self._reminders
                if reminder["status"] == "done" and reminder["dedupe_key"] in visible
                and reminder["completed_at"] > dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)
            ), key=lambda item: item["completed_at"], reverse=True)[:20]),
            "interviews": copy.deepcopy(sorted(
                (self._named(item) for item in self._interviews
                 if item["dedupe_key"] in visible and not item.get("deleted")),
                key=lambda item: item["starts_at"],
            )),
            "history": [
                {name: value for name, value in entry.items() if name != "dismissed_at"} for entry in self._history
                if entry["dismissed_at"] is None and entry["dedupe_key"] in visible
            ],
        }

    def _named(self, item: dict) -> dict:
        row = next(row for row in self.rows if row["dedupe_key"] == item["dedupe_key"])
        named = self._with_override(row)
        public = {key: value for key, value in item.items() if key != "deleted"}
        return {**public, "company": named["company"], "title": named["title"]}

    def _live_interview(self, interview_id: int, *, deleted: bool = False) -> dict | None:
        return next((item for item in self._interviews
                     if item["id"] == interview_id and bool(item.get("deleted")) == deleted), None)

    def interview_key(self, interview_id: int) -> str | None:
        return next((item["dedupe_key"] for item in self._interviews if item["id"] == interview_id), None)

    def update_interview(self, interview_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _interview_changes(payload)
        interview = self._live_interview(interview_id)
        if interview is None:
            return None
        previous = self._named(interview)
        if expected is not None and interview["revision"] != expected:
            raise database.RecordConflict(previous)
        cancelled = changes.pop("cancelled", None)
        merged = {**interview, **changes}
        if merged["ends_at"] is not None and merged["ends_at"] <= merged["starts_at"]:
            raise ValueError("interview end must be after its start")
        if cancelled is not None:
            merged["cancelled_at"] = (interview.get("cancelled_at") or dt.datetime.now(dt.timezone.utc)) if cancelled else None
        interview.update(merged, revision=interview["revision"] + 1)
        return {"interview": self._named(interview), "previous": previous}

    def delete_interview(self, interview_id: int) -> bool:
        interview = self._live_interview(interview_id)
        if interview is not None:
            interview["deleted"] = True
        return interview is not None

    def restore_interview(self, interview_id: int) -> dict | None:
        interview = self._live_interview(interview_id, deleted=True)
        if interview is None:
            return None
        interview["deleted"] = False
        return self._named(interview)

    def update_reminder(self, reminder_id: int, payload: dict) -> dict | None:
        action, until, expected = _reminder_change(payload)
        reminder = next((item for item in self._reminders if item["id"] == reminder_id), None)
        if reminder is None:
            return None
        previous = self._named(reminder)
        if expected is not None and reminder["revision"] != expected:
            raise database.RecordConflict(previous)
        if action not in {"done", "reopen"} and reminder["status"] == "done":
            raise ValueError("Reopen this follow-up before snoozing it")
        now = dt.datetime.now(dt.timezone.utc)
        if action == "done":
            reminder.update(status="done", completed_at=now)
        elif action == "reopen":
            snoozed = reminder["snoozed_until"] is not None and reminder["snoozed_until"] > now
            reminder.update(status="snoozed" if snoozed else "pending", completed_at=None)
        elif action == "snooze":
            reminder.update(status="snoozed", snoozed_until=until or now + dt.timedelta(days=3), completed_at=None)
        else:
            reminder.update(status="pending", snoozed_until=None, completed_at=None)
        reminder["revision"] += 1
        return {"reminder": self._named(reminder), "previous": previous}

    def reminder_key(self, reminder_id: int) -> str | None:
        return next((item["dedupe_key"] for item in self._reminders if item["id"] == reminder_id), None)

    def add_reminder(self, payload: dict) -> dict | None:
        key, due_at, note = _new_reminder(payload)
        if not any(row["dedupe_key"] == key and not self._is_hidden(row) for row in self.rows):
            return None
        reminder = {
            "id": self._next_reminder_id, "dedupe_key": key, "kind": "manual", "due_at": due_at,
            "status": "pending", "snoozed_until": None, "completed_at": None, "note": note, "revision": 0,
        }
        self._next_reminder_id += 1
        self._reminders.append(reminder)
        return self._named(reminder)

    def delete_reminder(self, reminder_id: int) -> bool:
        before = len(self._reminders)
        self._reminders = [item for item in self._reminders if not (item["id"] == reminder_id and item["kind"] == "manual")]
        return len(self._reminders) != before

    def add_interview(self, payload: dict) -> dict:
        starts_at = _parse_client_datetime(payload.get("starts_at"), "interview start")
        ends_at = _parse_client_datetime(payload.get("ends_at"), "interview end", optional=True)
        if ends_at is not None and ends_at <= starts_at:
            raise ValueError("interview end must be after its start")
        row = next(
            (item for item in self.rows if item["dedupe_key"] == payload.get("dedupe_key")),
            None,
        )
        if row is None:
            raise ValueError("job not found")
        interview = {
            "id": self._next_interview_id, "dedupe_key": row["dedupe_key"],
            "starts_at": starts_at, "ends_at": ends_at, "cancelled_at": None, "revision": 0,
            "location": str(payload.get("location") or "")[:500],
            "notes": str(payload.get("notes") or "")[:20_000],
            "company": row["company"], "title": row["title"],
        }
        self._next_interview_id += 1
        self._interviews.append(interview)
        if row["status"] not in {"offer", "rejected"}:
            row["status"] = "interviewing"
        row["next_step"] = f"Interview {starts_at.isoformat()}"
        row["application_updated_at"] = dt.datetime.now(dt.timezone.utc)
        self._complete_demo_followup(row["dedupe_key"])
        return copy.deepcopy(interview)

    def save_next_step(self, key: str, value: str, expected: str | None = None, reopen: list[dict] | None = None) -> dict | None:
        if len(value) > 1_000:
            raise ValueError("next step is too long")
        row = next((item for item in self.rows if item["dedupe_key"] == key), None)
        if row is None:
            return None
        if expected is not None and (row.get("next_step") or "") != expected:
            raise database.ValueConflict(row.get("next_step") or "")
        row["next_step"] = value.strip()
        row["application_updated_at"] = dt.datetime.now(dt.timezone.utc)
        closed = self._complete_demo_followup(key) if reopen is None else []
        reopened = []
        now = dt.datetime.now(dt.timezone.utc)
        for item in reopen or []:
            reminder = next((entry for entry in self._reminders if entry["id"] == item["id"] and entry["dedupe_key"] == key), None)
            if reminder is None or reminder["revision"] != item["revision"] or reminder["status"] != "done":
                continue
            snoozed = reminder.get("snoozed_until") is not None and reminder["snoozed_until"] > now
            reminder.update(status="snoozed" if snoozed else "pending", completed_at=None, revision=reminder["revision"] + 1)
            reopened.append(reminder["id"])
        return {"next_step": row["next_step"], "closed": closed, "reopened": reopened}

    def _company_counts(self) -> dict[str, dict]:
        counts: dict[str, dict] = {}
        for contact in self._contacts:
            if not contact.get("deleted"):
                entry = counts.setdefault(contact["company_key"], {})
                entry["contacts"] = entry.get("contacts", 0) + 1
        for key, note in self._company_notes.items():
            if note["body"].strip():
                counts.setdefault(key, {})["note"] = True
        for link in self._links:
            if not link.get("deleted"):
                entry = counts.setdefault(link["company_key"], {})
                entry["links"] = entry.get("links", 0) + 1
        return counts

    def companies(self, name: str | None = None, *, include_account: bool = False) -> dict:
        groups = _company_groups(self.jobs(include_closed=True), self._companies, self._company_counts())
        if not name:
            return {"companies": groups}
        key = database.resolve_company(self._companies, name)
        company = next((item for item in groups if item["key"] == key), None)
        if company is None:
            return {"company": None}
        contacts = [self._public_contact(item) for item in self._contacts
                    if item["company_key"] == key and not item.get("deleted")]
        note = self._company_notes.get(key, {"body": "", "updated_at": None})
        account = self._company_accounts.get(key) if include_account else None
        return {"company": {
            **company,
            "contacts": copy.deepcopy(contacts),
            "note": copy.deepcopy(note),
            "saved_links": [self._public_link(link) for link in self._links if link["company_key"] == key and not link.get("deleted")],
            "account": copy.deepcopy(account),
            "account_protected": not include_account,
        }}

    def save_company_note(self, name: str, body: str, expected: str | None = None) -> dict:
        if len(body) > 20_000:
            raise ValueError("company note is too long")
        if not database.company_key(name):
            raise ValueError("company name is required")
        key = database.resolve_company(self._company_registry(), name)
        if key is None:
            raise ValueError("company not found")
        current = (self._company_notes.get(key) or {"body": ""})["body"]
        if expected is not None and current != expected:
            raise database.ValueConflict(current)
        note = {"body": body, "updated_at": dt.datetime.now(dt.timezone.utc)}
        self._company_notes[key] = note
        return copy.deepcopy(note)

    def _taken_name(self, name: str, key: str | None = None) -> None:
        other = database.resolve_company(self._company_registry(), name)
        if other is not None and other != key:
            raise ValueError(f"{database.company_name(self._companies[other])} is already a company. Merge them instead.")

    def create_company(self, payload: dict) -> dict:
        name = _company_name(payload)
        existing = database.resolve_company(self._company_registry(), name)
        if existing is not None:
            row = self._companies[existing]
            if row["hidden_at"] is not None:
                raise ValueError(f"{database.company_name(row)} is hidden. Show hidden companies to restore it.")
            row["manual"] = True
            return {"company": self._public_company(existing), "created": False}
        key = database.company_key(name)
        self._companies[key] = {
            "key": key, "name": name, "display_name": name, "manual": True,
            "hidden_at": None, "revision": 0, "merged_into": None,
        }
        return {"company": self._public_company(key), "created": True}

    def _canonical(self, key: str) -> dict | None:
        row = self._company_registry().get(key)
        return row if row and not row["merged_into"] else None

    def rename_company(self, key: str, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        name = _company_name(payload)
        row = self._canonical(key)
        if row is None:
            return None
        previous = self._public_company(key)
        if expected is not None and row["revision"] != expected:
            raise database.RecordConflict(previous)
        self._taken_name(name, key)
        row["display_name"] = None if name == row["name"] and not row["manual"] else name
        row["revision"] += 1
        return {"company": self._public_company(key), "previous": previous}

    def hide_company(self, key: str, hidden: bool) -> dict | None:
        row = self._canonical(key)
        if row is None or (row["hidden_at"] is None) != hidden:
            return None
        row["hidden_at"] = dt.datetime.now(dt.timezone.utc) if hidden else None
        return self._public_company(key)

    def merge_company(self, key: str, payload: dict) -> dict | None:
        target = _merge_target(payload)
        if key == target:
            raise ValueError("Pick a different company to merge into.")
        source_row, target_row = self._canonical(key), self._canonical(target)
        if source_row is None or target_row is None:
            return None
        hidden = next((row for row in (source_row, target_row) if row["hidden_at"] is not None), None)
        if hidden:
            raise ValueError(f"{database.company_name(hidden)} is hidden. Show it in Companies before merging.")
        source_before = self._public_company(key)
        source_note, target_note = self._company_notes.get(key), self._company_notes.get(target)
        before = {"target_note": copy.deepcopy(target_note), "source_note": copy.deepcopy(source_note)}
        if source_note and source_note["body"].strip():
            kept = (target_note or {}).get("body", "").rstrip()
            source_name = database.company_name(source_row)
            appended = f"From {source_name}:\n{source_note['body'].strip()}"
            body = f"{kept}\n\n{appended}" if kept else appended
            self._company_notes[target] = {"body": body, "updated_at": dt.datetime.now(dt.timezone.utc)}
        self._company_notes.pop(key, None)
        moved = [contact for contact in self._contacts if contact["company_key"] == key]
        for contact in moved:
            contact.update(company_key=target, company_name=database.company_name(target_row))
        links = self._move_links(key, target)
        source_row["merged_into"] = target
        source_row["revision"] += 1
        target_row["revision"] += 1
        merge = {
            "id": len(self._merges) + 1, "source": key, "target": target, "revision": target_row["revision"],
            "note_at": (self._company_notes.get(target) or {}).get("updated_at"),
            "before": {**before, "contacts": [contact["id"] for contact in moved], "links": links}, "undone": False,
        }
        self._merges.append(merge)
        return {"merge_id": merge["id"], "company": self._public_company(target), "source": source_before,
                "moved": {"contacts": len(moved), "links": len(links)}}

    def _move_links(self, source: str, target: str, ids: list[int] | None = None) -> list[int]:
        moved = [link for link in self._links if link["company_key"] == source and (ids is None or link["id"] in ids)]
        for link in moved:
            link["company_key"] = target
        return [link["id"] for link in moved]

    @staticmethod
    def _public_link(link: dict) -> dict:
        return copy.deepcopy({key: value for key, value in link.items() if key not in {"deleted", "company_key"}})

    def _live_link(self, link_id: int, *, deleted: bool = False) -> dict | None:
        return next((item for item in self._links if item["id"] == link_id and bool(item.get("deleted")) == deleted), None)

    def add_company_link(self, key: str, payload: dict) -> dict | None:
        fields = _link_fields(payload)
        if self._canonical(key) is None:
            return None
        now = dt.datetime.now(dt.timezone.utc)
        link = {"id": len(self._links) + 1, "company_key": key, **fields, "revision": 0, "created_at": now, "updated_at": now}
        self._links.append(link)
        return self._public_link(link)

    def update_link(self, link_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _link_fields(payload, partial=True)
        link = self._live_link(link_id)
        if link is None:
            return None
        previous = self._public_link(link)
        if expected is not None and link["revision"] != expected:
            raise database.RecordConflict(previous)
        link.update(changes, revision=link["revision"] + 1, updated_at=dt.datetime.now(dt.timezone.utc))
        return {"link": self._public_link(link), "previous": previous}

    def delete_link(self, link_id: int) -> bool:
        link = self._live_link(link_id)
        if link is not None:
            link["deleted"] = True
        return link is not None

    def restore_link(self, link_id: int) -> dict | None:
        link = self._live_link(link_id, deleted=True)
        if link is None:
            return None
        link["deleted"] = False
        return self._public_link(link)

    def merge_target(self, merge_id: int) -> str | None:
        return next((merge["target"] for merge in self._merges if merge["id"] == merge_id), None)

    def undo_company_merge(self, merge_id: int, payload: dict) -> dict | None:
        expected = _expected_revision(dict(payload))
        merge = next((item for item in self._merges if item["id"] == merge_id), None)
        if merge is None:
            return None
        source, target = self._companies[merge["source"]], self._companies[merge["target"]]
        current = merge["revision"] if expected is None else expected
        if merge["undone"] or source["merged_into"] != merge["target"] or target["revision"] != current:
            raise database.EditConflict("This company changed after the merge, so it can't be undone.")
        if (self._company_notes.get(merge["target"]) or {}).get("updated_at") != merge["note_at"]:
            raise database.EditConflict("Its notes changed after the merge, so it can't be undone.")
        before = merge["before"]
        if before["target_note"] is None:
            self._company_notes.pop(merge["target"], None)
        else:
            self._company_notes[merge["target"]] = copy.deepcopy(before["target_note"])
        if before["source_note"] is not None:
            self._company_notes[merge["source"]] = copy.deepcopy(before["source_note"])
        for contact in self._contacts:
            if contact["id"] in before["contacts"] and contact["company_key"] == merge["target"]:
                contact.update(company_key=merge["source"], company_name=database.company_name(source))
        self._move_links(merge["target"], merge["source"], before["links"])
        source["merged_into"] = None
        source["revision"] += 1
        target["revision"] += 1
        merge["undone"] = True
        return {"company": self._public_company(merge["source"]), "target": self._public_company(merge["target"])}

    def save_contact(self, payload: dict, source: str = "manual") -> dict:
        company = str(payload.get("company") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not company or not name or source not in {"manual", "linkedin_csv"}:
            raise ValueError("company and contact name are required")
        linkedin_url = str(payload.get("linkedin_url") or "").strip()
        if linkedin_url and not linkedin_url.startswith("https://"):
            raise ValueError("LinkedIn URL must use HTTPS")
        key = database.resolve_company(self._company_registry(), company)
        if key is None:
            raise ValueError("company not found")
        company = database.company_name(self._companies[key])
        if source == "linkedin_csv":
            existing = next((item for item in self._contacts if
                item["company_key"] == key and item["name"].casefold() == name.casefold()
                and item["linkedin_url"] == linkedin_url), None)
            if existing:
                existing.update(title=str(payload.get("title") or ""), deleted=False, revision=existing["revision"] + 1,
                                updated_at=dt.datetime.now(dt.timezone.utc))
                return self._public_contact(existing)
        contact = {
            "id": self._next_contact_id, "company_key": key, "company_name": company,
            "name": name, "title": str(payload.get("title") or "")[:500],
            "email": str(payload.get("email") or "")[:500],
            "linkedin_url": linkedin_url[:2_000], "source": source, "revision": 0,
            "created_at": dt.datetime.now(dt.timezone.utc),
            "updated_at": dt.datetime.now(dt.timezone.utc),
        }
        self._next_contact_id += 1
        self._contacts.append(contact)
        return copy.deepcopy(contact)

    @staticmethod
    def _public_contact(contact: dict) -> dict:
        return copy.deepcopy({key: value for key, value in contact.items() if key != "deleted"})

    def _live_contact(self, contact_id: int, *, deleted: bool = False) -> dict | None:
        return next((item for item in self._contacts
                     if item["id"] == contact_id and bool(item.get("deleted")) == deleted), None)

    def update_contact(self, contact_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        changes = _contact_changes(payload)
        contact = self._live_contact(contact_id)
        if contact is None:
            return None
        previous = self._public_contact(contact)
        if expected is not None and contact["revision"] != expected:
            raise database.RecordConflict(previous)
        contact.update(changes, revision=contact["revision"] + 1, updated_at=dt.datetime.now(dt.timezone.utc))
        return {"contact": self._public_contact(contact), "previous": previous}

    def delete_contact(self, contact_id: int) -> bool:
        contact = self._live_contact(contact_id)
        if contact is not None:
            contact["deleted"] = True
        return contact is not None

    def restore_contact(self, contact_id: int) -> dict | None:
        contact = self._live_contact(contact_id, deleted=True)
        if contact is None:
            return None
        contact["deleted"] = False
        return self._public_contact(contact)

    def import_connections(self, contacts: list[dict]) -> dict:
        return _import_connections(self, contacts)

    def _public_view(self, view: dict) -> dict:
        return copy.deepcopy({key: value for key, value in view.items() if key != "deleted"})

    def _view(self, view_id: int, *, deleted: bool = False) -> dict | None:
        return next((view for view in self._saved_views if view["id"] == view_id and view["deleted"] == deleted), None)

    def _live_view_named(self, name: str, other: int | None = None) -> dict | None:
        return next((view for view in self._saved_views
                     if view["name"] == name and not view["deleted"] and view["id"] != other), None)

    def saved_views(self) -> dict:
        live = sorted((view for view in self._saved_views if not view["deleted"]), key=lambda view: (view["position"], view["id"]))
        return {"saved_views": [self._public_view(view) for view in live], "order_revision": self._view_order_revision}

    def create_view(self, payload: dict) -> dict:
        clean = {"filters": {}, "sort": "score", **database.normalize_view(payload)}
        if "name" not in clean:
            raise ValueError("view name must be between 1 and 40 characters")
        taken = self._live_view_named(clean["name"])
        if taken is not None:
            raise database.ViewNameTaken(self._public_view(taken))
        now = dt.datetime.now(dt.timezone.utc)
        view = {
            "id": self._next_view_id, "name": clean["name"], "filters": clean["filters"], "sort": clean["sort"],
            "pinned": True, "position": max((item["position"] for item in self._saved_views), default=-1) + 1,
            "revision": 0, "created_at": now, "updated_at": now, "deleted": False,
        }
        self._next_view_id += 1
        self._saved_views.append(view)
        return self._public_view(view)

    def update_view(self, view_id: int, payload: dict) -> dict | None:
        payload = dict(payload)
        expected = _expected_revision(payload)
        clean = database.normalize_view(_view_changes(payload))
        if not clean:
            raise ValueError("nothing to change")
        view = self._view(view_id)
        if view is None:
            return None
        previous = self._public_view(view)
        if expected is not None and view["revision"] != expected:
            raise database.RecordConflict(previous)
        if "name" in clean and self._live_view_named(clean["name"], view_id) is not None:
            raise ValueError(f"A view named “{clean['name']}” already exists.")
        view.update(clean, revision=view["revision"] + 1, updated_at=dt.datetime.now(dt.timezone.utc))
        return {"view": self._public_view(view), "previous": previous}

    def delete_view(self, view_id: int) -> bool:
        view = self._view(view_id)
        if view is not None:
            view["deleted"] = True
        return view is not None

    def restore_view(self, view_id: int) -> dict | None:
        view = self._view(view_id, deleted=True)
        if view is None:
            return None
        if self._live_view_named(view["name"]) is not None:
            raise ValueError(f"Another view is now named “{view['name']}”, so this one can't come back.")
        view["deleted"] = False
        return self._public_view(view)

    def reorder_views(self, view_ids: list[int], expected: int) -> dict:
        if len(view_ids) != len(set(view_ids)) or len(view_ids) > 500:
            raise ValueError("view order contains duplicates or too many views")
        live = {view["id"] for view in self._saved_views if not view["deleted"]}
        if expected != self._view_order_revision or live != set(view_ids):
            raise database.EditConflict("The saved views changed in another tab. Reload and retry.")
        for position, view_id in enumerate(view_ids):
            self._view(view_id)["position"] = position
        self._view_order_revision += 1
        return {"ids": view_ids, "order_revision": self._view_order_revision}

    def profile(self, dedupe_key: str | None = None, company: str | None = None) -> dict:
        profile = copy.deepcopy(self._profile)
        profile["revision"] = self._profile_revision
        for document in profile.get("documents", []):
            document.setdefault("default", False)
            document["application_count"] = sum(str(row.get("resume_document_id")) == str(document.get("id")) for row in self.rows)
        profile["copied_fields"] = sorted(self._copied_fields.get(dedupe_key or "", set()))
        profile["answer_overrides"] = copy.deepcopy(self._answer_overrides.get(dedupe_key or "", {}))
        profile["company_account"] = copy.deepcopy(
            self._company_accounts.get(database.resolve_company(self._company_registry(), company or "") or database.company_key(company or ""))
        )
        return profile

    def save_profile(self, payload: dict, revision: int | None = None, merge_documents: bool = False) -> dict:
        if revision is not None and revision != self._profile_revision:
            raise database.ProfileConflict("profile is at revision %d, not %d" % (self._profile_revision, revision))
        clean = database.normalize_profile_data(payload)
        if merge_documents:
            clean["documents"] = database.merge_imported_documents(self._profile.get("documents", []), clean["documents"])
        # mirrors the id-preserving postgres write
        for name, prefix in (("documents", "document"), ("answer_templates", "template")):
            known = {str(row.get("id")) for row in self._profile.get(name, [])}
            taken = set()
            for row in clean[name]:
                if row["id"] is None or str(row["id"]) not in known or str(row["id"]) in taken:
                    row["id"] = None
                    while row["id"] is None or row["id"] in known or row["id"] in taken:
                        self._next_profile_id += 1
                        row["id"] = "%s-%d" % (prefix, self._next_profile_id)
                taken.add(str(row["id"]))
        removed = {str(row.get("id")) for row in self._profile.get("documents", [])} - {str(row["id"]) for row in clean["documents"]}
        for row in self.rows:
            if str(row.get("resume_document_id")) in removed:
                row["resume_document_id"] = None  # same as the foreign key
        names = {str(row.get("id")): row["name"] for row in self._profile.get("answer_templates", [])}
        renames = {names[str(row["id"])]: row["name"] for row in clean["answer_templates"] if names.get(str(row["id"]), row["name"]) != row["name"]}
        for overrides in self._answer_overrides.values():
            moved = {renames[name]: overrides.pop(name) for name in list(overrides) if name in renames}
            overrides.update(moved)
        self._profile = clean
        self._profile_revision += 1
        return self.profile()

    def mark_copy(self, dedupe_key: str, target_key: str) -> None:
        if not dedupe_key or len(dedupe_key) > 500 or not target_key or len(target_key) > 200:
            raise ValueError("invalid job or copy target")
        self._copied_fields.setdefault(dedupe_key, set()).add(target_key)

    def save_profile_field(self, payload: dict) -> dict | None:
        clean = database.normalize_quick_fill_field(payload.get("field"))
        dedupe_key, company = database.clean_context_keys(str(payload.get("dedupe_key", "")), str(payload.get("company", "")))
        name = clean["name"]
        if clean["kind"] == "override":
            if not any(row["dedupe_key"] == dedupe_key for row in self.rows):
                return None
            record = self._answer_overrides.setdefault(dedupe_key, {})
            current = record.get(name, "")
        else:
            key = database.resolve_company(self._company_registry(), company) or database.company_key(company)
            record = self._company_accounts.setdefault(key, {
                "company_name": company, "account_exists": None, "sign_in_email": "", "password_manager_url": "",
            })
            current = record.get(name)
        if "expected" in clean and current != clean["expected"]:
            raise database.ValueConflict(current)
        if clean["kind"] == "override" and not clean["value"]:
            record.pop(name, None)
        else:
            record[name] = clean["value"]
        return {"profile": self.profile(dedupe_key, company), "value": clean["value"]}

    def eligibility(self, key: str) -> dict | None:
        description = self.description(key)
        if description is None:
            return None
        overrides = [item for item in self._eligibility_overrides.get(key, []) if not item.get("revoked")]
        return _with_overrides(analyze_eligibility(
            text=str(description.get("description_text") or ""),
            sections=description.get("sections") or [],
            profile=self._profile,
            overrides=overrides,
        ), overrides)

    def _flip_overrides(self, key: str, ids: list[int] | None, revoked: bool) -> dict | None:
        changed = []
        hidden = any(row["dedupe_key"] == key and self._is_hidden(row) for row in self.rows)
        for item in [] if hidden else self._eligibility_overrides.get(key, []):
            if bool(item.get("revoked")) != revoked and (ids is None or item["id"] in ids):
                item["revoked"] = revoked
                changed.append(item["id"])
        result = self.eligibility(key)
        return result and {"eligibility": result, "ids": sorted(changed)}

    def revoke_eligibility(self, key: str, ids: list[int] | None = None) -> dict | None:
        return self._flip_overrides(key, ids, True)

    def restore_eligibility(self, key: str, ids: list[int]) -> dict | None:
        return self._flip_overrides(key, ids, False)

    def override_eligibility(self, key: str, note: str = "") -> dict | None:
        if len(note) > 1_000:
            raise ValueError("override note is too long")
        result = self.eligibility(key)
        if result is None:
            return None
        blockers = [item for item in result["blockers"] if not item.get("overridden")]
        if not blockers:
            raise ValueError("there are no active blockers to override")
        now = dt.datetime.now(dt.timezone.utc)
        stored = self._eligibility_overrides.setdefault(key, [])
        ids = list(range(self._next_override_id, self._next_override_id + len(blockers)))
        self._next_override_id += len(blockers)
        stored.extend({
            "id": override_id, "blocker_key": item["key"], "evidence": item["evidence"],
            "comparison": item["comparison"], "note": note.strip(), "created_at": now,
        } for override_id, item in zip(ids, blockers))
        return {"eligibility": self.eligibility(key), "override_ids": ids}

    def healthy(self) -> bool:
        return True

    def description(self, key: str, *, force: bool = False) -> dict | None:
        if not any(row["dedupe_key"] == key for row in self.rows):
            return None
        if key in self._descriptions:
            return copy.deepcopy(self._descriptions[key])
        return {
            "description_text": "Build reliable systems with a thoughtful engineering team.",
            "sections": [
                {"key": "about", "text": "Build reliable systems with a thoughtful engineering team."},
                {"key": "requirements", "text": "Linux\nPython\nClear technical communication"},
            ],
            "description_fetched_at": dt.datetime.now(dt.timezone.utc),
            "description_error": None,
            "deadline": None,
            "deadline_source": None,
        }

    def prefetch_description(self, key: str) -> None:
        del key

    def save_manual_description(self, key: str, text: str) -> dict | None:
        if not any(row["dedupe_key"] == key for row in self.rows):
            return None
        self._descriptions[key] = self._demo_detail(parse_description(text))
        return self.description(key)

    @staticmethod
    def _demo_detail(detail) -> dict:
        return {
            "description_text": detail.text, "sections": list(detail.sections),
            "description_fetched_at": dt.datetime.now(dt.timezone.utc),
            "description_error": None, "deadline": detail.deadline,
            "deadline_source": detail.deadline_source,
        }

    def edit_posting(self, key: str, payload: dict) -> dict | None:
        fields, description = _posting_edit(payload)
        row = next((item for item in self.rows if item["dedupe_key"] == key), None)
        if row is None:
            return None
        override = self._overrides.get(key)
        current = self._with_override(row)
        changed = {name: value for name, value in fields.items() if value != current[name]}
        described = description is not None and description.text != self.description(key)["description_text"]
        if not changed and not described:
            return {"edit_id": None, **self._edited(key)}
        self._posting_edits.append({
            "id": len(self._posting_edits) + 1, "dedupe_key": key, "undone": False,
            "changed": bool(changed), "override": copy.deepcopy(override),
            "described": described, "description": copy.deepcopy(self._descriptions.get(key)),
        })
        if changed:
            self._overrides[key] = {
                **{name: (override or {}).get(name) for name in database.POSTING_EDIT_FIELDS}, **changed,
            }
        if described:
            self._descriptions[key] = self._demo_detail(description)
        return {"edit_id": self._posting_edits[-1]["id"], **self._edited(key)}

    def posting_edit_key(self, edit_id: int) -> str | None:
        return next((edit["dedupe_key"] for edit in self._posting_edits if edit["id"] == edit_id), None)

    def undo_posting_edit(self, edit_id: int) -> dict | None:
        edit = next((item for item in self._posting_edits if item["id"] == edit_id), None)
        if edit is None:
            return None
        latest = max(item["id"] for item in self._posting_edits
                     if item["dedupe_key"] == edit["dedupe_key"] and not item["undone"]) if not edit["undone"] else None
        if latest != edit_id:
            raise database.EditConflict("This posting changed after that edit, so it can't be undone.")
        for flag, store, previous in (
            ("changed", self._overrides, edit["override"]),
            ("described", self._descriptions, edit["description"]),
        ):
            if not edit[flag]:
                continue
            if previous is None:
                store.pop(edit["dedupe_key"], None)
            else:
                store[edit["dedupe_key"]] = previous
        edit["undone"] = True
        return self._edited(edit["dedupe_key"])

    def _edited(self, key: str) -> dict:
        job = next((item for item in self.jobs(include_closed=True) if item["dedupe_key"] == key), None)
        return {"job": job, "description": self.description(key)}

    def close(self) -> None:
        pass


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "jobseer-dashboard"

    @property
    def app(self):
        return self.server  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; connect-src 'self'; "
            "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "img-src 'self' data:; object-src 'none'; script-src 'self'; "
            "style-src 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, default=_json_value, separators=(",", ":")).encode()
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _text(self, body: str, content_type: str, *, filename: str | None = None) -> None:
        encoded = body.encode()
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _image(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_user(self) -> str | None:
        if not self.app.require_auth:
            return "demo"
        # Authentik 2026.5's proxy provider does not expose a signed identity
        # JWT. Its documented contract is a set of X-authentik-* headers, so
        # accept those only when the request also carries the provider metadata
        # injected by the expected outpost/application. Kubernetes networking
        # separately prevents any pod except that outpost from reaching us.
        username = self.headers.get("X-authentik-username", "")
        application = self.headers.get("X-authentik-meta-app", "")
        outpost = self.headers.get("X-authentik-meta-outpost", "")
        if not username or application != self.app.authentik_app or not outpost:
            return None
        if self.app.authentik_user and not hmac.compare_digest(username, self.app.authentik_user):
            return None
        return username

    def _require_api_auth(self) -> str | None:
        user = self._auth_user()
        if user is None:
            self._error(
                HTTPStatus.UNAUTHORIZED,
                "Authentik session missing. Open JobSeer through its protected URL.",
            )
        return user

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urllib.parse.urlsplit(origin).netloc == self.headers.get("Host", "")

    def _valid_csrf(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(CSRF_COOKIE)
        header = self.headers.get("X-CSRF-Token", "")
        return bool(
            morsel
            and header
            and hmac.compare_digest(morsel.value, header)
            and hmac.compare_digest(header, self.app.csrf_token)
        )

    def _require_write_security(self) -> bool:
        if self._require_api_auth() is None:
            return False
        if not self._same_origin() or not self._valid_csrf():
            self._error(HTTPStatus.FORBIDDEN, "Security token missing or expired. Reload and retry.")
            return False
        return True

    # stale tabs must not edit
    def _reject_hidden(self, *keys: str) -> bool:
        keys = [key for key in keys if key]
        try:
            hidden = bool(keys) and bool(self.app.store.hidden_keys(keys))
        except Exception:
            log.exception("could not check hidden jobs")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the change. Retry shortly.")
            return True
        if hidden:
            self._error(HTTPStatus.GONE, HIDDEN_WRITE)
        return hidden

    def _overview_write(self, key: str, action: str) -> None:
        if not self._require_write_security():
            return
        if self.app.overviews is None:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if self._reject_hidden(key):
            return
        store = self.app.store
        stored = isinstance(store, PostgresStore)
        try:
            if action in {"dismiss", "restore"}:
                row = store.dismiss_overview(key, action == "dismiss")
            elif action == "regenerate/undo":
                expected = _expected_revision(self._read_json())
                if expected is None:
                    raise ValueError("expected_revision is required")
                # only saved overviews have a past
                row = store.undo_regenerate_overview(key, expected) if stored else None
            else:
                detail = store.description(key)
                if detail is None:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                sections = detail.get("sections") or []
                getattr(self.app.overviews, "forget", lambda _: None)(sections)
                if stored:
                    row = store.regenerate_overview(key)
                    if row is not None:
                        self.app.trigger_overview()
                        self._json(_overview_payload(row), HTTPStatus.ACCEPTED)
                        return
                else:
                    if store.overview_dismissed(key):
                        store.dismiss_overview(key, False)
                    self._json({"status": "ready", "items": self.app.overviews.overview(sections), "dismissed": False, "revision": None})
                    return
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except database.EditConflict as error:
            self._error(HTTPStatus.CONFLICT, str(error))
            return
        except Exception:
            log.exception("could not change posting overview")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't change the overview. Retry shortly.")
            return
        if row is None:
            self._error(HTTPStatus.NOT_FOUND, "overview not found")
            return
        self._json(_overview_payload(row) if stored else {"dismissed": bool(row.get("dismissed_at"))})

    def _activity_write(self, raw_id: str, dismissed: bool) -> None:
        if not self._require_write_security():
            return
        try:
            entry_id = int(raw_id)
            key = self.app.store.history_key(entry_id)
            if key is None:
                self._error(HTTPStatus.NOT_FOUND, "activity entry not found")
                return
            if self._reject_hidden(key):
                return
            entry = self.app.store.dismiss_history(entry_id, dismissed)
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not change activity entry")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't change the activity entry. Retry shortly.")
            return
        if entry is None:
            self._error(HTTPStatus.CONFLICT, "This activity entry changed in another tab.")
            return
        self._json({"entry": entry})

    def _reminder_write(self, raw_id: str, action: str) -> None:
        if not self._require_write_security():
            return
        store = self.app.store
        try:
            if action == "add":
                payload = self._read_json()
                if self._reject_hidden(str(payload.get("dedupe_key") or "")):
                    return
                reminder = store.add_reminder(payload)
                if reminder is None:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                else:
                    self._json({"reminder": reminder}, HTTPStatus.CREATED)
                return
            reminder_id = int(raw_id)
            key = store.reminder_key(reminder_id)
            if key is None:
                self._error(HTTPStatus.NOT_FOUND, "reminder not found")
                return
            if self._reject_hidden(key):
                return
            if action == "delete":
                result = {"ok": True} if store.delete_reminder(reminder_id) else None
            else:
                result = store.update_reminder(reminder_id, self._read_json())
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except database.RecordConflict as conflict:
            self._json({"error": "This follow-up changed in another tab.", "reminder": conflict.record}, HTTPStatus.CONFLICT)
            return
        except Exception:
            log.exception("could not change reminder")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't update the follow-up. Retry shortly.")
            return
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, "reminder not found")
            return
        self._json(result)

    # revoke and its undo share this
    def _change_override(self, key: str, *, restore: bool) -> None:
        if not self._require_quick_fill_write() or self._reject_hidden(key):
            return
        try:
            sent = self.headers.get("Content-Length", "0") != "0"
            ids = _override_ids(self._read_json() if sent else {}, required=restore)
            store = self.app.store
            result = store.restore_eligibility(key, ids) if restore else store.revoke_eligibility(key, ids)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not change eligibility override")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't change the override. Retry shortly.")
            return
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, "job not found")
        elif not result["ids"]:
            self._json({
                "error": "The override changed in another tab.", "eligibility": result["eligibility"],
            }, HTTPStatus.CONFLICT)
        else:
            self._json(result)

    def _require_quick_fill_write(self) -> bool:
        if self._require_api_auth() is None:
            return False
        if not self.app.quick_fill_enabled:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return False
        if not self._same_origin() or not self._valid_csrf():
            self._error(HTTPStatus.FORBIDDEN, "Security token missing or expired. Reload and retry.")
            return False
        return True

    def _read_json(self, limit: int = MAX_BODY) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid content length") from error
        if length <= 0 or length > limit:
            raise ValueError("invalid request size")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def _static(self, relative: str) -> None:
        path = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in path.parents or not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        body = path.read_bytes()
        content_type = (
            "application/manifest+json" if path.suffix == ".webmanifest"
            else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache" if path.name == "service-worker.js" else "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_app_route(self, path: str) -> bool:
        root = path.strip("/").split("/", 1)[0]
        return path in {"/", "/index.html"} or root in APP_ROUTE_ROOTS

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/v1/"):
            user = self._require_api_auth()
            if user is None:
                return
            if parsed.path == "/api/v1/session":
                secure = self.headers.get("X-Forwarded-Proto") == "https"
                cookie = (
                    f"{CSRF_COOKIE}={self.app.csrf_token}; Path=/; SameSite=Strict; HttpOnly"
                    + ("; Secure" if secure else "")
                )
                self._json(
                    {
                        "user": user,
                        "csrf_token": self.app.csrf_token,
                        "features": {
                            "quick_fill": self.app.quick_fill_enabled,
                            "ai_overview": self.app.overviews is not None,
                        },
                    },
                    headers={"Set-Cookie": cookie},
                )
                return
            if parsed.path == "/api/v1/jobs":
                query = urllib.parse.parse_qs(parsed.query)
                include_closed = query.get("closed", [""])[0].lower() in {"1", "true", "yes"}
                try:
                    jobs = self.app.store.jobs(include_closed=include_closed)
                except Exception:
                    log.exception("could not load dashboard jobs")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't load jobs. Retry shortly.")
                    return
                self._json({"jobs": jobs, "refreshed_at": dt.datetime.now(dt.timezone.utc)})
                return
            if parsed.path == "/api/v1/company-icons":
                try:
                    self._json({"keys": self.app.store.company_icon_keys()})
                except Exception:
                    log.exception("could not load company icons")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't load company icons. Retry shortly.")
                return
            icon_prefix = "/api/v1/company-icons/"
            if parsed.path.startswith(icon_prefix):
                name = urllib.parse.unquote(parsed.path[len(icon_prefix):])
                if not name or len(name) > 500 or "/" in name:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
                    return
                try:
                    icon = self.app.store.company_icon(name)
                except Exception:
                    log.exception("could not load company icon")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't load company icon. Retry shortly.")
                    return
                if icon is None:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
                else:
                    self._image(bytes(icon["logo_data"]), icon["media_type"])
                return
            if parsed.path == "/api/v1/queue/session":
                try:
                    self._json({
                        "session": self.app.store.apply_session(),
                        "documents": self.app.store.documents() if self.app.quick_fill_enabled else [],
                    })
                except Exception:
                    log.exception("could not load apply session")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't load the session. Retry shortly.")
                return
            if parsed.path == "/api/v1/tracker":
                try:
                    self._json(self.app.store.tracker())
                except Exception:
                    log.exception("could not load tracker")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't load the tracker. Retry shortly.")
                return
            if parsed.path == "/api/v1/companies":
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    self._json(self.app.store.companies(
                        query.get("name", [None])[0],
                        include_account=self.app.quick_fill_enabled,
                    ))
                except Exception:
                    log.exception("could not load companies")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't load companies. Retry shortly.")
                return
            if parsed.path == "/api/v1/interviews.ics":
                try:
                    interviews = self.app.store.tracker()["interviews"]
                    query = urllib.parse.parse_qs(parsed.query)
                    interview_id = query.get("id", [None])[0]
                    if interview_id:
                        interviews = [
                            item for item in interviews if str(item["id"]) == str(interview_id)
                        ]
                    self._text(
                        _calendar_text(interviews), "text/calendar; charset=utf-8",
                        filename="jobseer-interviews.ics",
                    )
                except Exception:
                    log.exception("could not export interviews")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't export interviews. Retry shortly.")
                return
            if parsed.path == "/api/v1/saved-views":
                self._json(self.app.store.saved_views())
                return
            if parsed.path == "/api/v1/rules":
                try:
                    self._json(self.app.store.rules_with_order())
                except Exception:
                    log.exception("could not load inbox rules")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't load rules. Retry shortly.")
                return
            if parsed.path == "/api/v1/profile":
                if not self.app.quick_fill_enabled:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
                    return
                query = urllib.parse.parse_qs(parsed.query)
                self._json({
                    "profile": _with_checks(self.app.store.profile(
                        dedupe_key=query.get("job", [None])[0],
                        company=query.get("company", [None])[0],
                    ))
                })
                return
            detail_prefix = "/api/v1/jobs/"
            overview_suffix = "/overview"
            if parsed.path.startswith(detail_prefix) and parsed.path.endswith(overview_suffix):
                if self.app.overviews is None:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
                    return
                key = urllib.parse.unquote(
                    parsed.path[len(detail_prefix):-len(overview_suffix)].rstrip("/")
                )
                try:
                    if isinstance(self.app.store, PostgresStore):
                        if not any(job["dedupe_key"] == key for job in self.app.store.jobs()):
                            self._error(HTTPStatus.NOT_FOUND, "job not found")
                            return
                        self._json(_overview_payload(self.app.store.overview_job(key)))
                        return
                    detail = self.app.store.description(key)
                    if detail is None:
                        self._error(HTTPStatus.NOT_FOUND, "job not found")
                        return
                    if not detail.get("description_text"):
                        self._json({"items": [], "reason": "description unavailable"})
                        return
                    sections = detail.get("sections") or []
                    cached_only = urllib.parse.parse_qs(parsed.query).get("cached") == ["1"]
                    items = self.app.overviews.cached_overview(sections) if cached_only else self.app.overviews.overview(sections)
                    fallback = getattr(self.app.overviews, "is_fallback", lambda _: False)(sections)
                    dismissed = getattr(self.app.store, "overview_dismissed", lambda _: False)(key)
                    self._json({"items": items or [], "source": "posting" if fallback else "ai", "dismissed": dismissed})
                except Exception:
                    log.exception("could not generate posting overview")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "AI overview is unavailable. Retry shortly.")
                return
            eligibility_suffix = "/eligibility"
            if parsed.path.startswith(detail_prefix) and parsed.path.endswith(eligibility_suffix):
                if not self.app.quick_fill_enabled:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
                    return
                key = urllib.parse.unquote(
                    parsed.path[len(detail_prefix):-len(eligibility_suffix)].rstrip("/")
                )
                try:
                    result = self.app.store.eligibility(key)
                except Exception:
                    log.exception("could not evaluate job eligibility")
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't evaluate eligibility. Retry shortly.")
                    return
                if result is None:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                self._json({"eligibility": result})
                return
            detail_suffix = "/description"
            if parsed.path.startswith(detail_prefix) and parsed.path.endswith(detail_suffix):
                key = urllib.parse.unquote(
                    parsed.path[len(detail_prefix):-len(detail_suffix)].rstrip("/")
                )
                if not key:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                force = urllib.parse.parse_qs(parsed.query).get("retry") == ["1"]
                detail = self.app.store.description(key, force=force)
                if detail is None:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                # Raw provider HTML is cached for reparsing, not trusted as UI
                # markup. The browser receives deterministic plain text only.
                detail["pay"] = extract_pay(detail.get("description_text"))
                detail.pop("description_html", None)
                self._json({"description": detail})
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if parsed.path in {"/manifest.webmanifest", "/service-worker.js"}:
            self._static(parsed.path.removeprefix("/"))
            return
        if parsed.path.startswith("/static/"):
            self._static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/healthz":
            ok = self.app.store.healthy()
            self._json({"ok": ok}, HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if self._is_app_route(parsed.path):
            self._static("index.html")
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _edited_json(self, result: dict, **extra) -> None:
        detail = result.get("description")
        if detail:
            detail = {**detail, "pay": extract_pay(detail.get("description_text"))}
            detail.pop("description_html", None)
        self._json({**extra, "job": result["job"], "description": detail})

    def _patch_posting(self, key: str) -> None:
        if not self._require_write_security() or self._reject_hidden(key):
            return
        try:
            result = self.app.store.edit_posting(key, self._read_json(POSTING_EDIT_BODY))
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not edit posting")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the posting. Retry shortly.")
            return
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, "job not found")
            return
        self._edited_json(result, edit_id=result["edit_id"])

    def _record_write(self, kind: str, raw_id: str, action: str) -> None:
        if not self._require_write_security():
            return
        try:
            record_id = int(raw_id)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, f"invalid {kind}")
            return
        store = self.app.store
        try:
            # interviews belong to a job
            if kind == "interview":
                key = store.interview_key(record_id)
                if key is None:
                    self._error(HTTPStatus.NOT_FOUND, "interview not found")
                    return
                if self._reject_hidden(key):
                    return
            if action == "edit":
                result = getattr(store, f"update_{kind}")(record_id, self._read_json())
            elif action == "delete":
                result = {"ok": True} if getattr(store, f"delete_{kind}")(record_id) else None
            else:
                restored = getattr(store, f"restore_{kind}")(record_id)
                result = restored and {kind: restored}
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except database.RecordConflict as conflict:
            self._json({"error": f"This {kind} changed in another tab.", kind: conflict.record}, HTTPStatus.CONFLICT)
            return
        except Exception:
            log.exception("could not change %s", kind)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, f"Couldn't save the {kind}. Retry shortly.")
            return
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, f"{kind} not found")
            return
        self._json(result)

    def _company_route(self, path: str, method: str) -> bool:
        prefix, undo_prefix = "/api/v1/companies/", "/api/v1/company-merges/"
        if method == "POST" and path == "/api/v1/companies":
            action, key = "create", ""
        elif method == "POST" and path.startswith(undo_prefix) and path.endswith("/undo"):
            action, key = "undo", path[len(undo_prefix):-len("/undo")]
        elif path.startswith(prefix) and path.count("/") == 4 and method == "PATCH":
            action, key = "rename", path[len(prefix):]
        elif path.startswith(prefix) and path.count("/") == 5 and method == "POST":
            key, action = path[len(prefix):].split("/")
            if action not in {"hide", "restore", "merge", "links"}:
                return False
        else:
            return False
        if not self._require_write_security():
            return True
        store, key = self.app.store, urllib.parse.unquote(key)
        try:
            # undo may carry a revision
            sent = action != "undo" or self.headers.get("Content-Length", "0") != "0"
            payload = self._read_json() if action not in {"hide", "restore"} and sent else {}
            if action == "create":
                result = store.create_company(payload)
                self._json(result, HTTPStatus.CREATED if result["created"] else HTTPStatus.OK)
                return True
            if action == "undo":
                if not key.isdigit():
                    raise ValueError("invalid merge")
                result = store.undo_company_merge(int(key), payload)
            elif action == "rename":
                result = store.rename_company(key, payload)
            elif action == "merge":
                result = store.merge_company(key, payload)
            elif action == "links":
                link = store.add_company_link(key, payload)
                if link is not None:
                    self._json({"link": link}, HTTPStatus.CREATED)
                    return True
                result = None
            else:
                company = store.hide_company(key, action == "hide")
                result = company and {"company": company}
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return True
        except database.RecordConflict as conflict:
            self._json({"error": "This company changed in another tab.", "company": conflict.record}, HTTPStatus.CONFLICT)
            return True
        except database.EditConflict as error:
            self._error(HTTPStatus.CONFLICT, str(error))
            return True
        except Exception:
            log.exception("could not change company")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the company. Retry shortly.")
            return True
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, "merge not found" if action == "undo" else "company not found")
            return True
        self._json(result)
        return True

    def _record_route(self, path: str, method: str) -> bool:
        for kind, prefix in (("interview", "/api/v1/interviews/"), ("contact", "/api/v1/contacts/"), ("link", "/api/v1/company-links/"), ("rule", "/api/v1/rules/"), ("view", "/api/v1/saved-views/")):
            if not path.startswith(prefix):
                continue
            rest = path.removeprefix(prefix)
            if method == "POST" and rest.endswith("/restore"):
                self._record_write(kind, rest.removesuffix("/restore"), "restore")
            elif method in {"PATCH", "DELETE"}:
                self._record_write(kind, rest, "edit" if method == "PATCH" else "delete")
            else:
                return False
            return True
        return False

    def _patch_apply_session(self, raw_id: str) -> None:
        if not self._require_write_security():
            return
        if not raw_id.isdigit() or len(raw_id) > 18:
            self._error(HTTPStatus.NOT_FOUND, "session not found")
            return
        try:
            changes, expected = _session_changes(self._read_json())
            session = self.app.store.update_apply_session(int(raw_id), changes, expected)
        except database.RecordConflict as conflict:
            self._json({"error": "The session changed in another tab.", "session": conflict.record}, HTTPStatus.CONFLICT)
            return
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not save apply session")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the session. Retry shortly.")
            return
        if session is None:
            self._error(HTTPStatus.NOT_FOUND, "session not found")
            return
        self._json({"session": session})

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        if self._record_route(parsed.path, "PATCH") or self._company_route(parsed.path, "PATCH"):
            return
        session_prefix = "/api/v1/queue/session/"
        if parsed.path.startswith(session_prefix):
            self._patch_apply_session(parsed.path[len(session_prefix):])
            return
        prefix = "/api/v1/jobs/"
        posting_suffix = "/posting"
        if parsed.path.startswith(prefix) and parsed.path.endswith(posting_suffix):
            self._patch_posting(urllib.parse.unquote(parsed.path[len(prefix):-len(posting_suffix)].rstrip("/")))
            return
        if not parsed.path.startswith(prefix):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if self._require_api_auth() is None:
            return
        if not self._same_origin() or not self._valid_csrf():
            self._error(HTTPStatus.FORBIDDEN, "Security token missing or expired. Reload and retry.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid content length")
            return
        if length <= 0 or length > MAX_BODY:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid request size")
            return
        try:
            payload = json.loads(self.rfile.read(length))
            key = urllib.parse.unquote(parsed.path.removeprefix(prefix))
            if self._reject_hidden(key):
                return
            # absent notes stay as they are
            notes = None if payload.get("notes") is None else str(payload["notes"])
            # a notes edit keeps the status
            status = str(payload["status"]) if payload.get("status") is not None or notes is None else None
            if notes is not None and len(notes) > 20_000:
                raise ValueError("notes are too long")
            # undo must not clobber newer moves
            expected = payload.get("expected_status")
            if expected is not None and expected not in database.APPLICATION_STATUSES:
                raise ValueError("expected_status must be a job status")
            expected_notes = payload.get("expected_notes")
            if expected_notes is not None and not isinstance(expected_notes, str):
                raise ValueError("expected_notes must be text")
            if self.app.quick_fill_enabled and status in {"queued", "applying"}:
                eligibility = self.app.store.eligibility(key)
                if eligibility and eligibility.get("verdict") == "do_not_apply":
                    self._error(
                        HTTPStatus.CONFLICT,
                        "Override the eligibility verdict before applying.",
                    )
                    return
            result = self.app.store.save(key, status, notes, expected, expected_notes)
        except database.StatusConflict as error:
            self._json({"error": str(error), "status": error.status}, HTTPStatus.CONFLICT)
            return
        except database.ValueConflict as error:
            self._json({"error": "These notes changed in another tab.", "notes": error.value}, HTTPStatus.CONFLICT)
            return
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not save application state")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the change. Retry shortly.")
            return
        if not result["saved"]:
            self._error(HTTPStatus.NOT_FOUND, "job not found")
            return
        effective_status = result["status"]
        if effective_status in {"saved", "queued"}:
            self.app.store.prefetch_description(key)
        self._json({
            "ok": True, "status": effective_status, **({} if notes is None else {"notes": notes}),
            "liveness": result.get("liveness"),
        })

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        description_prefix = "/api/v1/jobs/"
        overview_suffix = "/overview"
        if parsed.path.startswith(description_prefix) and parsed.path.endswith(overview_suffix):
            if not self._require_write_security():
                return
            if self.app.overviews is None:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            key = urllib.parse.unquote(parsed.path[len(description_prefix):-len(overview_suffix)].rstrip("/"))
            if self._reject_hidden(key):
                return
            try:
                if isinstance(self.app.store, PostgresStore):
                    if not self.app.store.enqueue_overview(key):
                        self._error(HTTPStatus.NOT_FOUND, "job not found")
                        return
                    row = self.app.store.overview_job(key)
                    self.app.trigger_overview()
                    self._json({"status": row["status"], "items": row["items"] or [], "source": row["source"]}, HTTPStatus.ACCEPTED)
                    return
                detail = self.app.store.description(key)
                if detail is None:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                sections = detail.get("sections") or []
                items = self.app.overviews.overview(sections)
                fallback = getattr(self.app.overviews, "is_fallback", lambda _: False)(sections)
                self._json({"status": "ready", "items": items, "source": "posting" if fallback else "ai"})
            except Exception:
                log.exception("could not queue posting overview")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't queue the overview. Retry shortly.")
            return
        overview_actions = {"dismiss", "restore", "regenerate", "regenerate/undo"}
        if parsed.path.startswith(description_prefix) and "/overview/" in parsed.path:
            key, _, action = parsed.path[len(description_prefix):].partition("/overview/")
            if action in overview_actions:
                self._overview_write(urllib.parse.unquote(key), action)
                return
        activity_prefix = "/api/v1/activity/"
        if parsed.path.startswith(activity_prefix) and parsed.path.endswith(("/dismiss", "/restore")):
            raw_id, _, action = parsed.path[len(activity_prefix):].rpartition("/")
            self._activity_write(raw_id, action == "dismiss")
            return
        description_suffix = "/description"
        if parsed.path.startswith(description_prefix) and parsed.path.endswith(description_suffix):
            if not self._require_write_security():
                return
            key = urllib.parse.unquote(parsed.path[len(description_prefix):-len(description_suffix)].rstrip("/"))
            if self._reject_hidden(key):
                return
            try:
                detail = self.app.store.save_manual_description(
                    key, str(self._read_json().get("text") or "")
                )
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not save manually pasted description")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save that description. Retry shortly.")
                return
            if detail is None:
                self._error(HTTPStatus.NOT_FOUND, "job not found")
                return
            detail["pay"] = extract_pay(detail.get("description_text"))
            detail.pop("description_html", None)
            self._json({"description": detail})
            return
        if parsed.path == "/api/v1/capture":
            if not self._require_write_security():
                return
            try:
                job = self.app.store.capture(self._read_json())
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not capture posting")
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Couldn't fetch that posting. Add its details manually or retry shortly.",
                )
                return
            self._json({"job": job}, HTTPStatus.CREATED)
            return
        if parsed.path in {"/api/v1/hidden", "/api/v1/hidden/undo"}:
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                if parsed.path == "/api/v1/hidden":
                    keys = payload.get("dedupe_keys")
                    if not isinstance(keys, list) or not keys or len(keys) > 500 or not all(isinstance(key, str) and key for key in keys):
                        raise ValueError("dedupe_keys must be a list of job keys")
                    self._json(self.app.store.hide(keys))
                else:
                    # single use, short lived
                    self._json({"restored": self.app.store.undo_hide(payload.get("undo_token"))})
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            except database.HideUndoRefused as refused:
                self._error(HTTPStatus.NOT_FOUND if refused.unknown else HTTPStatus.CONFLICT, str(refused))
            except Exception:
                log.exception("could not change hidden jobs")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the change. Retry shortly.")
            return
        if parsed.path == "/api/v1/job-tags":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                keys = payload.get("dedupe_keys")
                if not isinstance(keys, list) or not keys or len(keys) > 500 or not all(isinstance(key, str) and key for key in keys):
                    raise ValueError("dedupe_keys must be a list of job keys")
                remove = payload.get("remove", False)
                if not isinstance(remove, bool):
                    raise ValueError("remove must be true or false")
                if self._reject_hidden(*keys):
                    return
                tag = database.clean_tag(payload.get("tag"))
                changed = self.app.store.tag_jobs(keys, tag, remove=remove)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not change job tags")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the tag. Retry shortly.")
                return
            if changed is None:
                self._error(HTTPStatus.NOT_FOUND, "job not found")
                return
            self._json({"tag": tag, "remove": remove, **changed})
            return
        if parsed.path == "/api/v1/job-tags/undo":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                ids, remove = payload.get("ids"), payload.get("remove")
                if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(type(item) is int for item in ids):
                    raise ValueError("ids must be a list of tag ids")
                if not isinstance(remove, bool):
                    raise ValueError("remove must be true or false")
                keys = self.app.store.tag_row_keys(ids)
                if not keys:
                    self._error(HTTPStatus.NOT_FOUND, "tags not found")
                    return
                if self._reject_hidden(*keys):
                    return
                result = self.app.store.undo_tag_rows(ids, remove=remove)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not undo job tags")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't undo the tag. Retry shortly.")
                return
            # another tab got there first
            if not result["ids"]:
                self._json({"error": "These tags changed in another tab.", **result}, HTTPStatus.CONFLICT)
                return
            self._json(result)
            return
        edit_prefix = "/api/v1/posting-edits/"
        if parsed.path.startswith(edit_prefix) and parsed.path.endswith("/undo"):
            if not self._require_write_security():
                return
            try:
                edit_id = int(parsed.path[len(edit_prefix):-len("/undo")].rstrip("/"))
                key = self.app.store.posting_edit_key(edit_id)
                if key is None:
                    self._error(HTTPStatus.NOT_FOUND, "edit not found")
                    return
                if self._reject_hidden(key):
                    return
                result = self.app.store.undo_posting_edit(edit_id)
            except ValueError as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except database.EditConflict as error:
                self._error(HTTPStatus.CONFLICT, str(error))
                return
            except Exception:
                log.exception("could not undo posting edit")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't undo the edit. Retry shortly.")
                return
            if result is None:
                self._error(HTTPStatus.NOT_FOUND, "edit not found")
                return
            self._edited_json(result)
            return
        rule_action_prefix = "/api/v1/rule-actions/"
        rule_action_suffix = "/undo"
        if parsed.path.startswith(rule_action_prefix) and parsed.path.endswith(rule_action_suffix):
            if not self._require_write_security():
                return
            try:
                action_id = int(parsed.path[len(rule_action_prefix):-len(rule_action_suffix)].rstrip("/"))
                action = self.app.store.undo_rule_action(action_id)
            except ValueError as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not undo rule action")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't undo the rule. Retry shortly.")
                return
            if action is None:
                self._error(HTTPStatus.NOT_FOUND, "rule action not found")
                return
            self._json({"action": action})
            return
        if parsed.path == "/api/v1/rules":
            if not self._require_write_security():
                return
            try:
                rule = self.app.store.save_rule(self._read_json())
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not save inbox rule")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the rule. Retry shortly.")
                return
            self._json({"rule": rule}, HTTPStatus.CREATED)
            return
        eligibility_prefix = "/api/v1/jobs/"
        eligibility_suffix = "/eligibility/override"
        if parsed.path.startswith(eligibility_prefix) and parsed.path.endswith(eligibility_suffix):
            if not self._require_quick_fill_write():
                return
            key = urllib.parse.unquote(
                parsed.path[len(eligibility_prefix):-len(eligibility_suffix)].rstrip("/")
            )
            if self._reject_hidden(key):
                return
            try:
                note = self._read_json().get("note") or ""
                if not isinstance(note, str):
                    raise ValueError("note must be text")
                result = self.app.store.override_eligibility(key, note)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not record eligibility override")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't record the override. Retry shortly.")
                return
            if result is None:
                self._error(HTTPStatus.NOT_FOUND, "job not found")
                return
            self._json(result, HTTPStatus.CREATED)
            return
        restore_suffix = "/eligibility/override/restore"
        if parsed.path.startswith(eligibility_prefix) and parsed.path.endswith(restore_suffix):
            self._change_override(urllib.parse.unquote(parsed.path[len(eligibility_prefix):-len(restore_suffix)].rstrip("/")), restore=True)
            return
        if self._record_route(parsed.path, "POST") or self._company_route(parsed.path, "POST"):
            return
        if parsed.path == "/api/v1/reminders":
            self._reminder_write("", "add")
            return
        if parsed.path == "/api/v1/interviews":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                if self._reject_hidden(str(payload.get("dedupe_key") or "")):
                    return
                interview = self.app.store.add_interview(payload)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not save interview")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the interview. Retry shortly.")
                return
            self._json({"interview": interview}, HTTPStatus.CREATED)
            return
        if parsed.path == "/api/v1/contacts":
            if not self._require_write_security():
                return
            try:
                contact = self.app.store.save_contact(self._read_json())
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not save contact")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the contact. Retry shortly.")
                return
            self._json({"contact": contact}, HTTPStatus.CREATED)
            return
        if parsed.path == "/api/v1/connections/import":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                contacts = payload.get("contacts")
                if not isinstance(contacts, list) or len(contacts) > 100:
                    raise ValueError("contacts must be a list of at most 100 matched connections")
                if not all(isinstance(item, dict) for item in contacts):
                    raise ValueError("every connection must be an object")
                result = self.app.store.import_connections(contacts)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not import matched connections")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't import connections. Retry shortly.")
                return
            self._json(result, HTTPStatus.CREATED)
            return
        if parsed.path == "/api/v1/queue/session":
            if not self._require_write_security():
                return
            try:
                sent = self.headers.get("Content-Length", "0") != "0"
                keys = _session_keys(self._read_json()) if sent else None
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if keys and self._reject_hidden(*keys):
                return
            try:
                session = self.app.store.start_session(keys)
                session["documents"] = self.app.store.documents() if self.app.quick_fill_enabled else []
                started = [job["dedupe_key"] for job in session["jobs"]]
                session["session"] = self.app.store.start_apply_session(started) if started else None
            except Exception:
                log.exception("could not start apply session")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't check the queue. Retry shortly.")
                return
            self._json(session, HTTPStatus.CREATED)
            return
        if parsed.path == "/api/v1/queue/check":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                if self._reject_hidden(str(payload.get("dedupe_key") or "")):
                    return
                result = self.app.store.check_session_job(str(payload.get("dedupe_key") or ""))
            except (ValueError, TypeError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not check session job")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't check this posting. Retry shortly.")
                return
            self._json(result)
            return
        applied_prefix = "/api/v1/applications/"
        applied_suffix = "/applied"
        if parsed.path.startswith(applied_prefix) and parsed.path.endswith(applied_suffix):
            if not self._require_write_security():
                return
            key = urllib.parse.unquote(
                parsed.path[len(applied_prefix):-len(applied_suffix)].rstrip("/")
            )
            if self._reject_hidden(key):
                return
            try:
                payload = self._read_json()
                snapshot = self.app.store.mark_applied(key, payload.get("document_id"))
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not mark application applied")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the application. Retry shortly.")
                return
            if snapshot is None:
                self._error(HTTPStatus.NOT_FOUND, "job not found")
                return
            self._json({"snapshot": snapshot})
            return
        if parsed.path == "/api/v1/profile/copy":
            if not self._require_quick_fill_write():
                return
            try:
                payload = self._read_json()
                if self._reject_hidden(str(payload.get("dedupe_key", ""))):
                    return
                self.app.store.mark_copy(
                    str(payload.get("dedupe_key", "")), str(payload.get("target_key", ""))
                )
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not save Quick-fill copy state")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save copy state. Retry shortly.")
                return
            self._json({"ok": True}, HTTPStatus.CREATED)
            return
        if parsed.path != "/api/v1/saved-views":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._require_write_security():
            return
        try:
            saved = self.app.store.create_view(self._read_json())
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except database.ViewNameTaken as taken:
            # the client asks before replacing
            self._json({"error": str(taken), "saved_view": taken.view}, HTTPStatus.CONFLICT)
            return
        except Exception:
            log.exception("could not save view")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the view. Retry shortly.")
            return
        self._json({"saved_view": saved}, HTTPStatus.CREATED)

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/v1/saved-views/order":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                view_ids = payload.get("ids")
                if not isinstance(view_ids, list) or not all(type(item) is int for item in view_ids):
                    raise ValueError("ids must be a list of view ids")
                expected = _expected_revision(payload)
                if expected is None:
                    raise ValueError("expected_revision is required; reload the views and retry")
                order = self.app.store.reorder_views(view_ids, expected)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except database.EditConflict as error:
                self._error(HTTPStatus.CONFLICT, str(error))
                return
            except Exception:
                log.exception("could not reorder saved views")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't reorder the views. Retry shortly.")
                return
            self._json(order)
            return
        if parsed.path == "/api/v1/rules/order":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                rule_ids = payload.get("ids")
                if not isinstance(rule_ids, list) or not all(type(item) is int for item in rule_ids):
                    raise ValueError("ids must be a list of rule ids")
                # without it a stale tab wins
                expected = _expected_revision(payload)
                if expected is None:
                    raise ValueError("expected_revision is required; reload the rules and retry")
                order = self.app.store.reorder_rules(rule_ids, expected)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except database.EditConflict as error:
                self._error(HTTPStatus.CONFLICT, str(error))
                return
            except Exception:
                log.exception("could not reorder inbox rules")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't reorder the rules. Retry shortly.")
                return
            self._json(order)
            return
        rule_prefix = "/api/v1/rules/"
        if parsed.path.startswith(rule_prefix):
            if not self._require_write_security():
                return
            try:
                rule_id = int(parsed.path.removeprefix(rule_prefix))
                enabled = self._read_json().get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be true or false")
                rule = self.app.store.set_rule_enabled(rule_id, enabled)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not update inbox rule")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't update the rule. Retry shortly.")
                return
            if rule is None:
                self._error(HTTPStatus.NOT_FOUND, "rule not found")
                return
            self._json({"rule": rule})
            return
        reminder_prefix = "/api/v1/reminders/"
        if parsed.path.startswith(reminder_prefix):
            self._reminder_write(parsed.path.removeprefix(reminder_prefix), "update")
            return
        next_step_prefix = "/api/v1/applications/"
        next_step_suffix = "/next-step"
        if parsed.path.startswith(next_step_prefix) and parsed.path.endswith(next_step_suffix):
            if not self._require_write_security():
                return
            key = urllib.parse.unquote(
                parsed.path[len(next_step_prefix):-len(next_step_suffix)].rstrip("/")
            )
            if self._reject_hidden(key):
                return
            try:
                payload = self._read_json()
                expected = payload.get("expected_next_step")
                if expected is not None and not isinstance(expected, str):
                    raise ValueError("expected_next_step must be text")
                reopen = payload.get("reopen_follow_ups")
                if reopen is not None and (not isinstance(reopen, list) or len(reopen) > 20 or not all(isinstance(item, dict) and type(item.get("id")) is int and type(item.get("revision")) is int for item in reopen)):
                    raise ValueError("reopen_follow_ups must list follow-up ids and revisions")
                saved = self.app.store.save_next_step(key, str(payload.get("next_step") or ""), expected, reopen)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except database.ValueConflict as error:
                self._json({"error": "The next step changed in another tab.", "next_step": error.value}, HTTPStatus.CONFLICT)
                return
            except Exception:
                log.exception("could not update next step")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't update the next step. Retry shortly.")
                return
            if saved is None:
                self._error(HTTPStatus.NOT_FOUND, "application not found")
                return
            self._json({"ok": True, "next_step": saved["next_step"], "closed_follow_ups": saved["closed"], "reopened_follow_ups": saved["reopened"]})
            return
        if parsed.path == "/api/v1/companies/note":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                expected = payload.get("expected_body")
                if expected is not None and not isinstance(expected, str):
                    raise ValueError("expected_body must be text")
                note = self.app.store.save_company_note(
                    str(payload.get("company") or ""), str(payload.get("body") or ""), expected
                )
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except database.ValueConflict as error:
                self._json({"error": "The company note changed in another tab.", "note": {"body": error.value}}, HTTPStatus.CONFLICT)
                return
            except Exception:
                log.exception("could not save company note")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the company note. Retry shortly.")
                return
            self._json({"note": note})
            return
        if parsed.path == "/api/v1/queue/order":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                dedupe_keys = payload.get("dedupe_keys")
                if not isinstance(dedupe_keys, list) or not all(isinstance(key, str) for key in dedupe_keys):
                    raise ValueError("dedupe_keys must be a list of job keys")
                if self._reject_hidden(*dedupe_keys):
                    return
                order = self.app.store.reorder_queue(dedupe_keys)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not reorder apply queue")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't reorder the queue. Retry shortly.")
                return
            self._json({"dedupe_keys": order})
            return
        if parsed.path == "/api/v1/tracker/board-order":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                status = payload.get("status")
                dedupe_keys = payload.get("dedupe_keys")
                if not isinstance(status, str):
                    raise ValueError("status must be a board column")
                if not isinstance(dedupe_keys, list) or not all(isinstance(key, str) for key in dedupe_keys):
                    raise ValueError("dedupe_keys must be a list of job keys")
                if self._reject_hidden(*dedupe_keys):
                    return
                order = self.app.store.reorder_board(status, dedupe_keys)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not reorder the board")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't reorder the board. Retry shortly.")
                return
            self._json({"status": status, "dedupe_keys": order})
            return
        if parsed.path not in {"/api/v1/profile", "/api/v1/profile/context"}:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._require_quick_fill_write():
            return
        try:
            payload = self._read_json()
            if parsed.path == "/api/v1/profile/context":
                if self._reject_hidden(str(payload.get("dedupe_key") or "")):
                    return
                # one field, so others stay
                if "field" not in payload:
                    raise ValueError("field is required; save one detail at a time")
                written = self.app.store.save_profile_field(payload)
                if written is None:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                self._json({"profile": _with_checks(written["profile"]), "value": written["value"]})
                return
            else:
                document = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
                revision = document.get("revision")
                # without it, tabs overwrite each other
                if not isinstance(revision, int) or isinstance(revision, bool):
                    raise ValueError("revision is required; reload the profile and retry")
                profile = self.app.store.save_profile(document, revision, merge_documents=payload.get("import") is True)
        except database.ValueConflict as error:
            self._json({"error": "It changed in another tab.", "value": error.value}, HTTPStatus.CONFLICT)
            return
        except database.ProfileConflict:
            self._json({
                "error": "Your profile changed in another tab or window.",
                "profile": _with_checks(self.app.store.profile()),
            }, HTTPStatus.CONFLICT)
            return
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not save Quick-fill profile")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save Quick-fill. Retry shortly.")
            return
        self._json({"profile": _with_checks(profile)})

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        if self._record_route(parsed.path, "DELETE"):
            return
        if parsed.path.startswith("/api/v1/reminders/"):
            self._reminder_write(parsed.path.removeprefix("/api/v1/reminders/"), "delete")
            return
        override_prefix, override_suffix = "/api/v1/jobs/", "/eligibility/override"
        if parsed.path.startswith(override_prefix) and parsed.path.endswith(override_suffix):
            self._change_override(urllib.parse.unquote(parsed.path[len(override_prefix):-len(override_suffix)].rstrip("/")), restore=False)
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        store,
        *,
        require_auth: bool = True,
        authentik_app: str = "jobseer",
        authentik_user: str | None = None,
        quick_fill_enabled: bool = False,
        overviews: OverviewService | None = None,
    ):
        super().__init__(address, DashboardHandler)
        self.store = store
        self.require_auth = require_auth
        self.authentik_app = authentik_app
        self.authentik_user = authentik_user
        self.quick_fill_enabled = quick_fill_enabled
        self.overviews = overviews
        self.csrf_token = secrets.token_urlsafe(32)
        self._overview_lock = threading.Lock()
        self._overview_running = False

    def trigger_overview(self) -> None:
        if not isinstance(self.store, PostgresStore) or self.overviews is None:
            return
        with self._overview_lock:
            if self._overview_running:
                return
            self._overview_running = True

        def work() -> None:
            try:
                from .overview_jobs import process
                process(self.store.dsn, self.overviews, limit=5, priority_only=True)
            except Exception:
                log.exception("priority overview worker failed")
            finally:
                with self._overview_lock:
                    self._overview_running = False

        threading.Thread(target=work, name="jobseer-priority-overview", daemon=True).start()


def serve(
    dsn: str | None,
    host: str = "0.0.0.0",
    port: int = 8080,
    profile_path: str | os.PathLike | None = None,
    demo: bool = False,
) -> None:
    demo_count = int(os.environ.get("JOBSCOUT_DEMO_COUNT", "2"))
    demo_seed = int(os.environ.get("JOBSCOUT_DEMO_SEED", "0"))
    store = DemoStore(count=demo_count, seed=demo_seed, profile_path=profile_path) if demo else PostgresStore(dsn or "")
    server = DashboardServer(
        (host, port),
        store,
        require_auth=not demo,
        authentik_app=os.environ.get("JOBSCOUT_AUTHENTIK_APP", "jobseer"),
        authentik_user=os.environ.get("JOBSCOUT_AUTHENTIK_USER"),
        quick_fill_enabled=_env_enabled("JOBSCOUT_QUICK_FILL_ENABLED"),
        overviews=(
            OverviewService(
                os.environ.get("JOBSCOUT_LLM_URL", "http://ollama.ai.svc.cluster.local:11434"),
                os.environ.get("JOBSCOUT_LLM_MODEL", "qwen3.5:9b"),
            ) if _env_enabled("JOBSCOUT_AI_OVERVIEW_ENABLED") else None
        ),
    )
    log.info("application workspace listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
