"""Derive board slugs from the Simplify READMEs, then prove each one answers.

Apply URLs give the slug away for the three providers we poll. Locked rows are
included on purpose: a company whose Summer 2027 posting has closed still has a
live board worth watching for the next opening.

Roughly 245 distinct slugs come out of the two files today. The probe is what
makes the list usable, since a slug lifted from a URL is not always the slug the
API answers to.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .sources import boards as boards_api
from .sources import simplify

log = logging.getLogger(__name__)

SLUG_PATTERNS = {
    "greenhouse": re.compile(r"https?://(?:job-)?boards?\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    "lever": re.compile(r"https?://jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    "ashby": re.compile(r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"),
}


def extract(html: str) -> dict[str, dict[str, str]]:
    """Returns {provider: {slug: company}} from every apply URL in the tables."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict[str, str]] = {p: {} for p in SLUG_PATTERNS}
    company = ""
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        raw = re.sub(r"\s+", " ", cells[0].get_text(strip=True))
        if raw and simplify._CONTINUATION not in raw:
            company = simplify._EMOJI_PREFIX.sub("", raw).strip()
        for anchor in row.find_all("a", href=True):
            href = anchor["href"]
            if "simplify.jobs" in href:
                continue
            for provider, pattern in SLUG_PATTERNS.items():
                match = pattern.search(href)
                if match:
                    # First company to claim a slug keeps it; later duplicates
                    # are the same employer under a continuation arrow.
                    found[provider].setdefault(match.group(1), company or match.group(1))
    return found


def probe(fetcher, provider: str, slug: str) -> bool:
    """True when the provider's API actually answers for this slug."""
    template = boards_api.BOARD_TEMPLATES[provider]
    payload = fetcher.get_json(template.format(slug=slug))
    if payload is None:
        return False
    rows = payload.get("jobs") if isinstance(payload, dict) else payload
    return rows is not None


def discover(fetcher) -> dict[str, list[dict[str, str]]]:
    candidates: dict[str, dict[str, str]] = {p: {} for p in SLUG_PATTERNS}
    for source, url in simplify.FILES.items():
        body = fetcher.get_text(url)
        if body is None:
            log.error("%s unavailable, skipping", source)
            continue
        for provider, slugs in extract(body).items():
            for slug, company in slugs.items():
                candidates[provider].setdefault(slug, company)

    result: dict[str, list[dict[str, str]]] = {}
    for provider, slugs in candidates.items():
        live = []
        for slug, company in sorted(slugs.items(), key=lambda kv: kv[0].lower()):
            if probe(fetcher, provider, slug):
                live.append({"slug": slug, "company": company})
            else:
                log.info("%s/%s did not answer, dropping", provider, slug)
        log.info("%s: %d/%d slugs answered", provider, len(live), len(slugs))
        result[provider] = live
    return result
