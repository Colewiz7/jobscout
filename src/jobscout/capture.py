"""Safe, deterministic capture of individual job posting URLs.

Automatic fetching is deliberately limited to fixed public ATS API hosts. An
unknown URL is still useful, but it must arrive with manual company and title
fields so this endpoint never becomes a general-purpose SSRF primitive.
"""
from __future__ import annotations

import dataclasses
import re
import urllib.parse

from .descriptions import ParsedDescription, parse_description
from .models import Posting, provider_id_from_url


@dataclasses.dataclass(frozen=True)
class CapturedPosting:
    posting: Posting
    description: ParsedDescription | None = None


_GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
_WORKDAY_HOST = re.compile(
    r"^(?P<tenant>[a-z0-9-]+)\.(?P<dc>[a-z0-9-]+)\.myworkdayjobs\.com$",
    re.I,
)


def clean_url(value: object) -> tuple[str, urllib.parse.SplitResult]:
    url = str(value or "").strip()
    if not url or len(url) > 4_096:
        raise ValueError("Paste a posting URL under 4,096 characters")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Posting URL must be a public HTTPS address")
    cleaned = urllib.parse.urlunsplit(
        ("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
    )
    return cleaned, urllib.parse.urlsplit(cleaned)


def _parts(parsed: urllib.parse.SplitResult) -> list[str]:
    return [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]


def _display_slug(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", value)).strip().title()


def manual_capture(payload: dict, url: str) -> CapturedPosting:
    title = str(payload.get("title") or "").strip()
    company = str(payload.get("company") or "").strip()
    if not title or not company:
        raise ValueError("This site needs a company and role title before it can be added")
    if len(title) > 500 or len(company) > 500:
        raise ValueError("Company and role title must be under 500 characters")
    location = str(payload.get("location") or "").strip()[:1_000]
    terms = str(payload.get("terms") or "").strip()[:500]
    return CapturedPosting(Posting(
        source="manual",
        company=company,
        title=title,
        location=location,
        terms=terms,
        url=url,
        remote=bool(payload.get("remote")) or "remote" in location.casefold(),
        provider_id=provider_id_from_url(url),
    ))


class PostingCapture:
    """Resolve supported ATS URLs through their provider-owned APIs."""

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def capture(self, payload: dict) -> CapturedPosting:
        url, parsed = clean_url(payload.get("url"))
        host = (parsed.hostname or "").casefold()
        parts = _parts(parsed)
        override = any(str(payload.get(key) or "").strip() for key in ("company", "title"))

        try:
            if host in _GREENHOUSE_HOSTS:
                captured = self._greenhouse(url, parts)
            elif host == "jobs.lever.co":
                captured = self._lever(url, parts)
            elif host == "jobs.ashbyhq.com":
                captured = self._ashby(url, parts)
            elif _WORKDAY_HOST.match(host):
                captured = self._workday(url, parsed, parts)
            else:
                return manual_capture(payload, url)
        except (KeyError, TypeError, ValueError):
            if not override:
                raise ValueError(
                    "The provider did not return enough details. Add the company and role manually."
                ) from None
            return manual_capture(payload, url)

        if override:
            posting = dataclasses.replace(
                captured.posting,
                company=str(payload.get("company") or captured.posting.company).strip(),
                title=str(payload.get("title") or captured.posting.title).strip(),
                location=str(payload.get("location") or captured.posting.location).strip(),
                terms=str(payload.get("terms") or captured.posting.terms).strip(),
                remote=bool(payload.get("remote")) or captured.posting.remote,
            )
            return dataclasses.replace(captured, posting=posting)
        return captured

    def _greenhouse(self, url: str, parts: list[str]) -> CapturedPosting:
        if len(parts) < 3 or parts[1] != "jobs" or not parts[2].isdigit():
            raise ValueError("unsupported Greenhouse URL")
        board, job_id = parts[0], parts[2]
        payload = self.fetcher.get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
        )
        if not isinstance(payload, dict):
            raise ValueError("Greenhouse posting unavailable")
        raw = str(payload.get("content") or "")
        return CapturedPosting(
            Posting(
                source="greenhouse", board=board, company=_display_slug(board),
                title=str(payload["title"]).strip(),
                location=str((payload.get("location") or {}).get("name") or "").strip(),
                url=str(payload.get("absolute_url") or url),
                remote="remote" in str((payload.get("location") or {}).get("name") or "").casefold(),
                provider_id=f"greenhouse:{job_id}",
            ),
            parse_description(raw, provider_deadline=payload.get("application_deadline")) if raw else None,
        )

    def _lever(self, url: str, parts: list[str]) -> CapturedPosting:
        if len(parts) < 2:
            raise ValueError("unsupported Lever URL")
        board, job_id = parts[0], parts[1]
        payload = self.fetcher.get_json(f"https://api.lever.co/v0/postings/{board}/{job_id}")
        if not isinstance(payload, dict):
            raise ValueError("Lever posting unavailable")
        categories = payload.get("categories") or {}
        location = str(categories.get("location") or "").strip()
        raw = str(payload.get("description") or payload.get("descriptionPlain") or "")
        return CapturedPosting(
            Posting(
                source="lever", board=board, company=_display_slug(board),
                title=str(payload["text"]).strip(), location=location,
                url=str(payload.get("hostedUrl") or url), remote="remote" in location.casefold(),
                provider_id=f"lever:{job_id}",
            ),
            parse_description(raw) if raw else None,
        )

    def _ashby(self, url: str, parts: list[str]) -> CapturedPosting:
        if len(parts) < 2:
            raise ValueError("unsupported Ashby URL")
        board, job_id = parts[0], parts[1]
        payload = self.fetcher.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        rows = payload.get("jobs", []) if isinstance(payload, dict) else []
        row = next((item for item in rows if str(item.get("id")) == job_id), None)
        if not isinstance(row, dict):
            raise ValueError("Ashby posting unavailable")
        location = str(row.get("location") or "").strip()
        raw = str(row.get("descriptionHtml") or "")
        return CapturedPosting(
            Posting(
                source="ashby", board=board, company=_display_slug(board),
                title=str(row["title"]).strip(), location=location,
                url=str(row.get("jobUrl") or row.get("applyUrl") or url),
                remote=bool(row.get("isRemote")) or "remote" in location.casefold(),
                provider_id=f"ashby:{job_id}",
            ),
            parse_description(raw) if raw else None,
        )

    def _workday(
        self, url: str, parsed: urllib.parse.SplitResult, parts: list[str]
    ) -> CapturedPosting:
        match = _WORKDAY_HOST.match(parsed.hostname or "")
        if match is None or len(parts) < 3 or parts[1] != "job":
            raise ValueError("unsupported Workday URL")
        tenant, dc = match.group("tenant"), match.group("dc")
        site = parts[0]
        path = "/" + "/".join(parts[1:])
        payload = self.fetcher.get_json(
            f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"
        )
        info = payload.get("jobPostingInfo", {}) if isinstance(payload, dict) else {}
        location = str(info.get("location") or "").strip()
        extra = [str(item).strip() for item in info.get("additionalLocations", []) if item]
        locations = "; ".join(dict.fromkeys([item for item in [location, *extra] if item]))
        raw = str(info.get("jobDescription") or "")
        return CapturedPosting(
            Posting(
                source="workday", board=f"{tenant}/{dc}/{site}",
                company=_display_slug(tenant), title=str(info["title"]).strip(),
                location=locations, url=url, remote="remote" in locations.casefold(),
                provider_id=f"workday:{tenant}:{path}",
            ),
            parse_description(raw) if raw else None,
        )
