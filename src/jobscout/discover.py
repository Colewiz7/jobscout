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


_NOT_A_NAME = re.compile(
    r"(inc|llc|corp|corporation|company|group|technologies|technology|the|and|"
    r"global|labs?|ltd|plc|holdings)$"
)


def _normalise_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def name_matches(company: str, slug: str) -> bool:
    """Does this slug plausibly belong to this employer.

    A slug that answers is not a slug that belongs: "charles" is a live
    Greenhouse board for a German company, not Charles Schwab, and "general"
    is a board literally named General Interest, not General Dynamics. What
    separates those from a real hit is coverage. "charles" accounts for seven
    of the thirteen characters in "charlesschwab" while "datadog" accounts for
    all of "datadog", so a slug has to carry most of the employer's name
    rather than merely appear inside it.
    """
    slug_key = _normalise_name(slug)
    if not slug_key:
        return False
    base = company.split("/")[0].split("(")[0]
    keys = {_normalise_name(base)}
    keys.add(_NOT_A_NAME.sub("", _normalise_name(base)))
    for key in keys:
        if not key:
            continue
        if slug_key == key:
            return True
        shorter, longer = sorted((slug_key, key), key=len)
        if shorter in longer and len(shorter) / len(longer) >= 0.7:
            return True
    return False


def probe(fetcher, provider: str, slug: str, company: str | None = None) -> bool:
    """True when the provider's API answers and the board is the right employer.

    Answering is not enough on its own: a derived slug can land on a live board
    belonging to someone else entirely. When the employer is known, the slug
    has to look like their name as well.
    """
    if company is not None and not name_matches(company, slug):
        log.info("%s/%s does not look like %r, skipping", provider, slug, company)
        return False
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
            # No company here on purpose. These slugs were read out of real
            # apply URLs, so they are already the employer's own board however
            # little they resemble the name: Atoms posts at cssmerge and Axon
            # at axontalentcommunity. The name rule is for a slug somebody
            # guessed, not one the employer published.
            if probe(fetcher, provider, slug):
                live.append({"slug": slug, "company": company})
            else:
                log.info("%s/%s did not answer, dropping", provider, slug)
        log.info("%s: %d/%d slugs answered", provider, len(live), len(slugs))
        result[provider] = live
    return result
