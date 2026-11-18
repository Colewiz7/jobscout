"""Oracle Recruiting Cloud ("Candidate Experience") career sites.

Each employer gets its own Fusion pod (egug.fa.us2.oraclecloud.com,
jpmc.fa.oraclecloud.com), and the site page reads two public REST resources
on that pod: `recruitingCEJobRequisitions` for search and
`recruitingCEJobRequisitionDetails` for one posting. A board here is
"<pod host>/<site>", where site is the segment after /sites/ in a posting URL.

That segment is usually the site number (CX_1, CX_1001), but can be a site
code such as Goldman Sachs' LateralHiring, which is really CX_3002. The REST
search does not reject a name it does not know, it falls back to searching
every external site on the pod. A pod belongs to one employer, so that is
still the right company's postings, and the site is passed through as is.

The US filter is a geography id that differs per pod (300000000229164 at
American Express, 300000000289738 at JPMorgan Chase), so search asks the pod
for its location facets once and picks "United States" out of them.
"""
from __future__ import annotations

import datetime
import html
import logging
import re
import time
import urllib.parse

from ..models import Posting
from . import Detail, Gone, Ref, Unavailable, fetch_json

log = logging.getLogger(__name__)

NAME = "oracle"

API = "https://{host}/hcmRestApi/resources/latest/{resource}?{query}"
VIEW_POD = "https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}"
VIEW_CUSTOM = "https://{host}/en/sites/{site}/job/{job_id}"

PAGE = 25            # what the site's own search page asks for
MAX_PAGES = 4        # plus one facet probe per board: about 5 requests a term
PAUSE = 0.5

# Company domains fronting a pod, verified live 2026-11-18: pod -> public host.
CUSTOM_HOSTS: dict[str, str] = {
    "careers.americanexpress.com": "egug.fa.us2.oraclecloud.com",
}
_PUBLIC_HOST = {pod: public for public, pod in CUSTOM_HOSTS.items()}

# Pods verified live 2026-11-18. A pod is one employer.
NAMES: dict[str, str] = {
    "egug.fa.us2.oraclecloud.com": "American Express",
    "jpmc.fa.oraclecloud.com": "JPMorgan Chase",
    "hdpc.fa.us2.oraclecloud.com": "Goldman Sachs",
}

# <pod>.fa.oraclecloud.com, <pod>.fa.<region>.oraclecloud.com, and the
# fa-xxxx-saasfaprod1.fa.ocs.oraclecloud.com form.
_POD_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.fa(?:\.[a-z0-9-]{1,63})?\.oraclecloud\.com")
_CE_PATH = re.compile(
    r"(?P<prefix>/hcmUI/CandidateExperience)?"
    r"/[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?"     # locale: en, en-US, fr_CA
    r"/sites/(?P<site>[A-Za-z0-9_-]{1,64})"
    r"/(?:(?:requisitions|jobs)/)?(?:job|preview)"
    r"/(?P<job_id>[A-Za-z0-9_-]{1,40})(?:[/?#]|$)"
)
_SITE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_US_NAMES = {"united states", "united states of america", "usa", "us"}


def _host(parsed: urllib.parse.SplitResult) -> str:
    return (parsed.hostname or "").rstrip(".").casefold()


def _ref(pod: str, site: str, job_id: str) -> Ref:
    public = _PUBLIC_HOST.get(pod)
    if public:
        url = VIEW_CUSTOM.format(host=public, site=site, job_id=job_id)
    else:
        url = VIEW_POD.format(host=pod, site=site, job_id=job_id)
    return Ref(
        provider=NAME,
        board=f"{pod}/{site}",
        job_id=job_id,
        url=url,
        # Requisition ids are unique per pod, and one posting can sit on two
        # sites (lateral and campus), so the site stays out of the dedupe key.
        provider_id=f"{NAME}:{pod}:{job_id}",
    )


def _board_parts(board: str) -> tuple[str, str] | None:
    host, _, site = (board or "").strip().rstrip("/").partition("/")
    host = host.casefold()
    host = CUSTOM_HOSTS.get(host, host)
    if _POD_HOST.fullmatch(host) and _SITE.fullmatch(site):
        return host, site
    return None


