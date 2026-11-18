"""Apple, through the JSON its own careers site calls.

jobs.apple.com renders its search server side, but the page's script talks to
two JSON routes that answer without a cookie or CSRF token:

- GET  /api/v1/jobDetails/<id>  one posting; a 404 with
  {"error": "jobsite.general.serviceError"} when the id is unknown.
- POST /api/v1/search           keyword search with a location filter, 20 per
  page. The body must carry the `format` block the site sends, or the answer
  is a 200 with no results at all, which is why an all-empty sweep is treated
  as "did not answer" below rather than "nothing open".

A posting id is "<positionId>-<location code>" (200695296-0157 is Austin,
200695296-0836 the same role in Cupertino). The details route takes the bare
position id and lists every location, and the bare page URL redirects to one
of the located ones, so the position id is the native id here: one role is
one row, not one per city.
"""
from __future__ import annotations

import dataclasses
import datetime
import html
import logging
import re
import time
import urllib.parse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from . import Detail, Gone, Ref, Unavailable, fetch_json
from ..models import Posting

log = logging.getLogger(__name__)

NAME = "apple"
BOARD = "apple"
COMPANY = "Apple"
HOST = "jobs.apple.com"
DETAIL = "https://jobs.apple.com/api/v1/jobDetails/{job_id}"
SEARCH = "https://jobs.apple.com/api/v1/search"
VIEW = "https://jobs.apple.com/en-us/details/{number}"
US = "postLocation-USA"
PAGE = 20           # the site's own page size
MAX_PAGES = 5       # relevance order puts title matches first
PAUSE = 0.5         # between search pages; it is somebody's careers site

# /en-us/details/200695296-0157/site-reliability-engineer-info-apps?team=SFTWR
_PATH = re.compile(
    r"/(?:app/)?(?:[a-z]{2}-[a-z]{2}/)?details/(?P<number>(?P<position>\d{6,12})(?:-\d{1,6})?)"
    r"(?:/(?P<slug>[^/]{1,300}))?/?",
    re.I,
)
# Only a plain slug is carried into the canonical URL; the id alone resolves.
_SLUG = re.compile(r"[A-Za-z0-9-]{1,200}")
# The Students team is where Apple files every internship; nothing else in
# the payload says so.
_STUDENT_TEAMS = {"stdnt", "students"}
_EMPLOYMENT = (
    ("CONTRACTOR", re.compile(r"contract", re.I)),
    ("TEMPORARY", re.compile(r"\btemp|seasonal", re.I)),
    ("PART_TIME", re.compile(r"part[- ]?time", re.I)),
    ("FULL_TIME", re.compile(r"full[- ]?time", re.I)),
)
# Plain-text sections in the order the posting page shows them. The lists are
# one item per line.
_SECTIONS = (
    ("jobSummary", "Summary", False),
    ("description", "Description", False),
    ("responsibilities", "Responsibilities", True),
    ("minimumQualifications", "Minimum Qualifications", True),
    ("preferredQualifications", "Preferred Qualifications", True),
)
# Footers worth keeping: the pay range and the closing date. The others are
# the same EEO and accessibility text on every posting.
_FOOTERS = ("Pay & Benefits", "Application Deadline")
_DATE = re.compile(
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4})\b"
)
_BLOCKS = {
    "p", "div", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "table", "blockquote", "pre",
}
# The body the site's own search sends. Without "format" the API answers 200
# with an empty result set.
_FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}


def _age_days(value) -> int | None:
    if not value:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)


def _place(loc: dict) -> str:
    """"Sunnyvale, California, United States", or just the country for a
    country-wide posting."""
    if not isinstance(loc, dict):
        return ""
    country = str(loc.get("countryName") or "").strip()
    city = str(loc.get("city") or loc.get("name") or "").strip()
    parts = [city, str(loc.get("stateProvince") or "").strip(), country]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _locations(rows) -> list[str]:
    return [p for p in (_place(loc) for loc in rows or []) if p]


