"""SmartRecruiters, through its public Posting API.

api.smartrecruiters.com/v1/companies/<company>/postings needs no key and
answers a keyword query with the US filter applied server side. The company
identifier is the first path segment of every posting URL
(jobs.smartrecruiters.com/LLNL/3743990015408206-...), and the API treats it
case-insensitively.

One quirk worth knowing: an unknown company is not a 404 on the list, it is a
200 with nothing in it. So a misspelt board reads as "nothing open" rather
than "did not answer"; the detail endpoint does 404 for it.
"""
from __future__ import annotations

import datetime
import html
import logging
import re
import urllib.parse

from . import Detail, Gone, Ref, Unavailable, fetch_json
from ..models import Posting

log = logging.getLogger(__name__)

NAME = "smartrecruiters"
HOST = "jobs.smartrecruiters.com"
API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
VIEW = "https://jobs.smartrecruiters.com/{company}/{job_id}"
VIEW_UUID = "https://jobs.smartrecruiters.com/ni/{company}/{job_id}"
PAGE = 100          # the API's maximum page size
MAX_PAGES = 3       # 300 hits for one infrastructure word is far past relevant

# The API's own company.name is often just the identifier again ("LLNL").
KNOWN_NAMES = {
    "llnl": "Lawrence Livermore National Laboratory",
    "wellmarkinc": "Wellmark Blue Cross Blue Shield",
}

_COMPANY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_NUMERIC = re.compile(r"(\d{6,20})(?:-.*)?")
_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:-.*)?", re.I)

# typeOfEmployment ids, mapped to schema.org employmentType.
_EMPLOYMENT = {
    "permanent": "FULL_TIME",
    "full_time": "FULL_TIME",
    "fulltime": "FULL_TIME",
    "part_time": "PART_TIME",
    "parttime": "PART_TIME",
    "contract": "CONTRACTOR",
    "contractor": "CONTRACTOR",
    "freelance": "CONTRACTOR",
    "temporary": "TEMPORARY",
    "intern": "INTERN",
    "internship": "INTERN",
    "volunteer": "VOLUNTEER",
}

# jobAd sections in reading order, with a heading for when the posting has none.
_SECTIONS = (
    ("companyDescription", "About the company"),
    ("jobDescription", "The role"),
    ("qualifications", "Requirements"),
    ("additionalInformation", "Additional information"),
)


def _company_name(identifier: str, payload_name: str | None = None) -> str:
    known = KNOWN_NAMES.get(identifier.casefold())
    if known:
        return known
    name = (payload_name or "").strip()
    if name and name.casefold() != identifier.casefold():
        return name
    # "WellmarkInc" -> "Wellmark Inc"
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", identifier) if identifier else ""


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
        stamp = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.isoformat()


def _location(job: dict) -> str:
    place = job.get("location") or {}
    full = (place.get("fullLocation") or "").strip()
    if not full:
        full = ", ".join(
            str(place[key]).strip()
            for key in ("city", "region", "country")
            if place.get(key)
        )
    if not full and place.get("remote"):
        return "Remote"
    return full


def _employment(job: dict) -> str | None:
    kind = (job.get("typeOfEmployment") or {}).get("id") or ""
    level = (job.get("experienceLevel") or {}).get("id") or ""
    if level.casefold() == "internship":
        return "INTERN"
    return _EMPLOYMENT.get(kind.casefold().replace("-", "_"))


def _ref(company: str, job_id: str) -> Ref:
    # The public page takes a publication uuid only under /ni/.
    view = VIEW if job_id.isdigit() else VIEW_UUID
    return Ref(
        provider=NAME,
        board=company,
        job_id=job_id,
        url=view.format(company=company, job_id=job_id),
    )