def _display_name(pod: str, fallback: str = "") -> str:
    if pod in NAMES:
        return NAMES[pod]
    if fallback:
        return fallback
    label = pod.split(".", 1)[0]
    return label[:1].upper() + label[1:]


def _finder_term(term: str) -> str:
    # The finder is a ;/,-separated list inside one query parameter, and the
    # keyword is a quoted string, so none of those may appear in it.
    cleaned = re.sub(r'[";,=]+', " ", term or "")
    return " ".join(cleaned.split())


def _api(host: str, resource: str, finder: str, **params) -> str:
    query = "&".join(
        [f"{k}={urllib.parse.quote(str(v), safe='.,')}" for k, v in params.items()]
        + [f"finder={finder}"]
    )
    return API.format(host=host, resource=resource, query=query)


def _parse_stamp(value) -> datetime.datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp


def _age_days(value) -> int | None:
    stamp = _parse_stamp(value)
    if stamp is None:
        return None
    return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)


def _locations(record: dict) -> str:
    cells = [record.get("PrimaryLocation")]
    for extra in record.get("secondaryLocations") or []:
        if isinstance(extra, dict):
            cells.append(extra.get("Name"))
    return "; ".join(dict.fromkeys(str(c).strip() for c in cells if c and str(c).strip()))


def _remote(record: dict, location: str) -> bool:
    return record.get("WorkplaceTypeCode") == "ORA_REMOTE" or "remote" in location.casefold()


def _employment_type(record: dict) -> str | None:
    labels = " ".join(
        str(record.get(key) or "")
        for key in ("JobType", "WorkerType", "RequisitionType", "ContractType")
    ).casefold()
    if "intern" in labels or "co-op" in labels:
        return "INTERN"
    if "temporary" in labels or "fixed term" in labels:
        return "TEMPORARY"
    if "contractor" in labels:
        return "CONTRACTOR"
    schedule = str(record.get("JobSchedule") or "").casefold()
    if "part" in schedule:
        return "PART_TIME"
    if "full" in schedule:
        return "FULL_TIME"
    return None


def _description(record: dict) -> str | None:
    parts = []
    for key in ("CorporateDescriptionStr", "OrganizationDescriptionStr", "ExternalDescriptionStr"):
        value = (record.get(key) or "").strip()
        if value:
            parts.append(value)
    # The site renders these under its own headings, which is what lets
    # parse_sections split them out.
    for heading, key in (
        ("Responsibilities", "ExternalResponsibilitiesStr"),
        ("Qualifications", "ExternalQualificationsStr"),
    ):
        value = (record.get(key) or "").strip()
        if value:
            parts.append(f"<h3>{heading}</h3>{value}")
    # Salary range and similar live in flex fields, as prompt and value.
    for field in record.get("requisitionFlexFields") or []:
        prompt = str((field or {}).get("Prompt") or "").strip()
        value = str((field or {}).get("Value") or "").strip()
        if prompt and value:
            parts.append(f"<p>{html.escape(prompt)}: {html.escape(value)}</p>")
    return "\n".join(parts) or None


def _us_facet(item: dict) -> str | None:
    for facet in item.get("locationsFacet") or []:
        if str((facet or {}).get("Name") or "").strip().casefold() in _US_NAMES:
            facet_id = facet.get("Id")
            if facet_id is not None and str(facet_id).isdigit():
                return str(facet_id)
    return None


def _search_item(payload) -> dict | None:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    return items[0]


