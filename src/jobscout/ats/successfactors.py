"""SAP SuccessFactors career sites.

Two kinds of URL lead here.

Career Site Builder (CSB) sites live on the company's own domain, so they can
only be recognised from an explicit list: jobs.mcdonalds.com/job/<slug>/<id>/.
The slug is decoration, the number is the CSB job id. Every job page carries
schema.org JobPosting as microdata (some templates add JSON-LD), and every site
has a server-rendered keyword search at /search/?q=... with a country facet,
which is what the scout reads.

The older links sit on SAP's own hosts:
career41.sapsf.com/career?company=mcdonaldsc&career_job_req_id=123 or
.../sfcareer/jobreqcareer?jobId=123&company=.... Those carry a requisition id,
not the CSB job id, and SAP answers them with a redirect to the company's CSB
site at /job-invite/<req>/, which redirects again to the job page. So a
requisition ref is read by following that chain, and comes back from detail()
as a CSB ref when the company's site is known.

How CSB says a posting is gone: the job URL still answers 200 with the page
template, minus the JobPosting markup, and "Sorry, this position has been
filled." An id it has never heard of redirects to /errorpage/?errortype=
Exception, which is too generic to call closed, so that reads as Unavailable.

Not every SuccessFactors customer has a public site to read. Lockheed Martin
runs SuccessFactors behind an Eightfold front end (lockheedmartin.eightfold.ai),
which is a different adapter's job.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.parse

from bs4 import BeautifulSoup

from . import Detail, Gone, Ref, Unavailable, fetch_text
from ..models import Posting

log = logging.getLogger(__name__)

NAME = "successfactors"

# CSB sites on a company's own domain, each checked live: host -> display name.
CAREER_SITES = {
    "jobs.mcdonalds.com": "McDonald's",
    "careers.acuityinc.com": "Acuity",
    "jobs.nassco.com": "General Dynamics NASSCO",
}

# SuccessFactors company ids whose legacy links redirect to one of the sites
# above, so a requisition link can be filed under the same board.
COMPANY_SITES = {
    "mcdonaldsc": "jobs.mcdonalds.com",
}

# SAP's own hosts for the legacy career pages: career4.successfactors.com,
# career41.sapsf.com, career5.successfactors.eu and so on.
_SAP_SUFFIXES = (".successfactors.com", ".successfactors.eu", ".sapsf.com", ".sapsf.eu")

SEARCH = "https://{host}/search/?{query}"
JOB_INVITE = "https://{host}/job-invite/{req}/"
LEGACY = "https://{host}/career?career_ns=job_listing&company={company}&career_job_req_id={req}"
MAX_PAGES = 5           # CSB pages hold 10 to 25 rows, so up to ~125 per term
PAUSE = 0.4             # these are somebody's careers site

_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_JOB_PATH = re.compile(r"/job/([^/]+)/(\d{4,})(?:-[a-z]{2}_[A-Z]{2})?/?")
_INVITE_PATH = re.compile(r"/job-invite/(\d+)/?")
_COMPANY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_REQ = re.compile(r"\d{1,12}")
_CSB_JOB_ID = re.compile(r"(?:[?&;]jobid=|/talentcommunity/apply/)(\d{4,})", re.I)
_RESULTS = re.compile(r"Results\s+\d+\s+(?:to|–|-)\s+\d+\s+of\s+(\d+)", re.I)
_MORE = re.compile(r"\s*\+\s*\d+\s+more.*$", re.I | re.S)
_FILLED = re.compile(
    r"position has been filled|no longer (?:available|accepting applications)|"
    r"job (?:has )?expired|posting has expired",
    re.I,
)
_WS = re.compile(r"\s+")

_LIST_DATES = ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%Y-%m-%d", "%d.%m.%Y")


def _clean(text) -> str:
    return _WS.sub(" ", str(text or "")).strip()


def _is_sap_host(host: str) -> bool:
    return any(host.endswith(suffix) for suffix in _SAP_SUFFIXES) and host.split(".", 1)[0].startswith("career")


def _site_name(host: str) -> str:
    if host in CAREER_SITES:
        return CAREER_SITES[host]
    labels = [l for l in host.split(".") if l not in ("www", "jobs", "careers", "career", "com", "net", "org")]
    return labels[0].capitalize() if labels else host


def _parse_stamp(value: str) -> datetime.datetime | None:
    """CSB microdata dates are Java's Date.toString: 'Sat Nov 07 08:00:00 UTC 2026'."""
    value = _clean(value)
    if not value:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        stamp = None
        # Drop the zone name, which strptime only understands for UTC/GMT.
        bare = re.sub(r"^(\w{3} \w{3} \d{1,2} [\d:]{8}) \S+ (\d{4})$", r"\1 \2", value)
        for pattern in ("%a %b %d %H:%M:%S %Y", *_LIST_DATES):
            try:
                stamp = datetime.datetime.strptime(bare, pattern)
                break
            except ValueError:
                continue
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp


