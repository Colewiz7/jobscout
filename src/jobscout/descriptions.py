"""Provider detail fetches and deterministic posting extraction.

Description reads are deliberately separate from board discovery. The Inbox
loads one on first open and caches it; saved and queued jobs can prefetch the
same path. No model-generated fields are produced here.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import html
import re
import threading
import time
import urllib.parse

from bs4 import BeautifulSoup


@dataclasses.dataclass(frozen=True)
class ParsedDescription:
    html: str
    text: str
    sections: tuple[dict[str, str], ...]
    deadline: dt.date | None = None
    deadline_source: str | None = None


_HEADINGS = {
    "about": ("about", "overview", "the role", "the opportunity", "who we are"),
    "responsibilities": ("responsibilities", "what you'll do", "what you will do", "your impact"),
    "requirements": ("requirements", "qualifications", "what you bring", "what we're looking for"),
    "nice_to_have": ("nice to have", "preferred qualifications", "bonus", "preferred"),
    "benefits": ("benefits", "perks", "what we offer", "compensation and benefits"),
}
_HEADING_LOOKUP = {
    label.strip(" :").casefold(): key for key, labels in _HEADINGS.items() for label in labels
}
_DEADLINE_LABEL = re.compile(
    r"(?:application(?:s)?\s+(?:deadline|close(?:s|d)?)|deadline|apply\s+by|"
    r"applications?\s+(?:will\s+)?be\s+accepted\s+(?:through|until))"
    r"\s*[:\-]?\s*"
    r"(?P<date>"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
    re.I,
)


def _plain_text(raw_html: str) -> str:
    # Greenhouse commonly returns entity-escaped markup; unescape before
    # parsing so tags never leak into the reading pane.
    soup = BeautifulSoup(html.unescape(raw_html or ""), "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def _section_key(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value).strip(" :").casefold()
    if normalized in _HEADING_LOOKUP:
        return _HEADING_LOOKUP[normalized]
    for label, key in _HEADING_LOOKUP.items():
        if normalized.startswith(label) and len(normalized) <= len(label) + 2:
            return key
    return None


def parse_sections(raw_html: str) -> tuple[dict[str, str], ...]:
    """Split familiar posting sections without inventing content."""
    soup = BeautifulSoup(html.unescape(raw_html or ""), "html.parser")
    blocks: list[tuple[str | None, str]] = []
    current: str | None = "about"
    for node in soup.find_all(["h1", "h2", "h3", "h4", "strong", "p", "li"]):
        value = node.get_text(" ", strip=True)
        if not value:
            continue
        heading = _section_key(value)
        if heading and (node.name.startswith("h") or len(value) <= 48):
            current = heading
            continue
        # Avoid duplicating text nested inside a paragraph or list item.
        if node.name == "strong" and node.parent and node.parent.name in {"p", "li"}:
            continue
        blocks.append((current, value))

    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for key, value in blocks:
        key = key or "about"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        if not grouped[key] or grouped[key][-1] != value:
            grouped[key].append(value)
    return tuple({"key": key, "text": "\n".join(grouped[key])} for key in order if grouped[key])


def parse_deadline(text: str) -> dt.date | None:
    """Return only an explicitly labelled, fully dated deadline."""
    match = _DEADLINE_LABEL.search(text or "")
    if not match:
        return None
    value = match.group("date").replace(",", "")
    for pattern in ("%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def parse_description(
    raw_html: str, *, provider_deadline: str | None = None
) -> ParsedDescription:
    """Parse provider HTML without generating or guessing any posting facts."""
    text = _plain_text(raw_html)
    deadline = parse_deadline(text)
    source = "description" if deadline else None
    if deadline is None and provider_deadline:
        try:
            deadline = dt.datetime.fromisoformat(provider_deadline.replace("Z", "+00:00")).date()
            source = "provider"
        except ValueError:
            pass
    return ParsedDescription(raw_html, text, parse_sections(raw_html), deadline, source)


def _slug_from_url(url: str, provider: str) -> str | None:
    parsed = urllib.parse.urlsplit(url or "")
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if provider == "greenhouse" and len(parts) >= 1:
        return parts[0]
    if provider in {"lever", "ashby"} and len(parts) >= 1:
        return parts[0]
    return None


def _workday_spec_from_url(url: str) -> tuple[str, str] | None:
    """Return (tenant/dc/site, API job path) for a public Workday URL."""
    parsed = urllib.parse.urlsplit(url or "")
    host = (parsed.hostname or "").casefold()
    labels = host.split(".")
    if len(labels) < 4 or labels[-2:] != ["myworkdayjobs", "com"]:
        return None
    tenant, dc = labels[0], labels[1]
    parts = [part for part in parsed.path.split("/") if part]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts = parts[1:]
    if len(parts) < 3 or parts[1] != "job":
        return None
    site = parts[0]
    return f"{tenant}/{dc}/{site}", "/" + "/".join(parts[1:])


class ProviderDescriptionFetcher:
    """Small process-local rate gate around public ATS detail APIs."""

    def __init__(self, fetcher, min_interval: float = 0.25):
        self.fetcher = fetcher
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}

    def _get_json(self, provider: str, url: str):
        with self._lock:
            remaining = self.min_interval - (time.monotonic() - self._last_request.get(provider, 0))
            if remaining > 0:
                time.sleep(remaining)
            payload = self.fetcher.get_json(url)
            self._last_request[provider] = time.monotonic()
            return payload

    def fetch(self, target: dict) -> ParsedDescription | None:
        key = str(target.get("dedupe_key") or "")
        provider, _, provider_id = key.partition(":")
        workday_spec = _workday_spec_from_url(str(target.get("url") or ""))
        if provider not in {"greenhouse", "lever", "ashby", "workday"} and workday_spec:
            provider = "workday"
            target = {**target, "board": workday_spec[0]}
            provider_id = workday_spec[1]
        if provider == "workday":
            if workday_spec:
                target = {**target, "board": workday_spec[0]}
                provider_id = workday_spec[1]
            else:
                workday_parts = key.split(":", 2)
                provider_id = workday_parts[2] if len(workday_parts) == 3 else ""
        board = target.get("board") or _slug_from_url(str(target.get("url") or ""), provider)
        if not board or not provider_id:
            return None

        if provider == "greenhouse":
            payload = self._get_json(
                provider,
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{provider_id}",
            )
            if isinstance(payload, dict) and payload.get("content"):
                return parse_description(
                    str(payload["content"]),
                    provider_deadline=payload.get("application_deadline"),
                )
        elif provider == "lever":
            payload = self._get_json(
                provider,
                f"https://api.lever.co/v0/postings/{board}/{provider_id}",
            )
            if isinstance(payload, dict):
                raw = payload.get("description") or payload.get("descriptionPlain")
                if raw:
                    return parse_description(str(raw))
        elif provider == "ashby":
            payload = self._get_json(
                provider,
                f"https://api.ashbyhq.com/posting-api/job-board/{board}",
            )
            for posting in (payload or {}).get("jobs", []):
                if str(posting.get("id")) == provider_id and posting.get("descriptionHtml"):
                    return parse_description(str(posting["descriptionHtml"]))
        elif provider == "workday":
            spec = str(target.get("board") or "").split("/")
            path = provider_id
            if len(spec) == 3 and path.startswith("/"):
                tenant, dc, site = spec
                payload = self._get_json(
                    provider,
                    f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/"
                    f"{tenant}/{site}{path}",
                )
                info = (payload or {}).get("jobPostingInfo") or {}
                raw = info.get("jobDescription")
                if raw:
                    return parse_description(str(raw))
        return None
