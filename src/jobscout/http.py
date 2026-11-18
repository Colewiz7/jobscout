"""HTTP with a retry window sized for the Cilium identity race.

A pod gets roughly a minute of EACCES on egress while Cilium settles its
identity, and this job's first act after starting is an outbound fetch. So
connection-level failures retry for longer than that window before we give up,
rather than failing the whole run on a cold start.
"""
from __future__ import annotations

import logging
import ssl
import time

import certifi
import httpx

from .models import is_signin_url

log = logging.getLogger(__name__)

CONNECT_RETRY_SECONDS = 90
BACKOFF = (2, 4, 8, 15, 25, 40)
USER_AGENT = "jobscout (+https://github.com/Colewiz7/jobscout)"

RETRY_STATUS = {429, 500, 502, 503, 504}

# A 429 means the host wants us to slow down, and a 90 second retry loop only
# sends it more traffic. One short retry, then give up and let the caller skip.
RATE_LIMIT_RETRIES = 1
RATE_LIMIT_MAX_WAIT = 10
RATE_LIMIT_DEFAULT_WAIT = 5


def _retry_after(response: httpx.Response) -> float:
    """Seconds to wait after a 429, from Retry-After when it is a plain number."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return RATE_LIMIT_DEFAULT_WAIT
    return min(max(seconds, 1), RATE_LIMIT_MAX_WAIT)


class SignInRefused(Exception):
    pass


def _refuse_signin(request: httpx.Request) -> None:
    # also catches redirect hops
    if is_signin_url(str(request.url)):
        raise SignInRefused(request.url.host)


class _NoALPNContext(ssl.SSLContext):
    """A normal verifying TLS context that never advertises ALPN.

    httpx always offers ALPN, and Workday's bot protection answers that
    handshake from this host with a 429 on nearly every request, http/1.1 or
    h2 alike. The same request without ALPN, which is what Python's own
    urllib sends, gets a 200 every time. HTTP/1.1 is all this client speaks
    anyway, so there is nothing to negotiate.
    """

    def set_alpn_protocols(self, protocols) -> None:
        return None


def _tls_context() -> ssl.SSLContext:
    context = _NoALPNContext(ssl.PROTOCOL_TLS_CLIENT)  # verifies host and chain
    context.load_verify_locations(certifi.where())
    return context


class Fetcher:
    def __init__(self, timeout: float = 25.0, retry_seconds: float = CONNECT_RETRY_SECONDS, transport=None):
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [_refuse_signin]},
            transport=transport,
            verify=_tls_context(),
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

    def send(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Any method, keeping a definitive HTTP error. The ATS adapters use it
        to tell a 404 (posting gone) from a 403 (host blocked us)."""
        return self._request(method, url, return_errors=True, **kwargs)

    def _request(
        self, method: str, url: str, *, return_errors: bool = False, **kwargs
    ) -> httpx.Response | None:
        deadline = time.monotonic() + self._retry_seconds
        attempt = 0
        last: Exception | None = None
        rate_limited = 0
        # not 403, that reads as a host block
        refused = httpx.Response(451, request=httpx.Request("GET", "https://refused.invalid/")) if return_errors else None
        if is_signin_url(url):
            log.info("refusing sign-in url")
            return refused
        while True:
            wait: float | None = None
            try:
                response = self._client.request(method, url, **kwargs)
                if response.status_code in RETRY_STATUS:
                    last = httpx.HTTPStatusError(
                        f"{response.status_code}", request=response.request, response=response
                    )
                    if response.status_code == 429:
                        rate_limited += 1
                        if rate_limited > RATE_LIMIT_RETRIES:
                            raise RuntimeError(f"rate limited by {url}") from last
                        wait = _retry_after(response)
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
            delay = wait if wait is not None else BACKOFF[min(attempt, len(BACKOFF) - 1)]
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