def _age_days(value: str) -> int | None:
    stamp = _parse_stamp(value)
    if stamp is None:
        return None
    return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)


def _employment(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    label = _clean(value).upper().replace("-", "_").replace(" ", "_")
    if not label:
        return None
    if "INTERN" in label or "CO_OP" in label or "COOP" in label:
        return "INTERN"
    for needle, kind in (("PART", "PART_TIME"), ("TEMP", "TEMPORARY"), ("CONTRACT", "CONTRACTOR"), ("FULL", "FULL_TIME"), ("REGULAR", "FULL_TIME")):
        if needle in label:
            return kind
    return None


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "job"


# --- reading a job page -------------------------------------------------------

def _jsonld_posting(soup) -> dict | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
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


def _jsonld_location(posting: dict) -> list[str]:
    places = posting.get("jobLocation")
    if isinstance(places, dict):
        places = [places]
    cells = []
    for place in places or []:
        address = (place or {}).get("address") or {}
        if isinstance(address, str):
            cells.append(_clean(address))
            continue
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        cells.append(", ".join(_clean(v) for v in (address.get("addressLocality"), address.get("addressRegion"), country) if v))
    return [c for c in cells if c]


def _prop(scope, name: str):
    """The first itemprop of this name inside the JobPosting scope."""
    return scope.find(attrs={"itemprop": name})


def _prop_value(element) -> str:
    if element is None:
        return ""
    if element.has_attr("content"):
        return _clean(element["content"])
    if element.has_attr("datetime"):
        return _clean(element["datetime"])
    return _clean(element.get_text(" "))


def _microdata_location(scope) -> list[str]:
    cells = []
    for place in scope.find_all(attrs={"itemprop": "jobLocation"}):
        parts = [_prop_value(_prop(place, key)) for key in ("addressLocality", "addressRegion", "addressCountry")]
        cell = ", ".join(p for p in parts if p)
        if cell:
            cells.append(cell)
    return cells


def _read_page(page: str, label: str) -> dict:
    """Title, location, description and dates from a CSB job page.

    Raises Gone for the filled-position page and Unavailable for anything
    that is not recognisably a job.
    """
    if "/errorpage/" in page and "errortype=" in page:
        raise Unavailable(f"{label}: career site answered with its error page")
    soup = BeautifulSoup(page, "html.parser")

    found = {}
    posting = _jsonld_posting(soup)
    scope = soup.find(attrs={"itemtype": re.compile(r"schema\.org/JobPosting", re.I)})
    if posting is not None:
        org = posting.get("hiringOrganization")
        found = {
            "title": _clean(posting.get("title")),
            "locations": _jsonld_location(posting),
            "description": posting.get("description") if isinstance(posting.get("description"), str) else None,
            "posted": _clean(posting.get("datePosted")),
            "closes": _clean(posting.get("validThrough")),
            "company": _clean(org.get("name") if isinstance(org, dict) else org),
            "employment": posting.get("employmentType"),
        }
    if scope is not None:
        description = _prop(scope, "description")
        micro = {
            "title": _prop_value(_prop(scope, "title")),
            "locations": _microdata_location(scope),
            "description": description.decode_contents().strip() if description is not None else None,
            "posted": _prop_value(_prop(scope, "datePosted")),
            "closes": _prop_value(_prop(scope, "validThrough")),
            "company": _prop_value(_prop(scope, "hiringOrganization")),
            "employment": _prop_value(_prop(scope, "employmentType")),
        }
        # JSON-LD wins where both exist; microdata fills the gaps.
        found = {key: found.get(key) or micro[key] for key in micro}
    if not found.get("title"):
        for script in soup(["script", "style"]):
            script.decompose()
        if _FILLED.search(soup.get_text(" ")):
            raise Gone(f"{label}: the career site says the position is filled")
        raise Unavailable(f"{label}: no JobPosting on the page")
    if not found.get("locations"):
        geo = soup.select_one(".jobGeoLocation")
        if geo is not None and _clean(geo.get_text(" ")):
            found["locations"] = [_clean(geo.get_text(" "))]
    job_id = _CSB_JOB_ID.search(page)
    found["job_id"] = job_id.group(1) if job_id else None
    return found


# --- reading a search page ----------------------------------------------------

def _read_search(page: str, host: str):
    """(rows, total) from a CSB search page, or None if it is not one.

    rows is empty when CSB says nothing matched. It still lists its ten most
    recent jobs underneath that notice, and those must not be read as hits.
    """
    soup = BeautifulSoup(page, "html.parser")
    if soup.select_one("#noresults") is not None:
        return [], 0
    table = soup.select_one("table#searchresults") or soup.select_one(".searchResultsShell")
    if table is None:
        return None
    rows = []
    for row in table.select("tr.data-row"):
        link = row.select_one("a.jobTitle-link")
        href = link.get("href") if link is not None else None
        path = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{host}/", href or ""))
        match = _JOB_PATH.fullmatch(path.path) if path.hostname == host else None
        if match is None:
            continue
        place = row.select_one("td.colLocation .jobLocation") or row.select_one(".jobLocation")
        date = row.select_one("td.colDate .jobDate") or row.select_one(".jobDate")
        rows.append({
            "job_id": match.group(2),
            "url": f"https://{host}/job/{match.group(1)}/{match.group(2)}/",
            "title": _clean(link.get_text(" ")),
            "location": _MORE.sub("", _clean(place.get_text(" ")) if place is not None else ""),
            "date": _clean(date.get_text(" ")) if date is not None else "",
        })
    total = None
    label = table.get("aria-label") or ""
    counted = _RESULTS.search(label)
    if counted is None:
        shown = soup.select_one(".paginationLabel")
        counted = _RESULTS.search(_clean(shown.get_text(" "))) if shown is not None else None
    if counted is not None:
        total = int(counted.group(1))
    return rows, total


def _list_age(text: str) -> int | None:
    for pattern in _LIST_DATES:
        try:
            stamp = datetime.datetime.strptime(_clean(text), pattern)
        except ValueError:
            continue
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)
    return None


