"""Company career sites read from their jobs sitemap and JobPosting markup.

Some employers run a career site whose search is closed to crawlers but whose
jobs are not: Gulfstream's robots.txt disallows `?q=` and every other search
parameter (and Cloudflare answers those with 403), yet allows everything else
and publishes a jobs sitemap. Each job page carries schema.org JobPosting, so
the whole site is readable within its own rules:

- detail is the job page's JobPosting; a missing job answers 404;
- search reads the jobs sitemap once, keeps the URLs whose slug names one of
  the search terms, and opens only those, capped per run.

A site here is listed by hand in SITES, so only those hosts ever match.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import re
import time
import urllib.parse

from bs4 import BeautifulSoup

from ..models import Posting
from . import Detail, Gone, Ref, Unavailable, fetch_text

log = logging.getLogger(__name__)

NAME = "careersite"
MAX_DETAILS = 40     # job pages opened per board per run
PAUSE = 0.5          # between job pages; this is somebody's careers site


@dataclasses.dataclass(frozen=True)
class Site:
    host: str
    company: str
    sitemap: str                 # the jobs sitemap, not the index
    path: re.Pattern             # a job page path; group 1 is the id, group 2 the slug


# Checked live 2026-11-18: robots.txt allows the sitemap and job pages, the
# sitemap lists every open job, and job pages carry JobPosting.
SITES: dict[str, Site] = {
    site.host: site
    for site in (
        Site(
            host="careers.gulfstream.com",
            company="Gulfstream",
            sitemap="https://careers.gulfstream.com/en/jobs/sitemap.xml",
            path=re.compile(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?jobs/(\d{3,12})(?:/([a-z0-9-]*))?/?"),
        ),
    )
}

_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
_WORD = re.compile(r"[a-z0-9]+")


def _site(board: str) -> Site | None:
    return SITES.get((board or "").strip().casefold())


def _ref(site: Site, job_id: str, slug: str | None) -> Ref:
    url = f"https://{site.host}/en/jobs/{job_id}/{slug}/" if slug else f"https://{site.host}/en/jobs/{job_id}/"
    return Ref(provider=NAME, board=site.host, job_id=job_id, url=url)


def _job_posting(page: str) -> dict | None:
    soup = BeautifulSoup(page, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except ValueError:
            continue
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                kinds = value.get("@type")
                if "JobPosting" in ([kinds] if isinstance(kinds, str) else kinds or []):
                    return value
                pending.extend(value.get("@graph", []))
    return None


def _location(posting: dict) -> str:
    places = posting.get("jobLocation")
    places = [places] if isinstance(places, dict) else places or []
    cells = []
    for place in places:
        address = (place or {}).get("address") or {}
        cell = ", ".join(str(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k))
        cell = cell or str((place or {}).get("name") or "").strip()
        if cell:
            cells.append(cell)
    return "; ".join(dict.fromkeys(cells))


def _age_days(value) -> int | None:
    try:
        posted = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=datetime.timezone.utc)
    return max((datetime.datetime.now(datetime.timezone.utc) - posted).days, 0)


def _employment_type(posting: dict) -> str | None:
    kind = posting.get("employmentType")
    kinds = [kind] if isinstance(kind, str) else [str(k) for k in kind or []]
    kinds = [k.strip().upper().replace("-", "_").replace(" ", "_") for k in kinds if k]
    if "INTERN" in kinds:
        return "INTERN"
    return kinds[0] if kinds else None


def _slug_matches(slug: str, terms) -> bool:
    """A term matches when every word of it is a word of the slug. Two slug
    words joined count too, so "coop" finds "co-op", but "intern" never finds
    "international"."""
    words = _WORD.findall(slug.casefold())
    vocabulary = set(words) | {a + b for a, b in zip(words, words[1:])}
    for term in terms or ():
        wanted = [re.sub(r"[^a-z0-9]", "", w) for w in term.casefold().split()]
        if wanted and all(w and w in vocabulary for w in wanted):
            return True
    return False


class _CareerSite:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        if "@" in (parsed.netloc or ""):
            return None
        site = SITES.get((parsed.hostname or "").rstrip(".").casefold())
        if site is None:
            return None
        found = site.path.fullmatch(parsed.path or "")
        return _ref(site, found.group(1), found.group(2)) if found else None

    def detail(self, fetcher, ref: Ref) -> Detail:
        site = _site(ref.board)
        if site is None:
            raise Unavailable(f"not a known career site: {ref.board!r}")
        page = fetch_text(fetcher, ref.url, headers={"Accept": "text/html"})
        posting = _job_posting(page)
        if posting is None or not str(posting.get("title") or "").strip():
            raise Unavailable(f"{ref.url} has no JobPosting")
        closes = posting.get("validThrough")
        if closes:
            try:
                deadline = datetime.datetime.fromisoformat(str(closes).replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=datetime.timezone.utc)
                if deadline < datetime.datetime.now(datetime.timezone.utc):
                    raise Gone(f"{ref.url} closed {closes}")
            except ValueError:
                closes = None
        location = _location(posting)
        organisation = (posting.get("hiringOrganization") or {}).get("name") if isinstance(posting.get("hiringOrganization"), dict) else None
        return Detail(
            ref=ref,
            company=str(organisation or site.company).strip(),
            title=str(posting["title"]).strip(),
            location=location,
            description_html=str(posting.get("description") or "") or None,
            remote=str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE",
            employment_type=_employment_type(posting),
            deadline=str(closes) if closes else None,
            age_days=_age_days(posting.get("datePosted")),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        site = _site(board)
        if site is None or not terms:
            log.warning("careersite board %r is not configured or has no terms, skipping", board)
            return None
        try:
            sitemap = fetch_text(fetcher, site.sitemap, headers={"Accept": "application/xml"})
        except (Gone, Unavailable) as error:
            # None, not []: a sitemap that did not answer must not retire jobs
            log.warning("careersite %s: sitemap failed (%s), treating the board as absent", board, error)
            return None
        refs = []
        for url in _LOC.findall(sitemap):
            found = site.path.fullmatch(urllib.parse.urlsplit(url).path)
            if found and _slug_matches(found.group(2) or "", terms):
                refs.append(_ref(site, found.group(1), found.group(2)))
        log.info("careersite %s: %d jobs match a term, opening %d", board, len(refs), min(len(refs), MAX_DETAILS))
        out = []
        for index, ref in enumerate(refs[:MAX_DETAILS]):
            if index:
                time.sleep(PAUSE)
            try:
                detail = self.detail(fetcher, ref)
            except (Gone, Unavailable) as error:
                log.info("careersite %s: skipping %s (%s)", board, ref.url, error)
                continue
            posting = detail.posting()
            out.append(dataclasses.replace(posting, company=company or posting.company))
        return out


ADAPTER = _CareerSite()
