"""iCIMS, on its classic portal and on the career sites it hosts for companies.

iCIMS serves a company in one or both of two ways.

The classic portal lives at `<portal>-<customer>.icims.com`, with job pages at
`/jobs/<id>/<slug>/job`. Asking with `?in_iframe=1` returns the job without the
company's site chrome (about 50 KB instead of 650 KB) and with schema.org
JobPosting JSON-LD in it. One customer usually runs several portals: AMD has
careers-amd, global-external-amd and canadacareers-amd, Aon has one per region.
A job is posted to only one of them, and every other portal answers 410 "does
not exist or is no longer open" for it. So a portal page only ever counts as an
answer when the portal comes from the job's own URL, never from a guess.

The career site is the product iCIMS bought as Jibe, on the company's own domain
(careers.amd.com). Once a company has one, its portals stop serving search and
answer with a script that sends the browser to the site, and for a job they do
not carry, closed or not, that script comes with a 410. The site covers every
portal: it has a JSON search API, and its job pages carry the same JSON-LD and
answer 404 once a job is gone. So for those companies the site is the only
thing read.

Board identifier: the iCIMS customer key ("amd", "gdms") for a company listed in
COMPANIES. It is the same whichever portal or site a link came from, so a
captured posting and a scout result for the same job dedupe. The key is the last
hyphenated part of every portal host, and the portal pages' own asset paths
(`/amd.icims.com/icims2/...`) confirm that is what iCIMS calls the customer. Any
other company's board is its portal subdomain ("careers-acme"), which is enough
to read and search it directly.
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
from . import Detail, Gone, Ref, Unavailable, fetch_json, fetch_text

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Company:
    key: str                   # iCIMS customer key, the board identifier
    name: str                  # display name
    site: str | None = None    # career site host, when the company has one
    portal: str | None = None  # portal to search when there is no career site


# Each entry checked live in November 2026: the career site's search API
# answered, or for GDMS and Shure the portal's own keyword search did.
COMPANIES: dict[str, Company] = {
    company.key: company
    for company in (
        Company("amd", "AMD", site="careers.amd.com"),
        Company("aon", "Aon", site="jobs.aon.com"),
        Company("gdms", "General Dynamics Mission Systems", portal="careers-gdms"),
        Company("jhuapl", "Johns Hopkins APL", site="careers.jhuapl.edu"),
        Company("kindermorgan", "Kinder Morgan", site="careers.kindermorgan.com"),
        Company("msasafety", "MSA Safety", site="careers.msasafety.com"),
        Company("rivian", "Rivian", site="careers.rivian.com"),
        Company("shure", "Shure", portal="careersus-shure"),
    )
}
SITES: dict[str, Company] = {c.site: c for c in COMPANIES.values() if c.site}

PORTAL_JOB = "https://{portal}.icims.com/jobs/{job_id}/job"
PORTAL_SEARCH = "https://{portal}.icims.com/jobs/search?{query}"
SITE_JOB = "https://{site}/jobs/{job_id}"
SITE_SEARCH = "https://{site}/api/jobs?{query}"

SITE_PAGE = 50            # results are ranked, so two pages is past anything relevant
SITE_MAX_PAGES = 2
PORTAL_MAX_PAGES = 3      # the portal shows 20 a page
PORTAL_MAX_DETAILS = 10   # job pages opened when a result card is missing fields
REQUEST_PAUSE = 0.4       # between requests to one board; these are company sites

# A portal host is `<portal>-<customer>`, always hyphenated, one label deep.
_PORTAL_HOST = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
_PORTAL_PATH = re.compile(r"/jobs/(\d+)(?:/|$)")
# careers.amd.com/jobs/92826 redirects to /careers-home/jobs/92826, and either
# can be what a person copies.
_SITE_PATH = re.compile(r"(?:/[A-Za-z0-9_-]+)?/jobs/(\d+)(?:/|$)")
_APPLY_ID = re.compile(r"\.icims\.com/jobs/(\d+)(?:/|$)")
_STUB = re.compile(r"""window\.top\.location\.href\s*=\s*['"]([^'"]+)['"]""")
_CLOSED = re.compile(r"does not exist or is no longer open", re.I)
_JSONLD = re.compile(
    r"""<script[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""", re.I | re.S
)

_REMOTE = re.compile(r"\bremote\b|\btelework|\btelecommut|work from home", re.I)
_NOT_REMOTE = re.compile(r"\b(?:not|non|no)[\s-]+remote\b", re.I)
_REMOTE_FIELD = re.compile(
    r"location|workplace|work arrangement|work model|type of role|remote|telework", re.I
)
_EMPLOYMENT_FIELD = re.compile(
    r"employment type|position type|job type|part[- ]time/full[- ]time|"
    r"full[- ]time/part[- ]time|worker type|employment status",
    re.I,
)
_DEADLINE_FIELD = re.compile(
    r"deadline|closing date|close date|posting end|apply by|application due", re.I
)
# iCIMS writes a location as COUNTRY-REGION-City, with the region empty for
# places like "US--Telework".
_ICIMS_PLACE = re.compile(r"([A-Z]{2,3})-([A-Za-z0-9]{0,3})-(.+)")
_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(?:inc\.?|incorporated|corp\.?|corporation|llc|l\.l\.c\.|ltd\.?)$", re.I
)
_DATE_FORMATS = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y")
# iCIMS fills validThrough in by itself a year or more out (a year after the
# posted date, or a year from today). Only something sooner was set by a person.
_AUTO_VALID_THROUGH_DAYS = 350


def _customer(portal: str) -> str:
    return portal.rsplit("-", 1)[-1]


def _company(board: str) -> Company | None:
    """The company a board names: a COMPANIES key, its career site host, or a
    portal subdomain."""
    board = (board or "").strip().lower()
    if board.endswith(".icims.com"):
        board = board[: -len(".icims.com")]
    if board in COMPANIES:
        return COMPANIES[board]
    if board in SITES:
        return SITES[board]
    if not _PORTAL_HOST.fullmatch(board):
        return None
    known = COMPANIES.get(_customer(board))
    return known or Company(board, "", portal=board)


def _portal_of(url: str) -> str | None:
    host = (urllib.parse.urlsplit(url or "").hostname or "").rstrip(".")
    if host.endswith(".icims.com"):
        label = host[: -len(".icims.com")]
        if _PORTAL_HOST.fullmatch(label):
            return label
    return None


def _ref(company: Company, job_id: str, portal: str | None = None) -> Ref:
    if company.site:
        url = SITE_JOB.format(site=company.site, job_id=job_id)
    else:
        url = PORTAL_JOB.format(portal=portal or company.portal or company.key, job_id=job_id)
    return Ref(provider="icims", board=company.key, job_id=job_id, url=url)


def _pause() -> None:
    if REQUEST_PAUSE:
        time.sleep(REQUEST_PAUSE)


class _Recorder:
    """Passes requests through and keeps the last response, so the body of a
    404 or 410 can still be read after fetch_text has turned it into Gone."""

    def __init__(self, fetcher):
        self._fetcher = fetcher
        self.response = None

    def send(self, method, url, **kwargs):
        self.response = self._fetcher.send(method, url, **kwargs)
        return self.response


def _stub_target(html: str) -> str | None:
    """Where a portal that only redirects to a career site is pointing."""
    if not html or "iCIMS_" in html:
        return None
    found = _STUB.search(html)
    return found.group(1).replace("\\/", "/") if found else None


def _get(fetcher, url: str) -> str:
    recorder = _Recorder(fetcher)
    try:
        html = fetch_text(recorder, url)
    except Gone:
        response = recorder.response
        moved = _stub_target(getattr(response, "text", "") or "")
        if moved:
            # A career-site portal answers 410 for a job it does not carry,
            # whether the job closed or was never on that portal. Not proof.
            raise Unavailable(
                f"{url} only redirects to {moved}", getattr(response, "status_code", None)
            ) from None
        raise
    moved = _stub_target(html)
    if moved:
        raise Unavailable(f"{url} only redirects to {moved}")
    return html


def _jsonld_posting(html: str) -> dict | None:
    for block in _JSONLD.findall(html or ""):
        try:
            data = json.loads(block, strict=False)
        except ValueError:
            continue
        pending = [data]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                kinds = value.get("@type")
                if "JobPosting" in ([kinds] if isinstance(kinds, str) else kinds or []):
                    return value
                pending.extend(value.get("@graph") or [])
    return None


def _parse_when(value) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        stamp = None
        for pattern in _DATE_FORMATS:
            try:
                stamp = datetime.datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if stamp is None:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp


def _age_days(value) -> int | None:
    stamp = _parse_when(value)
    if stamp is None:
        return None
    return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)


def _icims_place(value: str) -> str:
    """US-MN-Bloomington -> Bloomington, MN, US, to match the JSON-LD form."""
    found = _ICIMS_PLACE.fullmatch(value.strip())
    if not found:
        return value.strip()
    country, region, city = found.groups()
    return ", ".join(part for part in (city.strip(), region, country) if part)


def _jsonld_location(posting: dict) -> str:
    places = posting.get("jobLocation")
    if isinstance(places, dict):
        places = [places]
    cells = []
    for place in places if isinstance(places, list) else []:
        address = (place or {}).get("address") if isinstance(place, dict) else None
        if not isinstance(address, dict):
            continue
        parts: list[str] = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            value = address.get(key)
            if isinstance(value, dict):
                value = value.get("name")
            value = str(value or "").strip()
            if value and value.upper() != "UNAVAILABLE" and value not in parts:
                parts.append(value)
        if parts:
            cells.append(", ".join(parts))
    return "; ".join(dict.fromkeys(cells))


def _employment(value) -> str | None:
    """Schema.org employmentType from a JSON-LD value or a portal field."""
    values = value if isinstance(value, list) else [value]
    for item in values:
        text = str(item or "").strip()
        if not text or text.upper() == "OTHER":
            continue
        if re.fullmatch(r"[A-Z_]+", text):
            return text
        lowered = text.casefold()
        if re.search(r"\bintern|\bco-?op\b", lowered):
            return "INTERN"
        if "part" in lowered:
            return "PART_TIME"
        if "full" in lowered:
            return "FULL_TIME"
        if "contract" in lowered:
            return "CONTRACTOR"
        if "temp" in lowered:
            return "TEMPORARY"
    return None


def _is_remote(*texts: str) -> bool:
    return any(t and _REMOTE.search(t) and not _NOT_REMOTE.search(t) for t in texts)


def _clean_org(name) -> str:
    return _LEGAL_SUFFIX.sub("", str(name or "").strip()).strip(" ,")


def _field_pairs(node) -> list[tuple[str, str]]:
    """(label, value) for every field iCIMS shows in a job header or result card."""
    pairs = []
    for tag in node.select(".iCIMS_JobHeaderTag"):
        label = tag.select_one(".iCIMS_JobHeaderField")
        value = tag.select_one(".iCIMS_JobHeaderData")
        if label and value:
            pairs.append((label.get_text(" ", strip=True), value.get_text(" ", strip=True)))
    # Older templates put a field in a header cell: a screen-reader label
    # followed by the value.
    for label in node.select(".header .field-label"):
        value = label.find_next_sibling("span")
        if value is not None:
            stamp = value.get("title")
            pairs.append((label.get_text(" ", strip=True), stamp or value.get_text(" ", strip=True)))
    return pairs


def _field_locations(pairs) -> list[str]:
    cells = []
    for label, value in pairs:
        if "location" in label.casefold():
            for part in re.split(r"[|;\n]", value):
                if part.strip():
                    cells.append(_icims_place(part))
    return list(dict.fromkeys(cells))


def _posted_field(pairs) -> str | None:
    for label, value in pairs:
        if "posted" in label.casefold():
            return value
    return None


def _deadline(posting: dict, pairs) -> str | None:
    for label, value in pairs:
        if _DEADLINE_FIELD.search(label):
            stamp = _parse_when(value)
            if stamp:
                return stamp.date().isoformat()
    valid = _parse_when(posting.get("validThrough"))
    posted = _parse_when(posting.get("datePosted"))
    if valid and posted and 0 < (valid - posted).days < _AUTO_VALID_THROUGH_DAYS:
        return valid.date().isoformat()
    return None


def _check_identity(posting: dict, job_id: str, url: str) -> None:
    # A page that resolved to some other job must not be read as this one.
    stated = str(posting.get("url") or "")
    if stated and not re.search(rf"/jobs/{job_id}(?!\d)", stated):
        raise Unavailable(f"{url} describes {stated}, not job {job_id}")


def _build(ref: Ref, company: Company, posting: dict, pairs, fallback: dict) -> Detail:
    title = str(posting.get("title") or fallback.get("title") or "").strip()
    if not title:
        raise Unavailable(f"{ref.url}: no title on the job page")
    location = _jsonld_location(posting) or "; ".join(_field_locations(pairs))
    description = posting.get("description")
    if not isinstance(description, str) or len(description.strip()) < 80:
        description = fallback.get("description") or (description if isinstance(description, str) else None)
    employment = _employment(posting.get("employmentType"))
    if employment is None:
        employment = _employment([v for label, v in pairs if _EMPLOYMENT_FIELD.search(label)])
    org = posting.get("hiringOrganization")
    org_name = _clean_org(org.get("name") if isinstance(org, dict) else org)
    remote = (
        str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE"
        or _is_remote(location)
        or _is_remote(*(v for label, v in pairs if _REMOTE_FIELD.search(label)))
    )
    posted = posting.get("datePosted") or _posted_field(pairs)
    return Detail(
        ref=ref,
        company=company.name or org_name or company.key,
        title=title,
        location=location,
        description_html=description or None,
        remote=remote,
        employment_type=employment,
        deadline=_deadline(posting, pairs),
        age_days=_age_days(posted),
    )


def _detail_portal(fetcher, ref: Ref, company: Company, portal: str) -> Detail:
    url = PORTAL_JOB.format(portal=portal, job_id=ref.job_id) + "?in_iframe=1"
    html = _get(fetcher, url)
    soup = BeautifulSoup(html, "html.parser")
    posting = _jsonld_posting(html)
    if posting is None:
        for message in soup.select(".iCIMS_ErrorMessage"):
            if _CLOSED.search(message.get_text(" ", strip=True)):
                raise Gone(f"{url} says the job does not exist or is no longer open")
    content = soup.select_one(".iCIMS_JobContent")
    if posting is None and content is None:
        raise Unavailable(f"{url} is not a job page")
    if posting is not None:
        _check_identity(posting, ref.job_id, url)
    pairs = _field_pairs(content) if content is not None else []
    fallback = {}
    if content is not None:
        heading = content.select_one("h1.iCIMS_Header, .iCIMS_Header")
        fallback["title"] = heading.get_text(" ", strip=True) if heading else ""
        sections = content.select(".iCIMS_InfoField_Job, .iCIMS_InfoMsg_Job")
        fallback["description"] = "\n".join(str(node) for node in sections) or None
    return _build(ref, company, posting or {}, pairs, fallback)


def _detail_site(fetcher, ref: Ref, company: Company) -> Detail:
    url = SITE_JOB.format(site=company.site, job_id=ref.job_id)
    html = _get(fetcher, url)
    posting = _jsonld_posting(html)
    if posting is None:
        raise Unavailable(f"{url} has no JobPosting on it")
    _check_identity(posting, ref.job_id, url)
    return _build(ref, company, posting, [], {})


class _IcimsAdapter:
    name = "icims"

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        if parsed.scheme not in ("http", "https"):
            return None
        netloc = parsed.netloc or ""
        if "@" in netloc or "\\" in netloc:
            return None
        host = (parsed.hostname or "").rstrip(".")
        if host.endswith(".icims.com"):
            portal = host[: -len(".icims.com")]
            found = _PORTAL_PATH.match(parsed.path or "")
            if not _PORTAL_HOST.fullmatch(portal) or not found:
                return None
            company = COMPANIES.get(_customer(portal)) or Company(portal, "", portal=portal)
            return _ref(company, found.group(1), portal)
        company = SITES.get(host)
        if company is None:
            return None
        found = _SITE_PATH.match(parsed.path or "")
        if not found:
            return None
        return _ref(company, found.group(1))

    def detail(self, fetcher, ref: Ref) -> Detail:
        company = _company(ref.board)
        if company is None or not str(ref.job_id).isdigit():
            raise Unavailable(f"not an iCIMS posting: {ref.board!r} {ref.job_id!r}")
        try:
            if company.site:
                return _detail_site(fetcher, ref, company)
            portal = _portal_of(ref.url) or company.portal or company.key
            return _detail_portal(fetcher, ref, company, portal)
        except RuntimeError as error:
            # The fetcher gave up (rate limited, or the network never came
            # back). Keep the status so a 429 can still be told apart.
            response = getattr(error.__cause__, "response", None)
            raise Unavailable(str(error), getattr(response, "status_code", None)) from error

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        target = _company(board)
        if target is None:
            log.warning("icims board %r is neither a known company nor a portal, skipping", board)
            return None
        display = company if company and company != board else (target.name or board)
        try:
            if target.site:
                return _search_site(fetcher, target, board, display, terms)
            return _search_portal(fetcher, target, board, display, terms)
        except (Gone, Unavailable) as error:
            # None, not []: a board that did not answer must not look like one
            # that answered with nothing, or the close rule retires its jobs.
            log.warning("icims %s did not answer: %s", board, error)
            return None


def _site_posting(data, target: Company, board: str, display: str) -> Posting | None:
    if not isinstance(data, dict):
        return None
    found = _APPLY_ID.search(str(data.get("apply_url") or ""))
    job_id = found.group(1) if found else next(
        (str(data[key]) for key in ("slug", "req_id") if str(data.get(key) or "").isdigit()), None
    )
    title = str(data.get("title") or "").strip()
    if not job_id or not title:
        return None
    location = str(data.get("full_location") or "").strip() or ", ".join(
        str(data[key]).strip() for key in ("city", "state", "country") if data.get(key)
    )
    ref = _ref(target, job_id)
    return Posting(
        source="icims",
        board=board,
        company=display,
        title=title,
        location=location,
        url=ref.url,
        remote=_is_remote(location, str(data.get("location_name") or "")),
        provider_id=ref.provider_id,
        age_days=_age_days(data.get("posted_date")),
        employment_type=_employment(data.get("employment_type")),
    )


def _search_site(fetcher, target: Company, board: str, display: str, terms) -> list[Posting] | None:
    seen: dict[str, Posting] = {}
    answered = False
    for term in terms or ():
        for page in range(1, SITE_MAX_PAGES + 1):
            if answered:
                _pause()
            query = urllib.parse.urlencode({"keywords": term, "page": page, "limit": SITE_PAGE})
            payload = fetch_json(fetcher, SITE_SEARCH.format(site=target.site, query=query))
            jobs = payload.get("jobs") if isinstance(payload, dict) else None
            if not isinstance(jobs, list):
                raise Unavailable(f"{target.site} search answered without a jobs list")
            answered = True
            for item in jobs:
                data = item.get("data") if isinstance(item, dict) else None
                posting = _site_posting(data, target, board, display)
                if posting is not None:
                    seen.setdefault(posting.provider_id, posting)
            total = payload.get("totalCount")
            if len(jobs) < SITE_PAGE or (isinstance(total, int) and page * SITE_PAGE >= total):
                break
    return list(seen.values()) if answered else None


def _has_next(soup, page: int) -> bool:
    for link in soup.select(".iCIMS_Paging a[href]"):
        if "invisible" in (link.get("class") or []):
            continue
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(link["href"]).query)
        if query.get("pr") == [str(page + 1)]:
            return True
    return False


def _cards(soup):
    cards = soup.select("li.iCIMS_JobCardItem")
    return cards or soup.select(".iCIMS_JobsTable > .row")


def _search_portal(fetcher, target: Company, board: str, display: str, terms) -> list[Posting] | None:
    portal = target.portal or target.key
    seen: dict[str, dict] = {}
    answered = False
    for term in terms or ():
        for page in range(PORTAL_MAX_PAGES):
            if answered:
                _pause()
            query = urllib.parse.urlencode(
                {"ss": 1, "searchKeyword": term, "in_iframe": 1, "pr": page}
            )
            html = _get(fetcher, PORTAL_SEARCH.format(portal=portal, query=query))
            soup = BeautifulSoup(html, "html.parser")
            if not soup.select_one(".iCIMS_ListingsPage, .iCIMS_JobSearchTable, .iCIMS_JobsTable"):
                raise Unavailable(f"{portal} answered its search with something else")
            answered = True
            for card in _cards(soup):
                row = _card(card, portal)
                if row is not None:
                    seen.setdefault(row["job_id"], row)
            if not _has_next(soup, page):
                break
    if not answered:
        return None

    out = []
    opened = 0
    for row in seen.values():
        ref = _ref(target, row["job_id"], portal)
        if (not row["title"] or not row["location"]) and opened < PORTAL_MAX_DETAILS:
            # The portal decides which fields a result card shows. When it
            # leaves out the title or location, the job page has them.
            opened += 1
            _pause()
            try:
                detail = _detail_portal(fetcher, ref, target, portal)
            except (Gone, Unavailable) as error:
                log.info("icims %s: job %s unreadable: %s", board, row["job_id"], error)
            else:
                row["title"] = row["title"] or detail.title
                row["location"] = row["location"] or detail.location
                row["remote"] = row["remote"] or detail.remote
                row["employment"] = row["employment"] or detail.employment_type
                row["age"] = row["age"] if row["age"] is not None else detail.age_days
        if not row["title"]:
            continue
        out.append(
            Posting(
                source="icims",
                board=board,
                company=display,
                title=row["title"],
                location=row["location"],
                url=ref.url,
                remote=row["remote"] or _is_remote(row["location"]),
                provider_id=ref.provider_id,
                age_days=row["age"],
                employment_type=row["employment"],
            )
        )
    return out


def _card(card, portal: str) -> dict | None:
    link = None
    for anchor in card.select("a[href]"):
        found = _PORTAL_PATH.search(urllib.parse.urlsplit(anchor["href"]).path)
        if found and _portal_of(anchor["href"]) in (None, portal):
            link = (anchor, found.group(1))
            break
    if link is None:
        return None
    anchor, job_id = link
    heading = anchor.select_one("h1, h2, h3, h4")
    if heading is not None:
        title = heading.get_text(" ", strip=True)
    else:
        for label in anchor.select(".sr-only"):
            label.decompose()
        title = anchor.get_text(" ", strip=True)
    pairs = _field_pairs(card)
    locations = _field_locations(pairs)
    return {
        "job_id": job_id,
        "title": title,
        "location": "; ".join(locations),
        "remote": _is_remote(*locations)
        or _is_remote(*(v for label, v in pairs if _REMOTE_FIELD.search(label))),
        "employment": _employment([v for label, v in pairs if _EMPLOYMENT_FIELD.search(label)]),
        "age": _age_days(_posted_field(pairs)),
    }


ADAPTER = _IcimsAdapter()
