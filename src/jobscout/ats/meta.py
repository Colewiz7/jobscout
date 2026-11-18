"""Meta careers (metacareers.com), one posting at a time.

Each job page is server rendered with a schema.org JobPosting block, which is
what this reads. There is no search: the site's own search is a GraphQL call
that needs the LSD token scraped from a page first, and the public jobsearch
sitemap lists ids without titles, so scanning it would mean opening every one
of its ~1,400 pages (each over 600 KB).

A posting that is gone does not 404. The page comes back 200 as the job
details shell for that id with no JobPosting, no canonical link and no
og:title. A hiccup on Meta's side could plausibly render the same shell, so
that alone is never read as closed: the id also has to be missing from the
jobsearch sitemap, which lists every open posting.

robots.txt allows /profile/job_details/ and /jobsearch/sitemap.xml for all
agents, but opens with Facebook's notice that automated collection needs
written permission. This reads one posting per request, only for jobs already
captured, and never sweeps the sitemap for content.
"""
from __future__ import annotations

import datetime
import html
import json
import logging
import re
import urllib.parse

from . import Detail, Gone, Ref, Unavailable, fetch_text

log = logging.getLogger(__name__)

NAME = "meta"
COMPANY = "Meta"

VIEW = "https://www.metacareers.com/profile/job_details/{job_id}/"
SITEMAP = "https://www.metacareers.com/jobsearch/sitemap.xml"

HOSTS = {"www.metacareers.com", "metacareers.com"}

# /profile/job_details/123/, and the older /jobs/123/ and /v2/jobs/123/ that
# now redirect to it.
_PATH = re.compile(r"/(?:profile/job_details|jobs|v2/jobs)/(\d{6,20})/?")
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def _jobposting(page: str) -> dict | None:
    for block in _LD.findall(page or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for candidate in data if isinstance(data, list) else [data]:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return None


def _is_job_shell(page: str, job_id: str) -> bool:
    """The job details route rendered for this id, whether or not it has a job."""
    return (
        "CandidatePortalJobDetailsView" in page
        and f'"requisition_id":"{job_id}"' in page
    )


def _paragraphs(text: str) -> str:
    out = []
    for block in re.split(r"\n\s*\n", text or ""):
        lines = [html.escape(line.strip()) for line in block.splitlines() if line.strip()]
        if lines:
            out.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(out)


def _items(value: str) -> list[str]:
    # Meta separates list items with a literal "&nbsp;" and nothing else.
    parts = re.split(r"&nbsp;| ", value or "")
    return [html.unescape(part).strip() for part in parts if part.strip()]


def _description(posting: dict) -> str | None:
    out = [_paragraphs(str(posting.get("description") or ""))]
    for key, heading in (("responsibilities", "Responsibilities"), ("qualifications", "Qualifications")):
        items = _items(str(posting.get(key) or ""))
        if items:
            out.append(f"<h3>{heading}</h3><ul>")
            out.extend(f"<li>{html.escape(item)}</li>" for item in items)
            out.append("</ul>")
    body = "".join(out)
    return body or None


def _location(posting: dict) -> str:
    places = posting.get("jobLocation")
    if isinstance(places, dict):
        places = [places]
    cells = []
    for place in places or []:
        if not isinstance(place, dict):
            continue
        name = str(place.get("name") or "").strip()
        if not name:
            address = place.get("address") or {}
            name = ", ".join(
                str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry")
                if isinstance(address, dict) and address.get(key)
            )
        if name:
            cells.append(name)
    return "; ".join(dict.fromkeys(cells))


def _stamp(value) -> datetime.datetime | None:
    try:
        stamp = datetime.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=datetime.timezone.utc)


def _age_days(value) -> int | None:
    stamp = _stamp(value)
    if stamp is None:
        return None
    return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)


def _employment_type(posting: dict) -> str | None:
    value = posting.get("employmentType")
    if isinstance(value, list):
        value = value[0] if value else None
    value = str(value or "").strip()
    return value or None


def _ref(job_id: str) -> Ref:
    return Ref(provider=NAME, board=NAME, job_id=job_id, url=VIEW.format(job_id=job_id))


class _Meta:
    name = NAME
    search = None

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = (parsed.hostname or "").rstrip(".").casefold()
        if host not in HOSTS:
            return None
        found = _PATH.fullmatch(parsed.path or "")
        return _ref(found.group(1)) if found else None

    def detail(self, fetcher, ref: Ref) -> Detail:
        if not re.fullmatch(r"\d{6,20}", ref.job_id or ""):
            raise Unavailable(f"meta job id {ref.job_id!r} is not numeric")
        url = VIEW.format(job_id=ref.job_id)
        page = fetch_text(fetcher, url)
        posting = _jobposting(page)
        if posting is None:
            raise self._missing(fetcher, page, ref.job_id)
        title = str(posting.get("title") or "").strip()
        if not title:
            raise Unavailable(f"meta job {ref.job_id} has a JobPosting without a title")
        location = _location(posting)
        closes = _stamp(posting.get("validThrough"))
        org = posting.get("hiringOrganization")
        company = str(org.get("name") or "").strip() if isinstance(org, dict) else ""
        return Detail(
            ref=_ref(ref.job_id),
            company=company or COMPANY,
            title=title,
            location=location,
            description_html=_description(posting),
            remote=posting.get("jobLocationType") == "TELECOMMUTE",
            employment_type=_employment_type(posting),
            deadline=closes.isoformat() if closes else None,
            age_days=_age_days(posting.get("datePosted")),
        )

    def _missing(self, fetcher, page: str, job_id: str) -> Exception:
        """Why a page had no posting: Gone only when the page and sitemap agree."""
        if not _is_job_shell(page, job_id):
            return Unavailable(f"meta job {job_id}: page is not the job details view")
        try:
            sitemap = fetch_text(fetcher, SITEMAP)
        except Gone as error:
            return Unavailable(f"meta sitemap: {error}", 404)
        if "<urlset" not in sitemap or "/profile/job_details/" not in sitemap:
            return Unavailable("meta sitemap did not list any jobs")
        if f"/profile/job_details/{job_id}/" in sitemap:
            return Unavailable(f"meta job {job_id} is in the sitemap but its page had no posting")
        return Gone(f"meta job {job_id} has no posting and is not in the jobs sitemap")


ADAPTER = _Meta()
