"""Workable, through the JSON its own careers pages call.

apply.workable.com/<account> is a single-page app, but the two calls behind it
are public: a POST to /api/v3/accounts/<account>/jobs with a query body for
search, and a GET of /api/v2/accounts/<account>/jobs/<shortcode> for one
posting. Both 404 for an account that does not exist, and the detail 404s
("Job not found") once a posting is taken down.

The account is the slug in the posting URL, apply.workable.com/mindex/j/...,
and also the subdomain of the older mindex.workable.com/j/... links.
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

NAME = "workable"
HOST = "apply.workable.com"
SEARCH = "https://apply.workable.com/api/v3/accounts/{account}/jobs"
DETAIL = "https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}"
VIEW = "https://apply.workable.com/{account}/j/{shortcode}/"
MAX_PAGES = 3

# Subdomains of workable.com that are Workable's own, not a company's.
_NOT_ACCOUNTS = {"apply", "www", "jobs", "help", "api", "resources", "blog", "careers", "app", "marketplace"}

KNOWN_NAMES = {
    "mindex": "Mindex",
}

_ACCOUNT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?")
_SHORTCODE = re.compile(r"[A-Za-z0-9]{6,16}")
_US = [{"country": "United States", "countryCode": "US"}]
_CLOSED = {"closed", "archived"}

# Workable's type field, mapped to schema.org employmentType.
_EMPLOYMENT = {
    "full": "FULL_TIME",
    "full_time": "FULL_TIME",
    "part": "PART_TIME",
    "part_time": "PART_TIME",
    "contract": "CONTRACTOR",
    "temporary": "TEMPORARY",
    "internship": "INTERN",
    "intern": "INTERN",
    "volunteer": "VOLUNTEER",
}


def _company_name(account: str) -> str:
    return KNOWN_NAMES.get(account) or " ".join(w.capitalize() for w in account.split("-") if w)


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


def _place(place: dict) -> str:
    return ", ".join(
        str(place[key]).strip()
        for key in ("city", "region", "country")
        if place.get(key)
    )


def _location(job: dict) -> str:
    places = [p for p in job.get("locations") or [] if isinstance(p, dict) and not p.get("hidden")]
    if not places and isinstance(job.get("location"), dict):
        places = [job["location"]]
    cells = [cell for cell in (_place(p) for p in places) if cell]
    if not cells and _remote(job):
        return "Remote"
    return "; ".join(dict.fromkeys(cells))


def _remote(job: dict) -> bool:
    return bool(job.get("remote")) or (job.get("workplace") or "").casefold() == "remote"


def _employment(job: dict) -> str | None:
    return _EMPLOYMENT.get((job.get("type") or "").casefold().replace("-", "_"))


def _ref(account: str, shortcode: str) -> Ref:
    return Ref(
        provider=NAME,
        board=account,
        job_id=shortcode,
        url=VIEW.format(account=account, shortcode=shortcode),
    )


class _Workable:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = (parsed.hostname or "").casefold()
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if host == HOST:
            # apply.workable.com/<account>/j/<shortcode>/[apply/]
            if len(parts) < 3 or parts[1] != "j":
                return None
            account, shortcode = parts[0].casefold(), parts[2]
        elif host.endswith(".workable.com") and host.count(".") == 2:
            # <account>.workable.com/j/<shortcode>, the older form
            account = host.split(".", 1)[0]
            if account in _NOT_ACCOUNTS or len(parts) < 2 or parts[0] != "j":
                return None
            shortcode = parts[1]
        else:
            return None
        if not _ACCOUNT.fullmatch(account) or not _SHORTCODE.fullmatch(shortcode):
            return None
        return _ref(account, shortcode.upper())

    def detail(self, fetcher, ref: Ref) -> Detail:
        url = DETAIL.format(
            account=urllib.parse.quote(ref.board, safe=""),
            shortcode=urllib.parse.quote(ref.job_id, safe=""),
        )
        payload = fetch_json(fetcher, url)
        if not isinstance(payload, dict) or not payload.get("title"):
            raise Unavailable(f"unexpected Workable body for {ref.provider_id}")
        if (payload.get("state") or "").casefold() in _CLOSED:
            raise Gone(f"{ref.provider_id} is {payload['state']}")

        parts = []
        for key, heading in (("description", None), ("requirements", "Requirements"), ("benefits", "Benefits")):
            body = (payload.get(key) or "").strip()
            if body:
                parts.append(f"<h2>{html.escape(heading)}</h2>{body}" if heading else body)
        return Detail(
            ref=ref,
            company=_company_name(ref.board),
            title=(payload.get("title") or "").strip(),
            location=_location(payload),
            description_html="".join(parts) or None,
            remote=_remote(payload),
            employment_type=_employment(payload),
            age_days=_age_days(payload.get("published")),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        if not _ACCOUNT.fullmatch(board or ""):
            log.warning("workable board %r is not an account slug, skipping", board)
            return None
        url = SEARCH.format(account=board)
        seen: dict[str, Posting] = {}
        answered = False
        for term in terms or ():
            token = None
            for _ in range(MAX_PAGES):
                body = {
                    "query": term,
                    "location": _US,
                    "department": [],
                    "worktype": [],
                    "remote": [],
                }
                if token:
                    body["token"] = token
                try:
                    payload = fetch_json(fetcher, url, method="POST", body=body)
                except (Gone, Unavailable) as error:
                    # A 404 here is an account that does not exist, which is
                    # a bad board rather than a board with nothing open.
                    log.warning("workable %s: query %r failed (%s), treating the board as absent", board, term, error)
                    return None
                if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                    log.warning("workable %s: unexpected body for %r", board, term)
                    return None
                answered = True
                for job in payload["results"]:
                    shortcode = str(job.get("shortcode") or "")
                    if not shortcode or shortcode in seen or job.get("isInternal"):
                        continue
                    if (job.get("state") or "published").casefold() != "published":
                        continue
                    ref = _ref(board, shortcode.upper())
                    location = _location(job)
                    seen[shortcode] = Posting(
                        source=NAME,
                        board=board,
                        company=company or _company_name(board),
                        title=(job.get("title") or "").strip(),
                        location=location,
                        url=ref.url,
                        remote=_remote(job) or "remote" in location.casefold(),
                        provider_id=ref.provider_id,
                        age_days=_age_days(job.get("published")),
                        employment_type=_employment(job),
                    )
                token = payload.get("nextPage")
                if not token:
                    break
        return list(seen.values()) if answered else None


ADAPTER = _Workable()
