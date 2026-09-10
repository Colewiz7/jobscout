"""Parse the SimplifyJobs README tables.

Three things about these files bite anyone who treats them as markdown:

  * They are HTML `<table>`s, not pipe tables.
  * The default branch is `dev`. `main` 404s.
  * The two files have different columns. README.md is
    Company/Role/Location/Application/Age; README-Off-Season.md inserts a
    Terms column before Application. So columns are resolved by reading the
    nearest preceding header row, never by position.
"""
from __future__ import annotations

import logging
import re
import urllib.parse

from bs4 import BeautifulSoup

from ..models import Posting, parse_age

log = logging.getLogger(__name__)

BASE = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev"
FILES = {
    "simplify-s27": f"{BASE}/README.md",
    "simplify-off": f"{BASE}/README-Off-Season.md",
}

# A locked row's Application cell is literally this, with no link and no
# "Closed" text anywhere in the row.
LOCK = "\U0001f512"
# Companies that were posted recently get a fire emoji glued to the name.
_EMOJI_PREFIX = re.compile(r"^[\U0001F000-\U0001FAFF☀-➿️\s]+")
_CONTINUATION = "↳"  # the arrow marking "same company as the row above"

# Simplify's own tracking params. gh_jid is NOT one of these: it carries the
# Greenhouse job id, which is the dedupe key for embedded boards.
_STRIP_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "ref"}


def clean_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k not in _STRIP_PARAMS
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), "")
    )


def _cell_text(cell) -> str:
    """Text of a cell, with <br> turned into a separator.

    Without this the two halves of a multi-location cell get welded together
    into "Schaumburg, ILPlantation, FL", which no location filter can read.
    """
    for br in cell.find_all("br"):
        br.replace_with("; ")
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def _apply_url(cell) -> str:
    for anchor in cell.find_all("a", href=True):
        href = anchor["href"]
        if "simplify.jobs/p/" in href:
            continue  # Simplify's own mirror of the posting, not the employer
        return clean_url(href)
    return ""


def parse(html: str, source: str) -> list[Posting]:
    soup = BeautifulSoup(html, "html.parser")
    postings: list[Posting] = []
    columns: dict[str, int] = {}
    company = ""

    for row in soup.find_all("tr"):
        headers = row.find_all("th")
        if headers:
            columns = {
                re.sub(r"\s+", " ", h.get_text(strip=True)).lower(): i
                for i, h in enumerate(headers)
            }
            continue
        cells = row.find_all("td")
        if not columns or len(cells) < len(columns):
            continue

        def col(name: str) -> str:
            index = columns.get(name)
            return _cell_text(cells[index]) if index is not None else ""

        raw_company = col("company")
        if raw_company and _CONTINUATION not in raw_company:
            company = _EMOJI_PREFIX.sub("", raw_company).strip()
        if not company:
            continue

        application_index = columns.get("application")
        application = cells[application_index] if application_index is not None else None
        closed = application is None or LOCK in application.get_text()
        url = "" if closed else _apply_url(application)
        if not closed and not url:
            continue

        postings.append(
            Posting(
                source=source,
                company=company,
                title=col("role"),
                location=col("location"),
                terms=col("terms"),
                url=url,
                closed=closed,
                age_days=parse_age(col("age")),
            )
        )
    return postings


def fetch(fetcher) -> dict[str, list[Posting]]:
    out: dict[str, list[Posting]] = {}
    for source, url in FILES.items():
        body = fetcher.get_text(url)
        if body is None:
            log.error("%s unavailable at %s", source, url)
            continue
        out[source] = parse(body, source)
        log.info("%s: %d rows", source, len(out[source]))
    return out
