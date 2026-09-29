"""Small, dependency-free HTTP service for the job application workspace.

The scout remains a short-lived CronJob. This process is a separate deployment
which reads the same Postgres and owns only the human application workflow.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import mimetypes
import os
import pathlib
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import yaml

from . import db as database

log = logging.getLogger("jobscout.dashboard")
STATIC_ROOT = pathlib.Path(__file__).with_name("static")
DEFAULT_PROFILE = pathlib.Path(__file__).resolve().parents[2] / "config" / "profile.yaml"
MAX_BODY = 64 * 1024


def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def load_profile(path: str | os.PathLike | None = None) -> dict:
    profile_path = pathlib.Path(path or os.environ.get("JOBSCOUT_PROFILE") or DEFAULT_PROFILE)
    raw = yaml.safe_load(profile_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError("profile YAML must contain a mapping")
    return raw


class PostgresStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def jobs(self, include_closed: bool = False) -> list[dict]:
        with database.connect(self.dsn) as conn:
            database.ensure_schema(conn)
            return database.dashboard_postings(conn, include_closed=include_closed)

    def save(self, key: str, status: str, notes: str) -> bool:
        with database.connect(self.dsn) as conn:
            database.ensure_schema(conn)
            return database.save_application_state(conn, key, status, notes)

    def healthy(self) -> bool:
        try:
            with database.connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute("select 1")
                return cur.fetchone() is not None
        except Exception:
            log.exception("database health check failed")
            return False


class DemoStore:
    """Representative local data for visual development; never used by default."""

    def __init__(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.rows = [
            {
                "dedupe_key": "demo:1", "company": "Cloudflare",
                "title": "Site Reliability Engineer Intern — Summer 2027",
                "location": "Austin, TX; Remote in USA", "terms": "Summer 2027",
                "age_days": 1, "first_seen": now - dt.timedelta(days=1),
                "last_seen": now, "url": "https://example.com/apply", "sources": "greenhouse",
                "closed": False, "notified": True, "score": 160,
                "score_detail": {"role_named": 100, "internship": 20, "wanted_term": 30, "fresh": 10},
                "status": "preparing", "notes": "Mention the homelab incident response story.",
                "application_updated_at": now,
            },
            {
                "dedupe_key": "demo:2", "company": "Datadog",
                "title": "Cloud Platform Engineering Intern",
                "location": "New York, NY", "terms": "",
                "age_days": 3, "first_seen": now - dt.timedelta(days=3),
                "last_seen": now, "url": "https://example.com/apply", "sources": "greenhouse, simplify-s27",
                "closed": False, "notified": True, "score": 130,
                "score_detail": {"role_named": 100, "internship": 20, "fresh": 10},
                "status": "new", "notes": "", "application_updated_at": None,
            },
            {
                "dedupe_key": "demo:3", "company": "Fastly",
                "title": "Infrastructure Engineering Co-op",
                "location": "Remote in USA", "terms": "Spring 2027",
                "age_days": 6, "first_seen": now - dt.timedelta(days=6),
                "last_seen": now, "url": "https://example.com/apply", "sources": "lever",
                "closed": False, "notified": True, "score": 180,
                "score_detail": {"role_named": 100, "co_op": 40, "wanted_term": 30, "fresh": 10},
                "status": "saved", "notes": "", "application_updated_at": now,
            },
            {
                "dedupe_key": "demo:4", "company": "Grafana Labs",
                "title": "Developer Infrastructure Intern",
                "location": "Remote — United States", "terms": "Summer 2027",
                "age_days": 9, "first_seen": now - dt.timedelta(days=9),
                "last_seen": now, "url": "https://example.com/apply", "sources": "greenhouse",
                "closed": False, "notified": True, "score": 150,
                "score_detail": {"role_named": 100, "internship": 20, "wanted_term": 30},
                "status": "applied", "notes": "Applied through Greenhouse.",
                "application_updated_at": now,
            },
            {
                "dedupe_key": "demo:5", "company": "Tailscale",
                "title": "Systems Engineering Intern",
                "location": "New York, NY", "terms": "",
                "age_days": 12, "first_seen": now - dt.timedelta(days=12),
                "last_seen": now, "url": "https://example.com/apply", "sources": "ashby",
                "closed": False, "notified": True, "score": 120,
                "score_detail": {"role_named": 100, "internship": 20},
                "status": "interview", "notes": "Technical screen Tuesday at 2 PM.",
                "application_updated_at": now,
            },
        ]

    def jobs(self, include_closed: bool = False) -> list[dict]:
        return [dict(row) for row in self.rows if include_closed or not row["closed"]]

    def save(self, key: str, status: str, notes: str) -> bool:
        if status not in database.APPLICATION_STATUSES:
            raise ValueError(f"unknown application status: {status}")
        for row in self.rows:
            if row["dedupe_key"] == key:
                row.update(status=status, notes=notes, application_updated_at=dt.datetime.now(dt.timezone.utc))
                return True
        return False

    def healthy(self) -> bool:
        return True


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "jobscout-dashboard"

    @property
    def app(self):
        return self.server  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; connect-src 'self'; "
            "font-src 'self'; form-action 'none'; frame-ancestors 'none'; "
            "img-src 'self' data:; object-src 'none'; script-src 'self'; "
            "style-src 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=_json_value, separators=(",", ":")).encode()
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urllib.parse.urlsplit(origin).netloc == self.headers.get("Host", "")

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

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/api/profile":
            self._json(self.app.profile)
            return
        if parsed.path == "/api/jobs":
            query = urllib.parse.parse_qs(parsed.query)
            include_closed = query.get("closed", [""])[0].lower() in {"1", "true", "yes"}
            try:
                jobs = self.app.store.jobs(include_closed=include_closed)
            except Exception:
                log.exception("could not load dashboard jobs")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "could not load jobs")
                return
            self._json({"jobs": jobs, "refreshed_at": dt.datetime.now(dt.timezone.utc)})
            return
        if parsed.path == "/healthz":
            ok = self.app.store.healthy()
            self._json({"ok": ok}, HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        prefix = "/api/jobs/"
        if not parsed.path.startswith(prefix):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._same_origin():
            self._error(HTTPStatus.FORBIDDEN, "cross-origin update refused")
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
            saved = self.app.store.save(key, status, notes)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not save application state")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "could not save application state")
            return
        if not saved:
            self._error(HTTPStatus.NOT_FOUND, "job not found")
            return
        self._json({"ok": True, "status": status, "notes": notes})


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, profile):
        super().__init__(address, DashboardHandler)
        self.store = store
        self.profile = profile


def serve(
    dsn: str | None,
    host: str = "0.0.0.0",
    port: int = 8080,
    profile_path: str | os.PathLike | None = None,
    demo: bool = False,
) -> None:
    profile = load_profile(profile_path)
    store = DemoStore() if demo else PostgresStore(dsn or "")
    server = DashboardServer((host, port), store, profile)
    log.info("application workspace listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
