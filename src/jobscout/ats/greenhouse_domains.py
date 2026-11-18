"""Greenhouse boards that live on the company's own domain.

Datadog, Jane Street, Waymo and Zipline all run Greenhouse, but their posting
links point at their own careers sites, so the boards.greenhouse.io host check
never sees them. Each site carries the Greenhouse job id in the URL, either as
the `gh_jid` query parameter Greenhouse's embed uses or as a path segment, and
the posting itself is read from the public board API like any other
Greenhouse job.

These are the same jobs the scout already finds by scanning the board slug,
so the ref is filed as provider "greenhouse" with the plain
`greenhouse:<id>` key. A posting captured from careers.datadoghq.com and the
one the scout found on the datadog board must land on the same row. For the
same reason there is no search here: the slug scan already covers it.

Every domain below was checked live (2026-11): the board's own absolute_url
for each job names that domain, and the path forms listed are the ones the
site serves.
"""
from __future__ import annotations

import dataclasses
import datetime
import html
import logging
import re
import urllib.parse

from . import Detail, Ref, Unavailable, fetch_json

log = logging.getLogger(__name__)

NAME = "greenhouse-domains"
API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"


@dataclasses.dataclass(frozen=True)
class _Site:
    board: str           # Greenhouse board slug
    company: str         # display name, for when the API leaves it blank
    path: re.Pattern     # a posting path that carries the id itself
    view: str            # the board API's own absolute_url form


_DATADOG = _Site(
    "datadog", "Datadog",
    # /detail/8257324/?gh_jid=8257324
    re.compile(r"/detail/(\d+)/?"),
    "https://careers.datadoghq.com/detail/{id}/?gh_jid={id}",
)
_JANE_STREET = _Site(
    "janestreet", "Jane Street",
    # /join-jane-street/position/8213653002/ is the description page and
    # /join-jane-street/apply/8213653002/?gh_jid=... the board's own link.
    re.compile(r"/join-jane-street/(?:position|apply)/(\d+)/?"),
    "https://www.janestreet.com/join-jane-street/apply/{id}?gh_jid={id}",
)
_WAYMO = _Site(
    "waymo", "Waymo",
    # /jobs?gh_jid=8257205 redirects to /en-US/jobs/8257205?gh_jid=... and on
    # to a title slug, which drops the id unless gh_jid rides along.
    re.compile(r"/(?:[a-z]{2}-[a-z]{2}/)?jobs/(\d+)/?", re.I),
    "https://careers.withwaymo.com/jobs?gh_jid={id}",
)
_ZIPLINE = _Site(
    "flyzipline", "Zipline",
    # /open-roles/7895360003?gh_jid=7895360003
    re.compile(r"/open-roles/(\d+)/?"),
    "https://www.zipline.com/open-roles/{id}?gh_jid={id}",
)

# Exact hosts only. The bare janestreet.com and zipline.com redirect to www,
# so a link pasted without it is still theirs.
SITES: dict[str, _Site] = {
    "careers.datadoghq.com": _DATADOG,
    "www.janestreet.com": _JANE_STREET,
    "janestreet.com": _JANE_STREET,
    "careers.withwaymo.com": _WAYMO,
    "www.zipline.com": _ZIPLINE,
    "zipline.com": _ZIPLINE,
}

_JOB_ID = re.compile(r"\d{1,20}")

# Greenhouse has no employment type field. Companies put one in custom
# metadata under their own names: Datadog's "Time Type" plus "Early Career
# Time Type", Jane Street's "Employment Type", Waymo's "Job Level".
_EMPLOYMENT_FIELD = re.compile(r"time type|employment type|job type|job level|worker type", re.I)
_EMPLOYMENT_VALUE = (
    ("INTERN", re.compile(r"\bintern|\bco-?op\b", re.I)),
    ("CONTRACTOR", re.compile(r"contract", re.I)),
    ("TEMPORARY", re.compile(r"\btemp|seasonal|fixed[- ]term", re.I)),
    ("PART_TIME", re.compile(r"part[- ]?time", re.I)),
    ("FULL_TIME", re.compile(r"full[- ]?time|permanent|regular", re.I)),
)


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


def _iso(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _location(payload: dict) -> str:
    name = str((payload.get("location") or {}).get("name") or "")
    places = [part.strip() for part in name.split(";") if part.strip()]
    if not places:
        places = [
            str(office.get("location") or office.get("name") or "").strip()
            for office in payload.get("offices") or []
            if isinstance(office, dict)
        ]
    return "; ".join(dict.fromkeys(p for p in places if p))


def _employment(metadata) -> str | None:
    labels = []
    for item in metadata or []:
        if not isinstance(item, dict) or not _EMPLOYMENT_FIELD.search(str(item.get("name") or "")):
            continue
        value = item.get("value")
        labels.extend(v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str))
    # Datadog marks an internship "Full time" as well, so the stronger
    # label wins rather than whichever field came first.
    for kind, pattern in _EMPLOYMENT_VALUE:
        if any(pattern.search(label) for label in labels):
            return kind
    return None


def ref_for(site: _Site, job_id: str) -> Ref:
    return Ref(
        provider="greenhouse",
        board=site.board,
        job_id=job_id,
        url=site.view.format(id=job_id),
        provider_id=f"greenhouse:{job_id}",
    )


class _GreenhouseDomains:
    name = NAME
    # The scout already scans these boards by slug through the plain
    # Greenhouse provider, under the same provider ids.
    search = None

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        site = SITES.get((parsed.hostname or "").casefold())
        if site is None:
            return None
        values = urllib.parse.parse_qs(parsed.query).get("gh_jid") or []
        job_id = values[0].strip() if values else ""
        if not _JOB_ID.fullmatch(job_id):
            found = site.path.fullmatch(urllib.parse.unquote(parsed.path or ""))
            job_id = found.group(1) if found else ""
        if not _JOB_ID.fullmatch(job_id):
            return None
        return ref_for(site, job_id)

    def detail(self, fetcher, ref: Ref) -> Detail:
        payload = fetch_json(fetcher, API.format(board=ref.board, job_id=ref.job_id))
        if not isinstance(payload, dict) or not str(payload.get("title") or "").strip():
            raise Unavailable(f"unexpected Greenhouse body for {ref.board}/{ref.job_id}")
        site = next((s for s in SITES.values() if s.board == ref.board), None)
        company = str(payload.get("company_name") or "").strip() or (site.company if site else ref.board)
        content = str(payload.get("content") or "")
        location = _location(payload)
        return Detail(
            ref=ref,
            company=company,
            title=str(payload["title"]).strip(),
            location=location,
            # The board API escapes its markup ("&lt;p&gt;"), so one unescape
            # gives the HTML the posting page renders.
            description_html=html.unescape(content) if content.strip() else None,
            remote="remote" in location.casefold(),
            employment_type=_employment(payload.get("metadata")),
            deadline=_iso(payload.get("application_deadline")),
            age_days=_age_days(payload.get("first_published")),
        )


ADAPTER = _GreenhouseDomains()