class _SuccessFactors:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = (parsed.hostname or "").casefold()
        if host in CAREER_SITES:
            job = _JOB_PATH.fullmatch(parsed.path)
            if job:
                slug, job_id = job.groups()
                return Ref(NAME, host, job_id, f"https://{host}/job/{slug}/{job_id}/")
            invite = _INVITE_PATH.fullmatch(parsed.path)
            if invite:
                req = invite.group(1)
                return Ref(NAME, host, f"req-{req}", JOB_INVITE.format(host=host, req=req))
            return None
        if not _is_sap_host(host):
            return None
        query = {k.casefold(): v[0] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}
        path = parsed.path.rstrip("/").casefold()
        if path == "/career":
            req = query.get("career_job_req_id")
        elif path == "/sfcareer/jobreqcareer":
            req = query.get("jobid")
        else:
            return None
        company = query.get("company") or ""
        if not req or not _REQ.fullmatch(req) or not _COMPANY_ID.fullmatch(company):
            return None
        site = COMPANY_SITES.get(company.casefold())
        if site:
            return Ref(NAME, site, f"req-{req}", JOB_INVITE.format(host=site, req=req))
        return Ref(NAME, f"{host}/{company}", f"req-{req}", LEGACY.format(host=host, company=company, req=req))

    def detail(self, fetcher, ref: Ref) -> Detail:
        host, _, company_id = ref.board.partition("/")
        req = ref.job_id[4:] if ref.job_id.startswith("req-") else None
        if req is None and not ref.job_id.isdigit():
            raise Unavailable(f"{ref.provider_id}: not a CSB job id")
        if company_id:
            # A SAP-hosted company with no known CSB site: let SAP's own
            # redirects find the job.
            url = LEGACY.format(host=host, company=urllib.parse.quote(company_id), req=req) + "&locale=en_US"
        elif req is not None:
            url = JOB_INVITE.format(host=host, req=req) + "?locale=en_US"
        else:
            path = urllib.parse.urlsplit(ref.url)
            slug_path = path.path if path.hostname == host and _JOB_PATH.fullmatch(path.path) else f"/job/job/{ref.job_id}/"
            url = f"https://{host}{slug_path}?locale=en_US"

        found = _read_page(fetch_text(fetcher, url), ref.provider_id)

        # A requisition link resolves to the CSB job the scout knows by its
        # own id, so file it under that one.
        if req is not None and not company_id and found.get("job_id"):
            job_id = found["job_id"]
            ref = Ref(NAME, host, job_id, f"https://{host}/job/{_slug(found['title'])}/{job_id}/")

        location = "; ".join(dict.fromkeys(found.get("locations") or []))
        closes = _parse_stamp(found.get("closes") or "")
        site = host if not company_id else ""
        return Detail(
            ref=ref,
            company=CAREER_SITES.get(site) or found.get("company") or _site_name(host if site else company_id),
            title=found["title"],
            location=location,
            description_html=found.get("description") or None,
            remote="remote" in location.casefold(),
            employment_type=_employment(found.get("employment")),
            deadline=closes.isoformat() if closes else None,
            age_days=_age_days(found.get("posted") or ""),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        host = (board or "").casefold()
        if not _HOST.fullmatch(host):
            # A SAP-hosted company id has no search page to read; the board
            # needs to be the company's CSB host.
            log.warning("successfactors board %r is not a career site host, skipping", board)
            return None
        seen: dict[str, Posting] = {}
        answered = False
        first = True
        for term in terms or ():
            start = 0
            for _ in range(MAX_PAGES):
                params = {"q": term, "optionsFacetsDD_country": "US", "locale": "en_US"}
                if start:
                    params["startrow"] = start
                if not first:
                    time.sleep(PAUSE)
                first = False
                url = SEARCH.format(host=host, query=urllib.parse.urlencode(params))
                try:
                    page = fetch_text(fetcher, url)
                except (Gone, Unavailable) as error:
                    log.warning("successfactors %s: query %r failed (%s), treating the board as absent", host, term, error)
                    return None
                parsed = _read_search(page, host)
                if parsed is None:
                    log.warning("successfactors %s: %r did not return a search page", host, term)
                    return None
                answered = True
                rows, total = parsed
                for row in rows:
                    if row["job_id"] in seen:
                        continue
                    ref = Ref(NAME, host, row["job_id"], row["url"])
                    seen[row["job_id"]] = Posting(
                        source=NAME,
                        board=host,
                        company=company or _site_name(host),
                        title=row["title"],
                        location=row["location"],
                        url=row["url"],
                        remote="remote" in row["location"].casefold(),
                        provider_id=ref.provider_id,
                        age_days=_list_age(row["date"]),
                    )
                start += len(rows)
                if not rows or total is None or start >= total:
                    break
        return list(seen.values()) if answered else None


ADAPTER = _SuccessFactors()
