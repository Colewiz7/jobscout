"""Dependency-free HTTP boundary for the JobSeer application workspace."""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
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
from .models import Posting
from .capture import PostingCapture, manual_capture
from .descriptions import DescriptionAccessBlocked, ProviderDescriptionFetcher, parse_description, parse_sections
from .overview import OverviewService
from .eligibility import analyze as analyze_eligibility
from .http import Fetcher
from .liveness import LivenessResult, PostingLivenessChecker

log = logging.getLogger("jobscout.dashboard")
STATIC_ROOT = pathlib.Path(__file__).with_name("static")
DEFAULT_PROFILE = pathlib.Path(__file__).resolve().parents[2] / "config" / "profile.seed.json"
MAX_BODY = 64 * 1024
CSRF_COOKIE = "jobseer_csrf"
APP_ROUTE_ROOTS = frozenset({"inbox", "queue", "tracker", "companies", "profile"})


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


def _company_groups(jobs: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    tracked = {"applying", "applied", "interviewing", "offer", "rejected"}
    for job in jobs:
        key = database.company_key(str(job.get("company") or ""))
        if not key:
            continue
        group = grouped.setdefault(key, {
            "key": key,
            "name": job["company"],
            "postings": [],
            "applications": [],
            "connections_count": 0,
            "links": [],
        })
        group["postings"].append(job)
        if job.get("status") in tracked:
            group["applications"].append(job)
        group["connections_count"] = max(
            group["connections_count"], int(job.get("connections_count") or 0)
        )
        url = str(job.get("url") or "")
        if url and not any(item["url"] == url for item in group["links"]):
            group["links"].append({"label": job["title"], "url": url})
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
        tags = []
        boost = 0
        for rule in active:
            pattern = database.normalise(str(rule.get("pattern") or ""))
            if rule.get("kind") == "tag_title" and pattern and pattern in title:
                tag = str(rule.get("value") or "")
                if tag and tag not in tags:
                    tags.append(tag)
            elif rule.get("kind") == "boost_company" and pattern and pattern in company:
                boost += int(rule.get("value") or 0)
        job["tags"] = tags
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
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            jobs = database.dashboard_postings(conn, include_closed=include_closed)
            counts = database.contact_counts(conn)
            active_rules = database.rules(conn)
        # Old rows are retained for audit/history, but a changed scout filter
        # must remove newly discovered out-of-scope rows from today's Inbox.
        jobs = [job for job in jobs if _visible_scoped_job(job, self._filter_config)]
        for job in jobs:
            job["connections_count"] = counts.get(database.company_key(job["company"]), 0)
        return _annotate_rules(_annotate_reposts(jobs), active_rules)

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
        return next(
            job for job in self.jobs(include_closed=True)
            if job["dedupe_key"] == posting.dedupe_key
        )

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

    def save(self, key: str, status: str, notes: str) -> dict:
        liveness = None
        effective_status = status
        if status == "queued":
            liveness = self.check_liveness(key)
            if liveness.status == "closed":
                effective_status = "archived"
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            saved = database.save_application_state(conn, key, effective_status, notes)
        return {
            "saved": saved,
            "status": effective_status,
            "liveness": dataclasses.asdict(liveness) if liveness else None,
        }

    def reorder_queue(self, dedupe_keys: list[str]) -> list[str]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_queue_order(conn, dedupe_keys)

    def start_session(self) -> dict:
        # Do not probe the whole queue before showing the first job. A large
        # queue would make this request take minutes and appear blank to users.
        jobs = [job for job in self.jobs() if job["status"] == "queued"]
        jobs.sort(key=_queue_sort_key)
        return {"jobs": jobs, "skipped": []}

    def check_session_job(self, key: str) -> dict:
        job = next((item for item in self.jobs() if item["dedupe_key"] == key), None)
        if job is None or job["status"] != "queued":
            raise ValueError("job is no longer queued")
        result = self.check_liveness(key, force=True)
        if result.status == "closed":
            with database.connect(self.dsn) as conn:
                database.require_schema(conn)
                database.save_application_state(conn, key, "archived", job.get("notes") or "")
        return dataclasses.asdict(result)

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

    def update_reminder(self, reminder_id: int, action: str) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_reminder(conn, reminder_id, action)

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

    def save_next_step(self, key: str, value: str) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_next_step(conn, key, value)

    def companies(self, name: str | None = None, *, include_account: bool = False) -> dict:
        jobs = self.jobs(include_closed=True)
        groups = _company_groups(jobs)
        if not name:
            return {"companies": groups}
        key = database.company_key(name)
        company = next((item for item in groups if item["key"] == key), None)
        if company is None:
            return {"company": None}
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            records = database.company_records(conn, company["name"])
            account = database.company_account(conn, company["name"]) if include_account else None
        company = {**company, **records, "account": account, "account_protected": not include_account}
        return {"company": company}

    def save_company_note(self, name: str, body: str) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_company_note(conn, name, body)

    def save_contact(self, payload: dict, source: str = "manual") -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_contact(conn, payload, source=source)

    def import_connections(self, contacts: list[dict]) -> dict:
        companies = {
            item["key"]: item["name"] for item in _company_groups(self.jobs(include_closed=True))
        }
        matched = []
        for contact in contacts:
            key = database.company_key(str(contact.get("company") or ""))
            if key not in companies:
                continue
            matched.append({**contact, "company": companies[key]})
        saved = [self.save_contact(item, source="linkedin_csv") for item in matched]
        return {"imported": len(saved), "unmatched": len(contacts) - len(matched)}

    def saved_views(self) -> list[dict]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.saved_views(conn)

    def save_view(self, payload: dict) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_view(
                conn,
                name=str(payload.get("name", "")),
                filters=payload.get("filters") if isinstance(payload.get("filters"), dict) else {},
                sort=str(payload.get("sort", "score")),
                pinned=bool(payload.get("pinned", True)),
            )

    def delete_view(self, view_id: int) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.delete_saved_view(conn, view_id)

    def profile(self, dedupe_key: str | None = None, company: str | None = None) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.profile_data(conn, dedupe_key=dedupe_key, company=company)

    def save_profile(self, payload: dict) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.replace_profile_data(conn, payload)

    def mark_copy(self, dedupe_key: str, target_key: str) -> None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            database.mark_quick_fill_copy(conn, dedupe_key, target_key)

    def save_profile_context(self, payload: dict) -> dict:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.save_quick_fill_context(
                conn,
                dedupe_key=str(payload.get("dedupe_key", "")),
                company=str(payload.get("company", "")),
                answer_overrides=payload.get("answer_overrides", {}),
                company_account=payload.get("company_account", {}),
            )

    def eligibility(self, key: str) -> dict | None:
        description = self.description(key)
        if description is None:
            return None
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            profile = database.profile_data(conn)
            overrides = database.eligibility_overrides(conn, key)
        return analyze_eligibility(
            text=str(description.get("description_text") or ""),
            sections=description.get("sections") or [],
            profile=profile,
            overrides=overrides,
        )

    def override_eligibility(self, key: str, note: str = "") -> dict | None:
        result = self.eligibility(key)
        if result is None:
            return None
        blockers = [item for item in result["blockers"] if not item.get("overridden")]
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            database.record_eligibility_overrides(conn, key, blockers, note)
        return self.eligibility(key)

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
            "deadline": None, "queue_position": None,
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
                "deadline": None, "queue_position": 0,
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
                "deadline": None, "queue_position": None,
                "liveness_status": None, "liveness_checked_at": None,
                "liveness_evidence": None,
                "applied_at": None, "resume_document_id": None, "resume_name": None,
            },
        ]
        self.rows = _demo_rows(count, seed, now) if count > 2 else base_rows[:count]
        self._saved_views: list[dict] = []
        self._next_view_id = 1
        self._profile_path = profile_path or DEFAULT_PROFILE
        self._profile = load_profile(self._profile_path)
        self._copied_fields: dict[str, set[str]] = {}
        self._answer_overrides: dict[str, dict[str, str]] = {}
        self._company_accounts: dict[str, dict] = {}
        self._reminders = []
        self._interviews = []
        self._contacts = []
        self._company_notes: dict[str, dict] = {}
        self._next_reminder_id = 1
        self._next_interview_id = 1
        self._next_contact_id = 1
        self._eligibility_overrides: dict[str, list[dict]] = {}
        self._rules: list[dict] = []
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
                    "snoozed_until": None,
                    "company": row["company"],
                    "title": row["title"],
                })
                self._next_reminder_id += 1

    def _upsert_demo_followup(self, row: dict) -> None:
        reminder = next(
            (item for item in self._reminders if item["dedupe_key"] == row["dedupe_key"]),
            None,
        )
        values = {
            "dedupe_key": row["dedupe_key"], "kind": "follow_up",
            "due_at": row["applied_at"] + dt.timedelta(days=7),
            "status": "pending", "snoozed_until": None,
            "company": row["company"], "title": row["title"],
        }
        if reminder:
            reminder.update(values)
        else:
            self._reminders.append({"id": self._next_reminder_id, **values})
            self._next_reminder_id += 1

    def _complete_demo_followup(self, key: str) -> None:
        for reminder in self._reminders:
            if reminder["dedupe_key"] == key and reminder["kind"] == "follow_up":
                reminder["status"] = "done"

    def jobs(self, include_closed: bool = False) -> list[dict]:
        counts: dict[str, int] = {}
        for contact in self._contacts:
            if contact["source"] == "linkedin_csv":
                counts[contact["company_key"]] = counts.get(contact["company_key"], 0) + 1
        jobs = [dict(row) for row in self.rows if include_closed or not row["closed"]]
        for job in jobs:
            job["connections_count"] = counts.get(database.company_key(job["company"]), 0)
            job.setdefault("next_step", "")
        return _annotate_rules(_annotate_reposts(jobs), self._rules)

    def capture(self, payload: dict) -> dict:
        from .capture import clean_url

        url, _ = clean_url(payload.get("url"))
        posting = manual_capture(payload, url).posting
        existing = next(
            (row for row in self.rows if row["dedupe_key"] == posting.dedupe_key), None
        )
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
            "deadline": None, "queue_position": None,
            "liveness_status": None, "liveness_checked_at": None,
            "liveness_evidence": None, "applied_at": None,
            "resume_document_id": None, "resume_name": None,
        }
        self.rows.append(row)
        return copy.deepcopy(row)

    def rules(self) -> list[dict]:
        return copy.deepcopy(self._rules)

    def _apply_demo_rule(self, rule: dict) -> None:
        if not rule.get("enabled") or rule.get("kind") != "archive_title":
            return
        pattern = database.normalise(rule["pattern"])
        for row in self.rows:
            if pattern not in database.normalise(row["title"]) or row["status"] != "new":
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
            "created_at": now, "updated_at": now,
        }
        self._next_rule_id += 1
        self._rules.append(rule)
        self._apply_demo_rule(rule)
        return copy.deepcopy(rule)

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> dict | None:
        rule = next((item for item in self._rules if item["id"] == rule_id), None)
        if rule is None:
            return None
        rule["enabled"] = bool(enabled)
        rule["updated_at"] = dt.datetime.now(dt.timezone.utc)
        self._apply_demo_rule(rule)
        return copy.deepcopy(rule)

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

    def save(self, key: str, status: str, notes: str) -> dict:
        if status not in database.APPLICATION_STATUSES:
            raise ValueError(f"unknown application status: {status}")
        for row in self.rows:
            if row["dedupe_key"] == key:
                previous_status = row["status"]
                previous_notes = row.get("notes") or ""
                effective_status = status
                if status == "queued":
                    row.update(
                        liveness_status="closed" if row.get("closed") or not row.get("url") else "live",
                        liveness_checked_at=dt.datetime.now(dt.timezone.utc),
                        liveness_evidence="Posting is unavailable" if row.get("closed") or not row.get("url") else "Demo posting is available",
                    )
                    if row["liveness_status"] == "closed":
                        effective_status = "archived"
                row.update(
                    status=effective_status,
                    notes=notes,
                    application_updated_at=dt.datetime.now(dt.timezone.utc),
                )
                if effective_status != "archived" and row.get("rule_action_id"):
                    row.update(archived_by_rule=None, rule_action_id=None)
                if effective_status != "queued":
                    row["queue_position"] = None
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
                    } if status == "queued" else None,
                }
        return {"saved": False, "status": status, "liveness": None}

    def reorder_queue(self, dedupe_keys: list[str]) -> list[str]:
        queued = {row["dedupe_key"] for row in self.rows if row["status"] == "queued"}
        if queued != set(dedupe_keys) or len(dedupe_keys) != len(set(dedupe_keys)):
            raise ValueError("queue order must contain every queued job exactly once")
        positions = {key: index for index, key in enumerate(dedupe_keys)}
        for row in self.rows:
            if row["dedupe_key"] in positions:
                row["queue_position"] = positions[row["dedupe_key"]]
        return dedupe_keys

    def start_session(self) -> dict:
        queued = [dict(row) for row in self.rows if row["status"] == "queued"]
        queued.sort(key=_queue_sort_key)
        return {"jobs": queued, "skipped": []}

    def check_session_job(self, key: str) -> dict:
        row = next((item for item in self.rows if item["dedupe_key"] == key and item["status"] == "queued"), None)
        if row is None:
            raise ValueError("job is no longer queued")
        closed = bool(row.get("closed") or not row.get("url"))
        row.update(liveness_status="closed" if closed else "live",
                   liveness_checked_at=dt.datetime.now(dt.timezone.utc),
                   liveness_evidence="Posting is unavailable" if closed else "Demo posting is available")
        if closed:
            row["status"] = "archived"
        return {"status": row["liveness_status"], "evidence": row["liveness_evidence"]}

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
        return copy.deepcopy(self._profile.get("documents", []))

    def tracker(self) -> dict:
        applications = [
            job for job in self.jobs(include_closed=True)
            if job["status"] in {"applying", "applied", "interviewing", "offer", "rejected"}
        ]
        return {
            "applications": applications,
            "reminders": copy.deepcopy([
                reminder for reminder in self._reminders if reminder["status"] != "done"
            ]),
            "interviews": copy.deepcopy(sorted(
                self._interviews, key=lambda item: item["starts_at"]
            )),
            "history": [],
        }

    def update_reminder(self, reminder_id: int, action: str) -> dict | None:
        if action not in {"done", "snooze"}:
            raise ValueError("reminder action must be done or snooze")
        reminder = next((item for item in self._reminders if item["id"] == reminder_id), None)
        if reminder is None:
            return None
        if action == "done":
            reminder.update(status="done", completed_at=dt.datetime.now(dt.timezone.utc))
        else:
            reminder.update(
                status="snoozed",
                snoozed_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3),
            )
        return copy.deepcopy(reminder)

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
            "starts_at": starts_at, "ends_at": ends_at,
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

    def save_next_step(self, key: str, value: str) -> bool:
        if len(value) > 1_000:
            raise ValueError("next step is too long")
        row = next((item for item in self.rows if item["dedupe_key"] == key), None)
        if row is None:
            return False
        row["next_step"] = value.strip()
        row["application_updated_at"] = dt.datetime.now(dt.timezone.utc)
        self._complete_demo_followup(key)
        return True

    def companies(self, name: str | None = None, *, include_account: bool = False) -> dict:
        groups = _company_groups(self.jobs(include_closed=True))
        if not name:
            return {"companies": groups}
        key = database.company_key(name)
        company = next((item for item in groups if item["key"] == key), None)
        if company is None:
            return {"company": None}
        contacts = [item for item in self._contacts if item["company_key"] == key]
        note = self._company_notes.get(key, {"body": "", "updated_at": None})
        account = self._company_accounts.get(database.company_key(company["name"])) if include_account else None
        return {"company": {
            **company,
            "contacts": copy.deepcopy(contacts),
            "note": copy.deepcopy(note),
            "account": copy.deepcopy(account),
            "account_protected": not include_account,
        }}

    def save_company_note(self, name: str, body: str) -> dict:
        if len(body) > 20_000:
            raise ValueError("company note is too long")
        key = database.company_key(name)
        if not key:
            raise ValueError("company name is required")
        company = next(
            (item for item in _company_groups(self.jobs(include_closed=True)) if item["key"] == key),
            None,
        )
        if company is None:
            raise ValueError("company not found")
        note = {"body": body, "updated_at": dt.datetime.now(dt.timezone.utc)}
        self._company_notes[key] = note
        return copy.deepcopy(note)

    def save_contact(self, payload: dict, source: str = "manual") -> dict:
        company = str(payload.get("company") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not company or not name or source not in {"manual", "linkedin_csv"}:
            raise ValueError("company and contact name are required")
        linkedin_url = str(payload.get("linkedin_url") or "").strip()
        if linkedin_url and not linkedin_url.startswith("https://"):
            raise ValueError("LinkedIn URL must use HTTPS")
        key = database.company_key(company)
        known_company = next(
            (item for item in _company_groups(self.jobs(include_closed=True)) if item["key"] == key),
            None,
        )
        if known_company is None:
            raise ValueError("company not found")
        company = known_company["name"]
        if source == "linkedin_csv":
            existing = next((item for item in self._contacts if
                item["company_key"] == key and item["name"].casefold() == name.casefold()
                and item["linkedin_url"] == linkedin_url), None)
            if existing:
                existing.update(title=str(payload.get("title") or ""), updated_at=dt.datetime.now(dt.timezone.utc))
                return copy.deepcopy(existing)
        contact = {
            "id": self._next_contact_id, "company_key": key, "company_name": company,
            "name": name, "title": str(payload.get("title") or "")[:500],
            "email": str(payload.get("email") or "")[:500],
            "linkedin_url": linkedin_url[:2_000], "source": source,
            "created_at": dt.datetime.now(dt.timezone.utc),
            "updated_at": dt.datetime.now(dt.timezone.utc),
        }
        self._next_contact_id += 1
        self._contacts.append(contact)
        return copy.deepcopy(contact)

    def import_connections(self, contacts: list[dict]) -> dict:
        companies = {
            item["key"]: item["name"] for item in _company_groups(self.jobs(include_closed=True))
        }
        matched = []
        for contact in contacts:
            key = database.company_key(str(contact.get("company") or ""))
            if key in companies:
                matched.append({**contact, "company": companies[key]})
        for contact in matched:
            self.save_contact(contact, source="linkedin_csv")
        return {"imported": len(matched), "unmatched": len(contacts) - len(matched)}

    def saved_views(self) -> list[dict]:
        return [dict(view) for view in self._saved_views]

    def save_view(self, payload: dict) -> dict:
        name = str(payload.get("name", "")).strip()
        if not name or len(name) > 40:
            raise ValueError("view name must be between 1 and 40 characters")
        sort = str(payload.get("sort", "score"))
        if sort not in {"score", "newest", "company"}:
            raise ValueError("unknown saved-view sort")
        existing = next((view for view in self._saved_views if view["name"] == name), None)
        if existing is None:
            existing = {"id": self._next_view_id, "name": name}
            self._next_view_id += 1
            self._saved_views.append(existing)
        existing.update(
            filters=dict(payload.get("filters") or {}),
            sort=sort,
            pinned=bool(payload.get("pinned", True)),
        )
        return dict(existing)

    def delete_view(self, view_id: int) -> bool:
        before = len(self._saved_views)
        self._saved_views = [view for view in self._saved_views if view["id"] != view_id]
        return len(self._saved_views) != before

    def profile(self, dedupe_key: str | None = None, company: str | None = None) -> dict:
        profile = copy.deepcopy(self._profile)
        profile["copied_fields"] = sorted(self._copied_fields.get(dedupe_key or "", set()))
        profile["answer_overrides"] = copy.deepcopy(self._answer_overrides.get(dedupe_key or "", {}))
        profile["company_account"] = copy.deepcopy(
            self._company_accounts.get(database.company_key(company or ""))
        )
        return profile

    def save_profile(self, payload: dict) -> dict:
        self._profile = database.normalize_profile_data(payload)
        return self.profile()

    def mark_copy(self, dedupe_key: str, target_key: str) -> None:
        if not dedupe_key or len(dedupe_key) > 500 or not target_key or len(target_key) > 200:
            raise ValueError("invalid job or copy target")
        self._copied_fields.setdefault(dedupe_key, set()).add(target_key)

    def save_profile_context(self, payload: dict) -> dict:
        clean = database.normalize_quick_fill_context(
            dedupe_key=str(payload.get("dedupe_key", "")),
            company=str(payload.get("company", "")),
            answer_overrides=payload.get("answer_overrides", {}),
            company_account=payload.get("company_account", {}),
        )
        dedupe_key = clean["dedupe_key"]
        company = clean["company"]
        self._answer_overrides[dedupe_key] = clean["answer_overrides"]
        self._company_accounts[database.company_key(company)] = clean["company_account"]
        return self.profile(dedupe_key, company)

    def eligibility(self, key: str) -> dict | None:
        description = self.description(key)
        if description is None:
            return None
        return analyze_eligibility(
            text=str(description.get("description_text") or ""),
            sections=description.get("sections") or [],
            profile=self._profile,
            overrides=self._eligibility_overrides.get(key, []),
        )

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
        self._eligibility_overrides.setdefault(key, []).extend({
            "blocker_key": item["key"], "evidence": item["evidence"],
            "comparison": item["comparison"], "note": note.strip(), "created_at": now,
        } for item in blockers)
        return self.eligibility(key)

    def healthy(self) -> bool:
        return True

    def description(self, key: str, *, force: bool = False) -> dict | None:
        if not any(row["dedupe_key"] == key for row in self.rows):
            return None
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
        detail = parse_description(text)
        return {
            "description_text": detail.text, "sections": detail.sections,
            "description_fetched_at": dt.datetime.now(dt.timezone.utc),
            "description_error": None, "deadline": detail.deadline,
            "deadline_source": detail.deadline_source,
        }

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

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid content length") from error
        if length <= 0 or length > MAX_BODY:
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
                self._json({"saved_views": self.app.store.saved_views()})
                return
            if parsed.path == "/api/v1/rules":
                try:
                    self._json({"rules": self.app.store.rules()})
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
                    "profile": self.app.store.profile(
                        dedupe_key=query.get("job", [None])[0],
                        company=query.get("company", [None])[0],
                    )
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
                    self._json({"items": items or [], "source": "posting" if fallback else "ai"})
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

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        prefix = "/api/v1/jobs/"
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
            status = str(payload["status"])
            notes = str(payload.get("notes", ""))
            if len(notes) > 20_000:
                raise ValueError("notes are too long")
            if self.app.quick_fill_enabled and status in {"queued", "applying"}:
                eligibility = self.app.store.eligibility(key)
                if eligibility and eligibility.get("verdict") == "do_not_apply":
                    self._error(
                        HTTPStatus.CONFLICT,
                        "Override the eligibility verdict before applying.",
                    )
                    return
            result = self.app.store.save(key, status, notes)
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
            "ok": True, "status": effective_status, "notes": notes,
            "liveness": result.get("liveness"),
        })

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        description_prefix = "/api/v1/jobs/"
        description_suffix = "/description"
        if parsed.path.startswith(description_prefix) and parsed.path.endswith(description_suffix):
            if not self._require_write_security():
                return
            key = urllib.parse.unquote(parsed.path[len(description_prefix):-len(description_suffix)].rstrip("/"))
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
            try:
                result = self.app.store.override_eligibility(
                    key, str(self._read_json().get("note") or "")
                )
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
            self._json({"eligibility": result}, HTTPStatus.CREATED)
            return
        if parsed.path == "/api/v1/interviews":
            if not self._require_write_security():
                return
            try:
                interview = self.app.store.add_interview(self._read_json())
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
                session = self.app.store.start_session()
                session["documents"] = self.app.store.documents() if self.app.quick_fill_enabled else []
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
            saved = self.app.store.save_view(self._read_json())
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not save view")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the view. Retry shortly.")
            return
        self._json({"saved_view": saved}, HTTPStatus.CREATED)

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
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
            if not self._require_write_security():
                return
            try:
                reminder_id = int(parsed.path.removeprefix(reminder_prefix))
                reminder = self.app.store.update_reminder(
                    reminder_id, str(self._read_json().get("action") or "")
                )
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not update reminder")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't update the reminder. Retry shortly.")
                return
            if reminder is None:
                self._error(HTTPStatus.NOT_FOUND, "reminder not found")
                return
            self._json({"reminder": reminder})
            return
        next_step_prefix = "/api/v1/applications/"
        next_step_suffix = "/next-step"
        if parsed.path.startswith(next_step_prefix) and parsed.path.endswith(next_step_suffix):
            if not self._require_write_security():
                return
            key = urllib.parse.unquote(
                parsed.path[len(next_step_prefix):-len(next_step_suffix)].rstrip("/")
            )
            try:
                saved = self.app.store.save_next_step(
                    key, str(self._read_json().get("next_step") or "")
                )
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except Exception:
                log.exception("could not update next step")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't update the next step. Retry shortly.")
                return
            if not saved:
                self._error(HTTPStatus.NOT_FOUND, "application not found")
                return
            self._json({"ok": True})
            return
        if parsed.path == "/api/v1/companies/note":
            if not self._require_write_security():
                return
            try:
                payload = self._read_json()
                note = self.app.store.save_company_note(
                    str(payload.get("company") or ""), str(payload.get("body") or "")
                )
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
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
        if parsed.path not in {"/api/v1/profile", "/api/v1/profile/context"}:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._require_quick_fill_write():
            return
        try:
            payload = self._read_json()
            if parsed.path == "/api/v1/profile/context":
                profile = self.app.store.save_profile_context(payload)
            else:
                profile = self.app.store.save_profile(
                    payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
                )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not save Quick-fill profile")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save Quick-fill. Retry shortly.")
            return
        self._json({"profile": profile})

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        prefix = "/api/v1/saved-views/"
        if not parsed.path.startswith(prefix):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._require_write_security():
            return
        try:
            view_id = int(parsed.path.removeprefix(prefix))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid saved view")
            return
        if not self.app.store.delete_view(view_id):
            self._error(HTTPStatus.NOT_FOUND, "saved view not found")
            return
        self._json({"ok": True})


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
