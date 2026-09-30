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
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import yaml

from . import db as database
from .descriptions import ProviderDescriptionFetcher
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
        self._fetcher = Fetcher(timeout=15, retry_seconds=20)
        self._descriptions = ProviderDescriptionFetcher(self._fetcher)
        self._liveness = PostingLivenessChecker(self._fetcher)
        self._prefetch_lock = threading.Lock()
        self._prefetching: set[str] = set()

    def jobs(self, include_closed: bool = False) -> list[dict]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.dashboard_postings(conn, include_closed=include_closed)

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
        queued = [job for job in self.jobs() if job["status"] == "queued"]
        skipped = []
        for job in queued:
            result = self.check_liveness(job["dedupe_key"], force=True)
            if result.status == "closed":
                with database.connect(self.dsn) as conn:
                    database.require_schema(conn)
                    database.save_application_state(
                        conn, job["dedupe_key"], "archived", job.get("notes") or ""
                    )
                skipped.append({"dedupe_key": job["dedupe_key"], "reason": "Posting closed"})
        jobs = [job for job in self.jobs() if job["status"] == "queued"]
        jobs.sort(key=_queue_sort_key)
        return {"jobs": jobs, "skipped": skipped}

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

    def healthy(self) -> bool:
        try:
            with database.connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute("select 1")
                return cur.fetchone() is not None
        except Exception:
            log.exception("database health check failed")
            return False

    def description(self, key: str) -> dict | None:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            cached = database.cached_description(conn, key)
            if cached and cached.get("description_text"):
                return cached
            if cached and cached.get("description_error"):
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

    def jobs(self, include_closed: bool = False) -> list[dict]:
        return [dict(row) for row in self.rows if include_closed or not row["closed"]]

    def save(self, key: str, status: str, notes: str) -> dict:
        if status not in database.APPLICATION_STATUSES:
            raise ValueError(f"unknown application status: {status}")
        for row in self.rows:
            if row["dedupe_key"] == key:
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
                if effective_status != "queued":
                    row["queue_position"] = None
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
        skipped = []
        now = dt.datetime.now(dt.timezone.utc)
        for row in self.rows:
            if row["status"] != "queued":
                continue
            row.update(
                liveness_status="closed" if row.get("closed") or not row.get("url") else "live",
                liveness_checked_at=now,
                liveness_evidence="Posting is unavailable" if row.get("closed") or not row.get("url") else "Demo posting is available",
            )
            if row["liveness_status"] == "closed":
                row["status"] = "archived"
                skipped.append({"dedupe_key": row["dedupe_key"], "reason": "Posting closed"})
        queued = [dict(row) for row in self.rows if row["status"] == "queued"]
        queued.sort(key=_queue_sort_key)
        return {
            "jobs": queued,
            "skipped": skipped,
        }

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
        profile["company_account"] = copy.deepcopy(self._company_accounts.get((company or "").casefold().strip()))
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
        self._company_accounts[company.casefold()] = clean["company_account"]
        return self.profile(dedupe_key, company)

    def healthy(self) -> bool:
        return True

    def description(self, key: str) -> dict | None:
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
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=300")
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
                        "features": {"quick_fill": self.app.quick_fill_enabled},
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
            if parsed.path == "/api/v1/saved-views":
                self._json({"saved_views": self.app.store.saved_views()})
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
            detail_suffix = "/description"
            if parsed.path.startswith(detail_prefix) and parsed.path.endswith(detail_suffix):
                key = urllib.parse.unquote(
                    parsed.path[len(detail_prefix):-len(detail_suffix)].rstrip("/")
                )
                if not key:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                detail = self.app.store.description(key)
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
    ):
        super().__init__(address, DashboardHandler)
        self.store = store
        self.require_auth = require_auth
        self.authentik_app = authentik_app
        self.authentik_user = authentik_user
        self.quick_fill_enabled = quick_fill_enabled
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
    )
    log.info("application workspace listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