class _Oracle:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = _host(parsed)
        if host in CUSTOM_HOSTS:
            pod = CUSTOM_HOSTS[host]
            needs_prefix = False
        elif _POD_HOST.fullmatch(host):
            pod = host
            needs_prefix = True
        else:
            return None
        found = _CE_PATH.match(parsed.path or "")
        if not found or (needs_prefix and not found.group("prefix")):
            return None
        return _ref(pod, found.group("site"), found.group("job_id"))

    def detail(self, fetcher, ref: Ref) -> Detail:
        parts = _board_parts(ref.board)
        if parts is None:
            raise Unavailable(f"oracle board {ref.board!r} is not pod/site")
        pod, site = parts
        url = _api(
            pod,
            "recruitingCEJobRequisitionDetails",
            f"ById;Id=%22{urllib.parse.quote(ref.job_id, safe='')}%22,siteNumber={site}",
            expand="all",
            onlyData="true",
        )
        payload = fetch_json(fetcher, url)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise Unavailable(f"unexpected oracle detail shape for {ref.url}")
        if not items:
            # The pod answers 200 with no items for a requisition that is no
            # longer posted externally; it does not 404.
            raise Gone(f"{ref.url}: no posted requisition {ref.job_id}")
        record = items[0] if isinstance(items[0], dict) else {}
        if str(record.get("Id") or "") != ref.job_id:
            raise Unavailable(f"oracle answered {record.get('Id')!r} for {ref.job_id}")
        location = _locations(record)
        closes = _parse_stamp(record.get("ExternalPostedEndDate"))
        legal = record.get("LegalEmployer")
        return Detail(
            ref=ref,
            company=_display_name(pod, legal.strip() if isinstance(legal, str) else ""),
            title=str(record.get("Title") or "").strip(),
            location=location,
            description_html=_description(record),
            remote=_remote(record, location),
            employment_type=_employment_type(record),
            deadline=closes.isoformat() if closes else None,
            age_days=_age_days(record.get("ExternalPostedStartDate")),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        parts = _board_parts(board)
        if parts is None:
            # None, not []: an empty list reads as "answered with nothing".
            log.warning("oracle board %r is not pod/site, skipping", board)
            return None
        pod, site = parts
        if not company or company == board:
            company = _display_name(pod)
        terms = [t for t in (_finder_term(t) for t in terms or ()) if t]
        if not terms:
            return None

        resource = "recruitingCEJobRequisitions"
        try:
            probe = fetch_json(
                fetcher,
                _api(pod, resource, f"findReqs;siteNumber={site},facetsList=LOCATIONS,limit=1",
                     onlyData="true"),
            )
        except (Gone, Unavailable) as error:
            log.warning("oracle %s: did not answer (%s), treating as absent", board, error)
            return None
        item = _search_item(probe)
        if item is None:
            log.warning("oracle %s: unexpected search shape, treating as absent", board)
            return None
        us = _us_facet(item)
        if us is None:
            log.info("oracle %s: no United States location facet, searching unfiltered", board)

        today = datetime.datetime.now(datetime.timezone.utc).date()
        seen: dict[str, Posting] = {}
        for term in terms:
            for page in range(MAX_PAGES):
                time.sleep(PAUSE)
                finder = (
                    f"findReqs;siteNumber={site},facetsList=LOCATIONS,limit={PAGE},"
                    f"offset={page * PAGE},keyword=%22{urllib.parse.quote(term, safe='')}%22,"
                    + (f"selectedLocationsFacet={us}," if us else "")
                    + "sortBy=RELEVANCY"
                )
                try:
                    payload = fetch_json(
                        fetcher,
                        _api(pod, resource, finder, onlyData="true",
                             expand="requisitionList.secondaryLocations"),
                    )
                except (Gone, Unavailable) as error:
                    # A partial board is not an answer: report it as absent.
                    log.warning("oracle %s: query %r failed (%s), treating as absent", board, term, error)
                    return None
                item = _search_item(payload)
                if item is None:
                    log.warning("oracle %s: unexpected search shape, treating as absent", board)
                    return None
                rows = item.get("requisitionList") or []
                for record in rows:
                    job_id = str((record or {}).get("Id") or "")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", job_id) or job_id in seen:
                        continue
                    closes = _parse_stamp(record.get("PostingEndDate"))
                    if closes is not None and closes.date() < today:
                        continue
                    ref = _ref(pod, site, job_id)
                    location = _locations(record)
                    seen[job_id] = Posting(
                        source=NAME,
                        board=board,
                        company=company,
                        title=str(record.get("Title") or "").strip(),
                        location=location,
                        url=ref.url,
                        remote=_remote(record, location),
                        provider_id=ref.provider_id,
                        age_days=_age_days(record.get("PostedDate")),
                    )
                total = item.get("TotalJobsCount")
                if len(rows) < PAGE or (isinstance(total, int) and (page + 1) * PAGE >= total):
                    break
        return list(seen.values())


ADAPTER = _Oracle()
