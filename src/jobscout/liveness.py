"""Conservative posting-liveness checks for the apply queue."""
from __future__ import annotations

import dataclasses
import threading
import time
import urllib.parse

from .descriptions import _slug_from_url


@dataclasses.dataclass(frozen=True)
class LivenessResult:
    status: str
    evidence: str


class PostingLivenessChecker:
    """Prefer public ATS detail APIs and never call an ambiguous result closed."""

    def __init__(self, fetcher, min_interval: float = 0.25):
        self.fetcher = fetcher
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}

    def _probe(self, provider: str, url: str):
        with self._lock:
            remaining = self.min_interval - (time.monotonic() - self._last_request.get(provider, 0))
            if remaining > 0:
                time.sleep(remaining)
            response = self.fetcher.probe(url)
            self._last_request[provider] = time.monotonic()
            return response

    @staticmethod
    def _http_result(response, evidence: str) -> LivenessResult | None:
        if response.status_code in {404, 410}:
            return LivenessResult("closed", f"{evidence} returned {response.status_code}")
        if not response.is_success:
            return LivenessResult("unknown", f"{evidence} returned {response.status_code}")
        return None

    def check(self, target: dict) -> LivenessResult:
        key = str(target.get("dedupe_key") or "")
        provider, _, provider_id = key.partition(":")
        if provider == "workday":
            parts = key.split(":", 2)
            provider_id = parts[2] if len(parts) == 3 else ""
        url = str(target.get("url") or "")
        board = target.get("board") or _slug_from_url(url, provider)

        try:
            if provider == "greenhouse" and board and provider_id:
                endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{provider_id}"
                response = self._probe(provider, endpoint)
                result = self._http_result(response, "Greenhouse detail API")
                if result:
                    return result
                return LivenessResult("live", "Greenhouse detail API returned the posting")
            if provider == "lever" and board and provider_id:
                endpoint = f"https://api.lever.co/v0/postings/{board}/{provider_id}"
                response = self._probe(provider, endpoint)
                result = self._http_result(response, "Lever detail API")
                if result:
                    return result
                return LivenessResult("live", "Lever detail API returned the posting")
            if provider == "ashby" and board and provider_id:
                endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
                response = self._probe(provider, endpoint)
                result = self._http_result(response, "Ashby job board API")
                if result:
                    return result
                try:
                    jobs = response.json().get("jobs", [])
                except (AttributeError, ValueError):
                    return LivenessResult("unknown", "Ashby returned an unreadable job board response")
                if any(str(job.get("id")) == provider_id for job in jobs):
                    return LivenessResult("live", "Ashby job board API still lists the posting")
                return LivenessResult("closed", "Ashby job board API no longer lists the posting")
            if provider == "workday" and board and provider_id:
                spec = str(board).split("/")
                if len(spec) == 3 and provider_id.startswith("/"):
                    tenant, dc, site = spec
                    endpoint = (
                        f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/"
                        f"{tenant}/{site}{provider_id}"
                    )
                    response = self._probe(provider, endpoint)
                    result = self._http_result(response, "Workday detail API")
                    if result:
                        return result
                    return LivenessResult("live", "Workday detail API returned the posting")

            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return LivenessResult("closed", "No usable application URL remains")
            response = self._probe("other", url)
            result = self._http_result(response, "Application URL")
            if result:
                return result
            final = urllib.parse.urlsplit(str(response.url))
            if parsed.path not in {"", "/"} and final.path in {"", "/"}:
                return LivenessResult("unknown", "Application URL redirected to a site homepage")
            return LivenessResult("live", "Application URL responded")
        except Exception:
            return LivenessResult("unknown", "Liveness check could not reach the provider")
