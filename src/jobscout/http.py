"""HTTP with a retry window sized for the Cilium identity race.

A pod gets roughly a minute of EACCES on egress while Cilium settles its
identity, and this job's first act after starting is an outbound fetch. So
connection-level failures retry for longer than that window before we give up,
rather than failing the whole run on a cold start.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

CONNECT_RETRY_SECONDS = 90
BACKOFF = (2, 4, 8, 15, 25, 40)
USER_AGENT = "jobscout (+https://github.com/Colewiz7/jobscout)"

RETRY_STATUS = {429, 500, 502, 503, 504}


class Fetcher:
    def __init__(self, timeout: float = 25.0, retry_seconds: float = CONNECT_RETRY_SECONDS):
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._retry_seconds = retry_seconds

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    def post(self, url: str, content: bytes, headers: dict) -> httpx.Response | None:
        """POST with the same retry window. Used for ntfy pushes."""
        return self._request("POST", url, content=content, headers=headers)

    def get(self, url: str) -> httpx.Response | None:
        """Fetch a URL. Returns None for a definitive 4xx that is not worth retrying."""
        return self._request("GET", url)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response | None:
        deadline = time.monotonic() + self._retry_seconds
        attempt = 0
        last: Exception | None = None
        while True:
            try:
                response = self._client.request(method, url, **kwargs)
                if response.status_code in RETRY_STATUS:
                    last = httpx.HTTPStatusError(
                        f"{response.status_code}", request=response.request, response=response
                    )
                elif response.is_success:
                    return response
                else:
                    # 404 on a board slug is normal: the slug is wrong or the
                    # board is gone. Callers log and move on.
                    log.debug("%s -> %s", url, response.status_code)
                    return None
            except httpx.HTTPError as exc:
                last = exc

            if time.monotonic() >= deadline:
                raise RuntimeError(f"giving up on {url} after {self._retry_seconds}s") from last
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            log.warning("retrying %s in %ss (%s)", url, delay, last)
            time.sleep(delay)
            attempt += 1

    def get_text(self, url: str) -> str | None:
        response = self.get(url)
        return response.text if response is not None else None

    def get_json(self, url: str):
        response = self.get(url)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError:
            log.warning("non-JSON body from %s", url)
            return None