class _SmartRecruiters:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        if (parsed.hostname or "").casefold() != HOST:
            return None
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        company = posting = None
        if len(parts) >= 2 and parts[0] == "ni":
            # jobs.smartrecruiters.com/ni/<Company>/<uuid>-<slug>
            if len(parts) >= 3:
                company, posting = parts[1], parts[2]
        elif len(parts) >= 4 and parts[0] in ("oneclick-ui", "external-referrals") \
                and parts[1] == "company" and "publication" in parts:
            # .../company/<Company>/publication/<uuid>
            at = parts.index("publication")
            if at == 3 and len(parts) > at + 1:
                company, posting = parts[2], parts[at + 1]
        elif len(parts) >= 2:
            company, posting = parts[0], parts[1]
        if not company or not posting or not _COMPANY.fullmatch(company):
            return None
        found = _NUMERIC.fullmatch(posting) or _UUID.fullmatch(posting)
        if not found:
            return None
        return _ref(company, found.group(1).lower())

    def detail(self, fetcher, ref: Ref) -> Detail:
        url = f"{API.format(company=urllib.parse.quote(ref.board, safe=''))}/{urllib.parse.quote(ref.job_id, safe='')}"
        payload = fetch_json(fetcher, url)
        if not isinstance(payload, dict) or not payload.get("name"):
            raise Unavailable(f"unexpected SmartRecruiters body for {ref.provider_id}")
        if payload.get("active") is False:
            raise Gone(f"{ref.provider_id} is no longer active")

        # A uuid URL or a differently-cased company names the same posting as
        # the numeric form the scout finds, so hand back the canonical one.
        identifier = str((payload.get("company") or {}).get("identifier") or ref.board)
        numeric = str(payload.get("id") or "")
        if numeric.isdigit() and (numeric != ref.job_id or identifier != ref.board):
            ref = _ref(identifier, numeric)

        sections = ((payload.get("jobAd") or {}).get("sections")) or {}
        parts = []
        for key, fallback in _SECTIONS:
            section = sections.get(key)
            if isinstance(section, dict) and (section.get("text") or "").strip():
                title = section.get("title") or fallback
                parts.append(f"<h2>{html.escape(str(title))}</h2>{section['text']}")
        location = _location(payload)
        return Detail(
            ref=ref,
            company=_company_name(identifier, (payload.get("company") or {}).get("name")),
            title=(payload.get("name") or "").strip(),
            location=location,
            description_html="".join(parts) or None,
            remote=bool((payload.get("location") or {}).get("remote")),
            employment_type=_employment(payload),
            # The date SmartRecruiters will take the ad down on its own.
            deadline=_iso(payload.get("unpublishOn")),
            age_days=_age_days(payload.get("releasedDate")),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        if not _COMPANY.fullmatch(board or ""):
            log.warning("smartrecruiters board %r is not a company identifier, skipping", board)
            return None
        base = API.format(company=urllib.parse.quote(board, safe=""))
        seen: dict[str, Posting] = {}
        answered = False
        for term in terms or ():
            for page in range(MAX_PAGES):
                query = urllib.parse.urlencode(
                    {"q": term, "country": "us", "limit": PAGE, "offset": page * PAGE}
                )
                try:
                    payload = fetch_json(fetcher, f"{base}?{query}")
                except (Gone, Unavailable) as error:
                    # Partial results would let the close rule retire whatever
                    # the failed query would have returned.
                    log.warning("smartrecruiters %s: query %r failed (%s), treating the board as absent", board, term, error)
                    return None
                if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                    log.warning("smartrecruiters %s: unexpected body for %r", board, term)
                    return None
                answered = True
                rows = payload["content"]
                for job in rows:
                    job_id = str(job.get("id") or "")
                    if not job_id or job_id in seen:
                        continue
                    identifier = str((job.get("company") or {}).get("identifier") or board)
                    ref = _ref(identifier, job_id)
                    location = _location(job)
                    seen[job_id] = Posting(
                        source=NAME,
                        board=board,
                        company=company or _company_name(identifier, (job.get("company") or {}).get("name")),
                        title=(job.get("name") or "").strip(),
                        location=location,
                        url=ref.url,
                        remote=bool((job.get("location") or {}).get("remote")) or "remote" in location.casefold(),
                        provider_id=ref.provider_id,
                        age_days=_age_days(job.get("releasedDate")),
                        employment_type=_employment(job),
                    )
                total = payload.get("totalFound")
                if len(rows) < PAGE or (isinstance(total, int) and (page + 1) * PAGE >= total):
                    break
        # No terms means no request was made, which is not "nothing open".
        return list(seen.values()) if answered else None


ADAPTER = _SmartRecruiters()
