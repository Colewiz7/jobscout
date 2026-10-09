"""Provider detail fetches and deterministic posting extraction.

Description reads are deliberately separate from board discovery. The Inbox
loads one on first open and caches it; saved and queued jobs can prefetch the
same path. No model-generated fields are produced here.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import html
import json
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


class DescriptionAccessBlocked(Exception):
    """The posting host rejected a public detail request."""


_HEADINGS = {
    "about": ("about", "about us", "about the company", "about our company", "about the team", "who we are", "our company"),
    "role": ("the role", "about the role", "the opportunity", "job description", "position overview", "position summary", "role overview", "your team, your impact", "overview of department"),
    "responsibilities": ("responsibilities", "what you'll do", "what you will do", "your impact", "what you will be doing", "what you'll be doing", "duties", "in this role", "your responsibilities"),
    "requirements": ("requirements", "qualifications", "minimum qualifications", "required qualifications", "competencies", "what you bring", "what we're looking for", "what we are looking for", "who we are looking for", "what you'll need", "what you will need", "who you are", "required skills"),
    "nice_to_have": ("nice to have", "preferred qualifications", "bonus", "preferred", "desired", "desired qualifications", "nice-to-have"),
    "benefits": ("benefits", "perks", "what we offer", "what you can expect", "compensation and benefits", "what's in it for you", "what is in it for you", "pay and benefits"),
    "logistics": ("please note", "work authorization", "employment eligibility", "location", "work location", "salary", "compensation", "pay range", "application process"),
}
_HEADING_LOOKUP = {label: key for key, labels in _HEADINGS.items() for label in labels}
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

_PAY_AMOUNT = re.compile(r"\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?[kK]?(?!\w)")
_PAY_CONTEXT = re.compile(
    r"\b(?:pay|salary|wage|compensation|hourly|stipend|per hour|per week|per year|annually)\b|/\s*(?:hr|hour|week|year)\b",
    re.I,
)
_PAY_RANGE_JOIN = re.compile(r"^\s*(?:-|–|—|to\b|through\b|,?\s*(?:maximum|max)\b)", re.I)
_PAY_HEADING = re.compile(
    r"(?:pay|salary|wages?|compensation|hourly)(?:\s+(?:range(?:\(s\))?|rate|information))?\s*:?\s*",
    re.I,
)
_NON_BASE_PAY = r"(?:housing|relocation|meal|wellness|equipment|signing|sign-on|tuition|commuter)\s+(?:stipend|bonus|assistance|allowance|reimbursement)"


def extract_pay(text: str | None) -> str | None:
    """Return only compensation explicitly stated in the posting, never an estimate.

    A missing unit stays missing: a dollar range alone does not establish an
    hourly or annual rate. Benefit stipends are not presented as base pay.
    """
    if not text:
        return None
    candidates: list[tuple[int, int, str]] = []
    previous = ""
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        amounts = list(_PAY_AMOUNT.finditer(line))
        if not amounts:
            previous = line
            continue
        for index, amount in enumerate(amounts):
            heading = previous if _PAY_HEADING.fullmatch(previous) else ""
            nearby = f"{heading} {line[max(0, amount.start() - 100):amount.end() + 100]}"
            before = line[max(0, amount.start() - 45):amount.start()]
            after = line[amount.end():amount.end() + 30]
            is_benefit = bool(
                re.search(_NON_BASE_PAY + r"(?:\s+(?:of|up to))?\s*$", before, re.I)
                or re.match(r"^\s*(?:for\s+)?" + _NON_BASE_PAY + r"\b", after, re.I)
            )
            if not _PAY_CONTEXT.search(nearby) or is_benefit:
                continue
            following = amounts[index + 1] if index + 1 < len(amounts) else None
            is_range = bool(
                following and following.start() - amount.end() <= 45
                and _PAY_RANGE_JOIN.search(line[amount.end():following.start()])
            )
            value = amount.group().replace(".00", "")
            if is_range:
                value += "–" + following.group().replace(".00", "")
            unit_context = line[max(0, amount.start() - 60):
                                (following.end() if is_range else amount.end()) + 100]
            if re.search(r"\b(?:per hour|hourly)\b|/\s*(?:hr|hour)\b", unit_context, re.I):
                suffix, priority = "/hr", 4
            elif re.search(r"\bper week\b|/\s*week\b", unit_context, re.I):
                suffix, priority = "/week", 3
            elif re.search(r"\b(?:per year|annually|annual)\b|/\s*year\b", unit_context, re.I):
                suffix, priority = "/yr", 2
            else:
                suffix, priority = "", 1
            candidates.append((priority, -len(candidates), value + suffix))
        previous = line
    return max(candidates)[2] if candidates else None


def _plain_text(raw_html: str) -> str:
    # Greenhouse commonly returns entity-escaped markup; unescape before
    # parsing so tags never leak into the reading pane.
    soup = BeautifulSoup(html.unescape(raw_html or ""), "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    block_names = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "div", "section"}
    lines = []
    for node in soup.find_all(block_names):
        if node.find(block_names):
            continue
        value = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if value and (not lines or lines[-1] != value):
            lines.append(value)
    if not lines:
        lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    return "\n".join(lines)


def _section_key(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value).strip(" :\u2013\u2014?").casefold().replace("\u2019", "'")
    if re.fullmatch(r"(?:qualifications?\s*[/ :]\s*(?:requirements?|required)|requirements?\s*[/ :]\s*qualifications?)", normalized):
        return "requirements"
    if normalized in _HEADING_LOOKUP:
        return _HEADING_LOOKUP[normalized]
    if len(normalized) > 85 or normalized.endswith((".", "!", "?")):
        return None
    for label, key in sorted(_HEADING_LOOKUP.items(), key=lambda item: -len(item[0])):
        if key == "logistics":
            continue
        if normalized.startswith(label + " ") and len(normalized) <= len(label) + 32:
            return key
    # Real ATS postings often use bespoke question headings. Treat a short,
    # standalone What/Who line as a divider, never as body prose with a period.
    if len(normalized) <= 80 and re.match(r"^(?:what|who)\b", normalized):
        if re.search(r"\b(?:need|require|bring|looking for|qualif|eligible)\b", normalized):
            return "requirements"
        if re.search(r"\b(?:offer|benefit|perk|in it for you)\b", normalized):
            return "benefits"
        if normalized.startswith("who we") or normalized.startswith("who are we"):
            return "requirements" if "looking for" in normalized else "about"
        if normalized.startswith("who you are") or normalized.startswith("who are you"):
            return "requirements"
        return "responsibilities" if normalized.startswith("what") else "role"
    return None


def parse_sections(raw_html: str) -> tuple[dict[str, str], ...]:
    """Keep provider section order and wording; classify headings conservatively."""
    lines = _plain_text(raw_html).splitlines()
    sections: list[dict[str, str]] = []
    current_key = "about"
    current_title = "Overview"
    body: list[str] = []

    def flush() -> None:
        if body:
            sections.append({"key": current_key, "title": current_title, "text": "\n".join(body)})
            body.clear()

    for line in lines:
        # ATS HTML sometimes appends "Responsibilities:" to the end of an
        # intro paragraph. Split only explicit, known labels at sentence edges.
        pieces = re.split(
            r"(?<=[.!?])\s+(?=(?:Responsibilities|Qualifications\s*:\s*Required|Required|Desired|Preferred)\s*:)",
            line.strip(), flags=re.I,
        )
        for value in pieces:
            value = value.strip()
            if not value:
                continue
            heading = _section_key(value)
            if heading:
                flush()
                current_key = heading
                current_title = value.rstrip(" :\u2013\u2014")
                continue
            if not body or body[-1] != value:
                body.append(value)
    flush()
    return tuple(sections)


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


_EMBEDDED_GREENHOUSE = {
    "careers.withwaymo.com": "waymo",
    "www.zipline.com": "flyzipline",
}
_JSONLD_HOSTS = {"careers.amd.com", "careers.excellusbcbs.com"}
_AMAZON_HOSTS = {"amazon.jobs", "www.amazon.jobs"}
_HTML_CONTENT_SELECTORS = {"careers.acuityinc.com": ".joblayouttoken"}


def _jobposting_description(raw_html: str) -> str | None:
    soup = BeautifulSoup(raw_html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                kinds = value.get("@type")
                if "JobPosting" in ([kinds] if isinstance(kinds, str) else kinds or []):
                    description = value.get("description")
                    if isinstance(description, str) and len(description.strip()) >= 80:
                        return description[:200_000]
                pending.extend(value.get("@graph", []))
    return None


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
            if hasattr(self.fetcher, "probe"):
                response = self.fetcher.probe(url)
                if response.status_code in {401, 403}:
                    raise DescriptionAccessBlocked("The posting host blocked automated access to its description. Open the original posting to read it.")
                try:
                    payload = response.json() if response.is_success else None
                except ValueError:
                    payload = None
            else:
                payload = self.fetcher.get_json(url)
            self._last_request[provider] = time.monotonic()
            return payload

    def _get_html(self, provider: str, url: str) -> str | None:
        with self._lock:
            remaining = self.min_interval - (time.monotonic() - self._last_request.get(provider, 0))
            if remaining > 0:
                time.sleep(remaining)
            if hasattr(self.fetcher, "probe"):
                response = self.fetcher.probe(url)
                if response.status_code in {401, 403}:
                    raise DescriptionAccessBlocked("The posting host blocked automated access to its description. Open the original posting to read it.")
                content = response.text if response.is_success else None
            else:
                content = self.fetcher.get_text(url)
            self._last_request[provider] = time.monotonic()
            return content

    def fetch(self, target: dict) -> ParsedDescription | None:
        key = str(target.get("dedupe_key") or "")
        provider, _, provider_id = key.partition(":")
        url = str(target.get("url") or "")
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").casefold()
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        workday_spec = _workday_spec_from_url(url)
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
            provider, provider_id = "greenhouse", parts[2]
            target = {**target, "board": parts[0]}
        elif host in _EMBEDDED_GREENHOUSE and urllib.parse.parse_qs(parsed.query).get("gh_jid"):
            provider, provider_id = "greenhouse", urllib.parse.parse_qs(parsed.query)["gh_jid"][0]
            target = {**target, "board": _EMBEDDED_GREENHOUSE[host]}
        elif host == "jobs.lever.co" and len(parts) >= 2:
            provider, provider_id = "lever", parts[1]
            target = {**target, "board": parts[0]}
        elif host == "jobs.ashbyhq.com" and len(parts) >= 2:
            provider, provider_id = "ashby", parts[1]
            target = {**target, "board": parts[0]}
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
        if host == "jobs.smartrecruiters.com" and len(parts) >= 2:
            company, posting_id = parts[:2]
            if re.fullmatch(r"[A-Za-z0-9_-]+", company) and re.fullmatch(r"[A-Za-z0-9-]+", posting_id):
                payload = self._get_json("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}")
                sections = ((payload or {}).get("jobAd") or {}).get("sections") or {}
                raw = "".join(
                    f"<h2>{html.escape(str(section.get('title') or label))}</h2>{section.get('text') or ''}"
                    for key_name, label in (("companyDescription", "About the company"), ("jobDescription", "The role"), ("qualifications", "Requirements"), ("additionalInformation", "Additional information"))
                    if isinstance(section := sections.get(key_name), dict) and section.get("text")
                )
                if raw:
                    return parse_description(raw)
        if host in _JSONLD_HOSTS or host in _AMAZON_HOSTS or host in _HTML_CONTENT_SELECTORS:
            page = self._get_html(host, url)
            if page:
                raw = _jobposting_description(page)
                if not raw and host in _AMAZON_HOSTS:
                    soup = BeautifulSoup(page, "html.parser")
                    content = soup.select_one("#job-detail-body .content")
                    raw = str(content) if content and len(content.get_text(" ", strip=True)) >= 80 else None
                if not raw and host in _HTML_CONTENT_SELECTORS:
                    soup = BeautifulSoup(page, "html.parser")
                    content = soup.select_one(_HTML_CONTENT_SELECTORS[host])
                    raw = str(content) if content and len(content.get_text(" ", strip=True)) >= 80 else None
                if raw:
                    return parse_description(raw)
        board = target.get("board") or _slug_from_url(url, provider)
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