def _employment(team_names, employment_type) -> str | None:
    if any(str(name).strip().casefold() in _STUDENT_TEAMS for name in team_names or ()):
        return "INTERN"
    label = str(employment_type or "")
    for kind, pattern in _EMPLOYMENT:
        if pattern.search(label):
            return kind
    # "Standard" is what almost every corporate role says, and it does not
    # distinguish full time from anything else.
    return None


def _text_html(text: str, as_list: bool) -> str:
    lines = [line.strip() for line in str(text).split("\n") if line.strip()]
    if not lines:
        return ""
    if as_list:
        return "<ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>"
    return "".join(f"<p>{html.escape(line)}</p>" for line in lines)


def _footers(res: dict) -> list[tuple[str, str]]:
    seen: dict[tuple[str, str], None] = {}
    for footer in res.get("postingFooters") or []:
        localized = (footer or {}).get("localizations") or {}
        for item in localized.get("en_US") or []:
            name = str((item or {}).get("name") or "")
            content = str((item or {}).get("content") or "").strip()
            if name in _FOOTERS and content:
                seen.setdefault((name, content), None)
    return list(seen)


def _wrap_loose(fragment: str) -> str:
    """Put text that sits outside any block into a paragraph of its own.

    The pay footer is bare text and <br>s, and a reader that walks block
    elements would skip the one line that states the range.
    """
    soup = BeautifulSoup(fragment, "html.parser")
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        chunk = "".join(run).strip()
        if BeautifulSoup(chunk, "html.parser").get_text(strip=True):
            out.append(f"<p>{chunk}</p>")
        run.clear()

    for node in list(soup.contents):
        if isinstance(node, Tag) and node.name in _BLOCKS:
            flush()
            out.append(str(node))
        elif isinstance(node, Tag):
            run.append(str(node))
        elif isinstance(node, NavigableString) and not isinstance(node, Comment):
            run.append(html.escape(str(node), quote=False))
    flush()
    return "".join(out)


def _description(res: dict) -> str | None:
    parts = []
    for key, heading, as_list in _SECTIONS:
        body = _text_html(res.get(key) or "", as_list)
        if body:
            parts.append(f"<h2>{heading}</h2>{body}")
    # Footer content is already HTML.
    for name, content in _footers(res):
        parts.append(f"<h2>{html.escape(name)}</h2>{_wrap_loose(content)}")
    return "".join(parts) or None


def _deadline(res: dict) -> str | None:
    """Only a date the Application Deadline footer states outright. Most say
    "on an ongoing basis", which is no deadline at all."""
    for name, content in _footers(res):
        if name != "Application Deadline":
            continue
        text = re.sub(r"<[^>]+>", " ", html.unescape(content))
        found = _DATE.search(text)
        if found:
            value = re.sub(r"\.", "", found.group(1))
            for pattern in ("%B %d, %Y", "%b %d, %Y"):
                try:
                    return datetime.datetime.strptime(value, pattern).date().isoformat()
                except ValueError:
                    continue
    return None


def _ref(position: str, number: str | None = None, slug: str | None = None) -> Ref:
    url = VIEW.format(number=number or position)
    if slug:
        url = f"{url}/{slug}"
    return Ref(provider=NAME, board=BOARD, job_id=position, url=url)


def _query(term: str) -> str:
    # A bare multi-word query is an OR: "site reliability" matches 1434 US
    # postings, the quoted phrase 77.
    term = term.strip().replace('"', "")
    return f'"{term}"' if " " in term else term


