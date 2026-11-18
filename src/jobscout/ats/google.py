"""Google, from the data its careers pages render server side.

There is no public JSON route. The careers site's script fetches through
Google's batchexecute endpoint, which wants the f.sid and bl values out of a
loaded page, and lifting a session value from one response to replay in the
next is not something this does. The pages themselves are enough though:
every one embeds its data as JSON in AF_initDataCallback blocks, and
AF_dataServiceRequests says which RPC filled which block. Block keys move
around between pages (ds:0 is the job on a detail page and the company list
on a results page), so blocks are found by RPC id instead.

- A posting page, /about/careers/applications/jobs/results/<id>, carries RPC
  sf9Qmf with the id as its request: one job record, or [] when the id is
  unknown. That empty answer still comes back as a 200 titled "Jobs search",
  so it is the only signal a posting has gone.
- A results page, the same path with ?q=&location=&page=, carries RPC r06xKb:
  [records, None, total, page size], 20 to a page, relevance ordered.

A job record is a positional array, read by index below. Trailing empty
fields are dropped, so a record can be shorter than the highest index.
"""
from __future__ import annotations

import datetime
import html
import json
import logging
import re
import time
import urllib.parse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from . import Detail, Gone, Ref, Unavailable, fetch_text
from ..models import Posting

log = logging.getLogger(__name__)

NAME = "google"
BOARD = "google"
COMPANY = "Google"
VIEW = "https://www.google.com/about/careers/applications/jobs/results/{job_id}"
RESULTS = "https://www.google.com/about/careers/applications/jobs/results/"
HEADERS = {"Accept": "text/html", "Accept-Language": "en-US,en;q=0.8"}
DETAIL_RPC = "sf9Qmf"
SEARCH_RPC = "r06xKb"
LOCATION = "United States"
PAGE = 20           # the site's own page size
MAX_PAGES = 2       # each results page is ~1.3 MB, and the scout sends ~14 terms
PAUSE = 1.0         # between pages; a results page is over a megabyte

_NEW_PATH = re.compile(r"/about/careers/applications/(?:u/\d{1,2}/)?jobs/results/(\d{6,25})(?:-[^/]*)?/?")
_OLD_PATH = re.compile(r"/jobs/results/(\d{6,25})(?:-[^/]*)?/?")
# careers.google.com/jobs/results/<id> still redirects to the new path.
_HOSTS = {
    "www.google.com": _NEW_PATH,
    "google.com": _NEW_PATH,
    "careers.google.com": _OLD_PATH,
}

# Record fields.
_ID, _TITLE, _RESPONSIBILITIES, _QUALIFICATIONS = 0, 1, 3, 4
_COMPANY, _LOCATIONS, _ABOUT, _CREATED, _ADDITIONAL, _LEVEL = 7, 9, 10, 12, 15, 20
# target_level: 1 EARLY, 2 MID, 3 ADVANCED, 4 DIRECTOR, 5 INTERN_AND_APPRENTICE,
# matched against the site's own target_level filter.
_INTERN_LEVEL = 5

_REQUESTS = re.compile(r"'(ds:\d+)'\s*:\s*\{\s*id\s*:\s*'(\w+)'\s*,\s*request\s*:\s*(\[[^{}]*?\])\s*\}")
_CALLBACK = re.compile(r"AF_initDataCallback\(\{\s*key:\s*'(ds:\d+)'\s*,\s*hash:\s*'[^']*'\s*,\s*data:")
# "The anticipated application window is open until February 26, 2027". The
# usual "will be open until at least <date>" is a floor, not a deadline, and
# the capitalised month keeps it from matching.
_WINDOW = re.compile(
    r"application window (?:is|will be) open until\s+(?P<date>[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})"
)
_BLOCKS = {
    "p", "div", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "table", "blockquote", "pre",
}


def _blocks(page: str) -> dict[str, tuple[str, object]]:
    """RPC id -> (request text, decoded data)."""
    rpcs = {key: (rpc, request) for key, rpc, request in _REQUESTS.findall(page or "")}
    decoder = json.JSONDecoder()
    out = {}
    for found in _CALLBACK.finditer(page or ""):
        rpc, request = rpcs.get(found.group(1), ("", ""))
        if not rpc:
            continue
        try:
            data, _ = decoder.raw_decode(page, found.end())
        except ValueError:
            continue
        out[rpc] = (request, data)
    return out


def _at(record: list, index: int):
    return record[index] if isinstance(record, list) and len(record) > index else None


def _html(record: list, index: int) -> str:
    field = _at(record, index)
    if isinstance(field, list) and len(field) > 1 and isinstance(field[1], str):
        return field[1].strip()
    return ""


def _location(record: list) -> str:
    places = []
    for place in _at(record, _LOCATIONS) or []:
        if isinstance(place, list) and place and isinstance(place[0], str) and place[0].strip():
            places.append(place[0].strip())
    return "; ".join(dict.fromkeys(places))


def _age_days(record: list) -> int | None:
    stamp = _at(record, _CREATED)
    if not (isinstance(stamp, list) and stamp and isinstance(stamp[0], (int, float))):
        return None
    created = datetime.datetime.fromtimestamp(stamp[0], datetime.timezone.utc)
    return max((datetime.datetime.now(datetime.timezone.utc) - created).days, 0)


