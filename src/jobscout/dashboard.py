"""Dependency-free HTTP boundary for the JobSeer application workspace."""
from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import mimetypes
import os
import pathlib
import secrets
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import yaml

from . import db as database

log = logging.getLogger("jobscout.dashboard")
STATIC_ROOT = pathlib.Path(__file__).with_name("static")
DEFAULT_PROFILE = pathlib.Path(__file__).resolve().parents[2] / "config" / "profile.yaml"
MAX_BODY = 64 * 1024
CSRF_COOKIE = "jobseer_csrf"
APP_ROUTE_ROOTS = frozenset({"inbox", "queue", "tracker", "companies", "profile"})


def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def load_profile(path: str | os.PathLike | None = None) -> dict:
    """Load the non-secret seed and overlay private scalar fields from env."""
    profile_path = pathlib.Path(path or os.environ.get("JOBSCOUT_PROFILE") or DEFAULT_PROFILE)
    raw = yaml.safe_load(profile_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError("profile YAML must contain a mapping")
    for field in ("name", "email", "phone", "location"):
        value = os.environ.get(f"JOBSCOUT_PROFILE_{field.upper()}")
        if value is not None:
            raw[field] = value
    return raw


class PostgresStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def jobs(self, include_closed: bool = False) -> list[dict]:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
            return database.dashboard_postings(conn, include_closed=include_closed)

    def save(self, key: str, status: str, notes: str) -> bool:
        with database.connect(self.dsn) as conn:
            database.require_schema(conn)
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
                "location": "New York, NY", "terms": "", "age_days": 3,
                "first_seen": now - dt.timedelta(days=3), "last_seen": now,
                "url": "https://example.com/apply", "sources": "greenhouse, simplify-s27",
                "closed": False, "notified": True, "score": 130,
                "score_detail": {"role_named": 100, "internship": 20, "fresh": 10},
                "status": "new", "notes": "", "application_updated_at": None,
            },
        ]

    def jobs(self, include_closed: bool = False) -> list[dict]:
        return [dict(row) for row in self.rows if include_closed or not row["closed"]]

    def save(self, key: str, status: str, notes: str) -> bool:
        if status not in database.APPLICATION_STATUSES:
            raise ValueError(f"unknown application status: {status}")
        for row in self.rows:
            if row["dedupe_key"] == key:
                row.update(
                    status=status,
                    notes=notes,
                    application_updated_at=dt.datetime.now(dt.timezone.utc),
                )
                return True
        return False

    def healthy(self) -> bool:
        return True


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
                    {"user": user, "csrf_token": self.app.csrf_token},
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
            saved = self.app.store.save(key, status, notes)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            log.exception("could not save application state")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Couldn't save the change. Retry shortly.")
            return
        if not saved:
            self._error(HTTPStatus.NOT_FOUND, "job not found")
            return
        self._json({"ok": True, "status": status, "notes": notes})


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
    ):
        super().__init__(address, DashboardHandler)
        self.store = store
        self.require_auth = require_auth
        self.authentik_app = authentik_app
        self.authentik_user = authentik_user
        self.csrf_token = secrets.token_urlsafe(32)


def serve(
    dsn: str | None,
    host: str = "0.0.0.0",
    port: int = 8080,
    profile_path: str | os.PathLike | None = None,
    demo: bool = False,
) -> None:
    # Kept for CLI compatibility. Phase 1 deliberately does not load or expose
    # structured personal data until the Authentik path is deployment-verified.
    del profile_path
    store = DemoStore() if demo else PostgresStore(dsn or "")
    server = DashboardServer(
        (host, port),
        store,
        require_auth=not demo,
        authentik_app=os.environ.get("JOBSCOUT_AUTHENTIK_APP", "jobseer"),
        authentik_user=os.environ.get("JOBSCOUT_AUTHENTIK_USER"),
    )
    log.info("application workspace listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
