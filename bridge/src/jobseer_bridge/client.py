import time
from urllib.parse import quote

import httpx

from .config import Config


class BridgeError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status: int, message: str, body: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body or {}


class Conflict(ApiError):
    # the server sends the current value beside the error
    @property
    def current(self) -> dict:
        return {key: value for key, value in self.body.items() if key != "error"}


def key_path(key: str) -> str:
    return quote(key, safe="")


class Client:
    def __init__(self, config: Config, transport: httpx.BaseTransport | None = None):
        self.config = config
        self._http = httpx.Client(
            base_url=config.url,
            headers=config.auth_headers(),
            timeout=config.timeout,
            transport=transport,
        )
        self._token: str | None = None
        self._session: dict | None = None
        self._jobs: dict[bool, tuple[float, dict]] = {}
        self.session_fetches = 0

    def close(self) -> None:
        self._http.close()

    # --- session ---

    def session(self, refresh: bool = False) -> dict:
        if refresh or self._session is None:
            reply = self._http.get("/api/v1/session")
            self._raise_for(reply)
            self.session_fetches += 1
            self._session = reply.json()
            self._token = self._session["csrf_token"]
        return self._session

    def features(self) -> dict:
        return self.session().get("features", {})

    # --- requests ---

    def get(self, path: str, **params) -> dict:
        params = {key: value for key, value in params.items() if value is not None}
        reply = self._http.get(path, params=params)
        self._raise_for(reply)
        return reply.json()

    def get_text(self, path: str, **params) -> str:
        params = {key: value for key, value in params.items() if value is not None}
        reply = self._http.get(path, params=params)
        self._raise_for(reply)
        return reply.text

    def send(self, method: str, path: str, body: dict | None = None) -> dict:
        self._jobs.clear()
        try:
            self.session()
            reply = self._write(method, path, body)
            # restart rotates the token
            # a loop would hide real denials
            if reply.status_code == 403:
                self.session(refresh=True)
                reply = self._write(method, path, body)
        finally:
            # a lost reply hides commits
            self._jobs.clear()
        self._raise_for(reply)
        return reply.json()

    def _write(self, method: str, path: str, body: dict | None) -> httpx.Response:
        headers = {"X-CSRF-Token": self._token or "", "Origin": self.config.origin}
        return self._http.request(method, path, json={} if body is None else body, headers=headers)

    def _raise_for(self, reply: httpx.Response) -> None:
        if reply.status_code < 400:
            return
        try:
            body = reply.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        message = str(body.get("error") or reply.reason_phrase or "request failed")
        if reply.status_code == 401:
            message = "not authenticated; check the Authentik settings"
        cls = Conflict if reply.status_code == 409 else ApiError
        raise cls(reply.status_code, message, body)

    # --- cached inbox ---

    def jobs(self, include_closed: bool = False) -> list[dict]:
        now = time.monotonic()
        hit = self._jobs.get(include_closed)
        if hit and now - hit[0] < self.config.cache_ttl:
            return hit[1]["jobs"]
        data = self.get("/api/v1/jobs", closed="1" if include_closed else None)
        self._jobs[include_closed] = (now, data)
        return data["jobs"]

    def job(self, key: str) -> dict | None:
        for closed in (False, True):
            for row in self.jobs(closed):
                if row["dedupe_key"] == key:
                    return row
        return None