class _Apple:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        if (parsed.hostname or "").casefold() != HOST:
            return None
        found = _PATH.fullmatch(urllib.parse.unquote(parsed.path or ""))
        if not found:
            return None
        slug = found.group("slug")
        return _ref(found.group("position"), found.group("number"),
                    slug if slug and _SLUG.fullmatch(slug) else None)

    def detail(self, fetcher, ref: Ref) -> Detail:
        payload = fetch_json(fetcher, DETAIL.format(job_id=urllib.parse.quote(ref.job_id, safe="")))
        res = payload.get("res") if isinstance(payload, dict) else None
        if not isinstance(res, dict) or not str(res.get("postingTitle") or "").strip():
            raise Unavailable(f"unexpected Apple body for {ref.provider_id}")
        location = "; ".join(dict.fromkeys(_locations(res.get("locations"))))
        return Detail(
            ref=ref,
            company=COMPANY,
            title=str(res["postingTitle"]).strip(),
            location=location,
            description_html=_description(res),
            remote=bool(res.get("homeOffice")),
            employment_type=_employment(res.get("teamNames"), res.get("employmentType")),
            deadline=_deadline(res),
            age_days=_age_days(res.get("postDateInGMT")),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        seen: dict[str, Posting] = {}
        answered = False
        hits = 0
        first = True
        for term in terms or ():
            if not str(term).strip():
                continue
            for page in range(1, MAX_PAGES + 1):
                if not first:
                    time.sleep(PAUSE)
                first = False
                body = {
                    "query": _query(str(term)),
                    "filters": {"locations": [US]},
                    "page": page,
                    "locale": "en-us",
                    "sort": "relevance",
                    "format": _FORMAT,
                }
                try:
                    payload = fetch_json(fetcher, SEARCH, method="POST", body=body)
                except (Gone, Unavailable) as error:
                    # A partial sweep would let the close rule retire whatever
                    # the failed query would have returned.
                    log.warning("apple: query %r failed (%s), treating the board as absent", term, error)
                    return None
                res = payload.get("res") if isinstance(payload, dict) else None
                rows = res.get("searchResults") if isinstance(res, dict) else None
                if not isinstance(rows, list):
                    log.warning("apple: unexpected search body for %r", term)
                    return None
                answered = True
                hits += len(rows)
                for job in rows:
                    self._add(seen, board, company, job)
                total = res.get("totalRecords")
                if len(rows) < PAGE or (isinstance(total, int) and page * PAGE >= total):
                    break
        if not answered:
            return None
        if hits == 0:
            # Apple always has some infrastructure roles in the US. Every
            # query coming back empty is the shape of a changed request
            # format, and reporting it as "nothing open" would close them all.
            log.warning("apple: every query came back empty, treating the board as absent")
            return None
        return list(seen.values())

    @staticmethod
    def _add(seen: dict[str, Posting], board: str, company: str, job: dict) -> None:
        if not isinstance(job, dict):
            return
        number = str(job.get("id") or job.get("reqId") or "")
        position = str(job.get("positionId") or number.split("-", 1)[0])
        if not position.isdigit():
            return
        places = _locations(job.get("locations"))
        existing = seen.get(position)
        if existing is not None:
            # The same role listed again for another city.
            merged = [p for p in (*existing.location.split("; "), *places) if p]
            seen[position] = dataclasses.replace(existing, location="; ".join(dict.fromkeys(merged)))
            return
        slug = str(job.get("transformedPostingTitle") or "")
        ref = _ref(position, number if number.startswith(position) else None,
                   slug if _SLUG.fullmatch(slug) else None)
        location = "; ".join(dict.fromkeys(places))
        team = job.get("team") or {}
        seen[position] = Posting(
            source=NAME,
            board=board or BOARD,
            company=company or COMPANY,
            title=str(job.get("postingTitle") or "").strip(),
            location=location,
            url=ref.url,
            remote=bool(job.get("homeOffice")) or "remote" in location.casefold(),
            provider_id=ref.provider_id,
            age_days=_age_days(job.get("postDateInGMT")),
            employment_type=_employment([team.get("teamCode"), team.get("teamName")], None),
        )


ADAPTER = _Apple()
