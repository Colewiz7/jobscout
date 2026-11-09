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

from .models import is_signin_url

log = logging.getLogger(__name__)

CONNECT_RETRY_SECONDS = 90
BACKOFF = (2, 4, 8, 15, 25, 40)
USER_AGENT = "jobscout (+https://github.com/Colewiz7/jobscout)"

RETRY_STATUS = {429, 500, 502, 503, 504}


class SignInRefused(Exception):
    pass


def _refuse_signin(request: httpx.Request) -> None:
    # also catches redirect hops
    if is_signin_url(str(request.url)):
        raise SignInRefused(request.url.host)


class Fetcher:
    def __init__(self, timeout: float = 25.0, retry_seconds: float = CONNECT_RETRY_SECONDS, transport=None):
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [_refuse_signin]},
            transport=transport,
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

    def probe(self, url: str) -> httpx.Response:
        """Fetch while preserving a definitive HTTP error for liveness checks."""
        return self._request("GET", url, return_errors=True)

    def _request(
        self, method: str, url: str, *, return_errors: bool = False, **kwargs
    ) -> httpx.Response | None:
        deadline = time.monotonic() + self._retry_seconds
        attempt = 0
        last: Exception | None = None
        # not 403, that reads as a host block
        refused = httpx.Response(451, request=httpx.Request("GET", "https://refused.invalid/")) if return_errors else None
        if is_signin_url(url):
            log.info("refusing sign-in url")
            return refused
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
                    return response if return_errors else None
            except SignInRefused:
                log.info("refusing sign-in redirect")
                return refused
            except (httpx.InvalidURL, ValueError):
                # bad redirect target, nothing was sent
                log.info("refusing malformed url")
                return refused
            except httpx.HTTPError as exc:
                # a bad location never improves on retry
                if isinstance(exc, httpx.RemoteProtocolError) and "location header" in str(exc):
                    log.info("refusing malformed redirect")
                    return refused
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