def _employment(record: list) -> str | None:
    # The level is all the record says. The rest is FULL_TIME almost always,
    # but nothing in the payload states it.
    return "INTERN" if _at(record, _LEVEL) == _INTERN_LEVEL else None


def _company(record: list, fallback: str = COMPANY) -> str:
    name = _at(record, _COMPANY)
    # DeepMind and YouTube post through the same site under their own name.
    return name.strip() if isinstance(name, str) and name.strip() else fallback


def _wrap_loose(fragment: str) -> str:
    """Put text that sits between blocks into a paragraph of its own.

    About the job ends with the pay line as bare text after the last </p>
    ("US: $183000 - $265000 (USD) + 20% bonus target"), and a reader that
    walks block elements would skip it.
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


def _description(record: list) -> str | None:
    parts = []
    qualifications = _html(record, _QUALIFICATIONS)
    if qualifications:
        # Already headed "Minimum qualifications:" / "Preferred qualifications:".
        parts.append(_wrap_loose(qualifications))
    for index, heading in ((_ABOUT, "About the job"), (_RESPONSIBILITIES, "Responsibilities"),
                           (_ADDITIONAL, "Additional information")):
        body = _html(record, index)
        if body:
            parts.append(f"<h3>{heading}</h3>{_wrap_loose(body)}")
    return "".join(parts) or None


def _deadline(record: list) -> str | None:
    text = html.unescape(re.sub(r"<[^>]+>", " ", _html(record, _ADDITIONAL)))
    found = _WINDOW.search(re.sub(r"\s+", " ", text.replace("\xa0", " ")))
    if not found:
        return None
    value = found.group("date").replace(".", "")
    for pattern in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _ref(job_id: str) -> Ref:
    return Ref(provider=NAME, board=BOARD, job_id=job_id, url=VIEW.format(job_id=job_id))


def _query(term: str) -> str:
    # Unquoted words are ORed: "site reliability" is 248 US postings, the
    # quoted phrase 45.
    term = term.strip().replace('"', "")
    return f'"{term}"' if " " in term else term


class _Google:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        path = _HOSTS.get((parsed.hostname or "").casefold())
        if path is None:
            return None
        found = path.fullmatch(urllib.parse.unquote(parsed.path or ""))
        return _ref(found.group(1)) if found else None

    def detail(self, fetcher, ref: Ref) -> Detail:
        if not ref.job_id.isdigit():
            raise Unavailable(f"not a Google job id: {ref.job_id!r}")
        page = fetch_text(fetcher, VIEW.format(job_id=ref.job_id), headers=HEADERS)
        found = _blocks(page).get(DETAIL_RPC)
        if found is None:
            raise Unavailable(f"no job data in the Google page for {ref.provider_id}")
        request, data = found
        if f'"{ref.job_id}"' not in request:
            raise Unavailable(f"Google page answered for a different job than {ref.provider_id}")
        if data == []:
            raise Gone(f"Google has no job {ref.job_id}")
        record = data[0] if isinstance(data, list) and data else None
        if not isinstance(record, list) or str(_at(record, _ID)) != ref.job_id or not _at(record, _TITLE):
            raise Unavailable(f"unexpected Google job record for {ref.provider_id}")
        location = _location(record)
        return Detail(
            ref=ref,
            company=_company(record),
            title=str(record[_TITLE]).strip(),
            location=location,
            description_html=_description(record),
            remote="remote" in location.casefold(),
            employment_type=_employment(record),
            deadline=_deadline(record),
            age_days=_age_days(record),
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
                params = {"q": _query(str(term)), "location": LOCATION}
                if page > 1:
                    params["page"] = page
                url = f"{RESULTS}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
                try:
                    found = _blocks(fetch_text(fetcher, url, headers=HEADERS)).get(SEARCH_RPC)
                except (Gone, Unavailable) as error:
                    # A partial sweep would let the close rule retire whatever
                    # the failed query would have returned.
                    log.warning("google: query %r failed (%s), treating the board as absent", term, error)
                    return None
                data = found[1] if found else None
                if not isinstance(data, list) or not data or not isinstance(data[0], (list, type(None))):
                    log.warning("google: no search data in the page for %r", term)
                    return None
                answered = True
                rows = data[0] or []
                hits += len(rows)
                for record in rows:
                    job_id = str(_at(record, _ID) or "")
                    if not job_id.isdigit() or job_id in seen or not _at(record, _TITLE):
                        continue
                    ref = _ref(job_id)
                    location = _location(record)
                    seen[job_id] = Posting(
                        source=NAME,
                        board=board or BOARD,
                        company=_company(record, company or COMPANY),
                        title=str(record[_TITLE]).strip(),
                        location=location,
                        url=ref.url,
                        remote="remote" in location.casefold(),
                        provider_id=ref.provider_id,
                        age_days=_age_days(record),
                        employment_type=_employment(record),
                    )
                total = _at(data, 2)
                size = _at(data, 3) if isinstance(_at(data, 3), int) and _at(data, 3) > 0 else PAGE
                if len(rows) < size or (isinstance(total, int) and page * size >= total):
                    break
        if not answered:
            return None
        if hits == 0:
            # Google always has infrastructure roles in the US, so every query
            # empty means the page changed under us, not that nothing is open.
            log.warning("google: every query came back empty, treating the board as absent")
            return None
        return list(seen.values())


ADAPTER = _Google()
