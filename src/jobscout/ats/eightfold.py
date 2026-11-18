"""Eightfold career sites, through the PCSX API behind the page.

Every Eightfold site, on its own subdomain of eightfold.ai or on a company
domain such as jobs.nvidia.com, serves the same two JSON calls its page uses:
`/api/pcsx/search` and `/api/pcsx/position_details`. Both need a `domain`
parameter naming the tenant (nvidia.com), and a domain the host does not serve
gets an HTML error page back, so a board here is "<host>/<domain>".

The older `/api/apply/v2/jobs` API still answers, but only with 403 "Not
authorized for PCSX" on every tenant checked (NVIDIA, Qualcomm, Micron,
PayPal), so it is not used.

Old Microsoft links on jobs.careers.microsoft.com are not matched: they carry
Microsoft's own job number and redirect to the search page, not the posting.
"""
from __future__ import annotations

import datetime
import logging
import re
import time
import urllib.parse

from ..models import Posting
from . import Detail, Gone, Ref, Unavailable, fetch_json

log = logging.getLogger(__name__)

NAME = "eightfold"

SEARCH = "https://{host}/api/pcsx/search?{query}"
DETAIL = "https://{host}/api/pcsx/position_details?{query}"
VIEW = "https://{host}/careers/job/{job_id}"

PAGE = 10            # PCSX ignores any page size it is asked for
MAX_PAGES = 5        # 50 hits per term, well past anything relevant
PAUSE = 1.5          # Microsoft's front end answered a quick second request with 429
US = "United States"

# Company domains serving an Eightfold site, verified live 2026-11-18.
# host -> (Eightfold domain parameter, display name)
CUSTOM_HOSTS: dict[str, tuple[str, str]] = {
    "jobs.nvidia.com": ("nvidia.com", "NVIDIA"),
    "apply.careers.microsoft.com": ("microsoft.com", "Microsoft"),
    "careers.deere.com": ("johndeere.com", "John Deere"),
    "careers.qualcomm.com": ("qualcomm.com", "Qualcomm"),
}
# The canonical host for a known tenant, so the same posting reached through
# nvidia.eightfold.ai and jobs.nvidia.com is one board.
_HOST_FOR_DOMAIN = {domain: host for host, (domain, _) in CUSTOM_HOSTS.items()}
_NAMES = {domain: name for domain, name in CUSTOM_HOSTS.values()}
# Tenants served only from <tenant>.eightfold.ai, verified live 2026-11-18.
_NAMES["lockheedmartin.com"] = "Lockheed Martin"

_TENANT_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.eightfold\.ai")
# Shared front doors, where the subdomain says nothing about the tenant.
_SHARED_LABELS = {"app", "www", "careers", "jobs"}
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_JOB_PATH = re.compile(r"/careers/job/(\d{1,20})(?:[/?#]|$)")
_PID = re.compile(r"\d{1,20}")
_REMOTE_OPTIONS = {"remote", "remote_local", "remote_global"}


def _host(parsed: urllib.parse.SplitResult) -> str:
    return (parsed.hostname or "").rstrip(".").casefold()


def _display_name(domain: str) -> str:
    known = _NAMES.get(domain)
    if known:
        return known
    stem = domain.split(".", 1)[0]
    return stem[:1].upper() + stem[1:]


def _canonical(host: str, domain: str) -> str:
    return _HOST_FOR_DOMAIN.get(domain, host)


def _ref(host: str, domain: str, job_id: str) -> Ref:
    host = _canonical(host, domain)
    return Ref(
        provider=NAME,
        board=f"{host}/{domain}",
        job_id=job_id,
        url=VIEW.format(host=host, job_id=job_id),
        # The domain names the tenant whichever host served it, so dedupe on it.
        provider_id=f"{NAME}:{domain}:{job_id}",
    )


def _board_parts(board: str) -> tuple[str, str] | None:
    """(host, domain) from "<host>/<domain>", or a bare known host or domain.

    The host must be one this adapter owns, same as match(), so a board line
    in the config cannot point the scout at an arbitrary site.
    """
    board = (board or "").strip().casefold().rstrip("/")
    host, _, domain = board.partition("/")
    if domain:
        owned = host in CUSTOM_HOSTS or _TENANT_HOST.fullmatch(host)
        if owned and _DOMAIN.fullmatch(domain):
            return host, domain
        return None
    if board in CUSTOM_HOSTS:
        return board, CUSTOM_HOSTS[board][0]
    if board in _HOST_FOR_DOMAIN:
        return _HOST_FOR_DOMAIN[board], board
    return None


def _age_days(epoch) -> int | None:
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return None
    stamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)


def _locations(position: dict) -> str:
    cells = position.get("locations") or []
    if not cells and position.get("location"):
        cells = [position["location"]]
    if not cells:
        cells = position.get("standardizedLocations") or []
    return "; ".join(dict.fromkeys(str(c).strip() for c in cells if c and str(c).strip()))


def _remote(position: dict, location: str) -> bool:
    option = str(position.get("workLocationOption") or "").casefold()
    return option in _REMOTE_OPTIONS or "remote" in location.casefold()


def _first(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _employment_type(position: dict) -> str | None:
    """Schema.org-style label from whichever custom field the tenant fills."""
    for key in ("efcustomTextEmploymentType", "efcustomTextTimeType", "efcustomTextJobType"):
        text = _first(position.get(key)).casefold()
        if not text:
            continue
        if "intern" in text or "co-op" in text:
            return "INTERN"
        if "part" in text:
            return "PART_TIME"
        if "temp" in text or "fixed term" in text:
            return "TEMPORARY"
        if "contract" in text:
            return "CONTRACTOR"
        if "full" in text or "regular" in text:
            return "FULL_TIME"
    return None


class _Eightfold:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = _host(parsed)
        query = urllib.parse.parse_qs(parsed.query)
        known = CUSTOM_HOSTS.get(host)
        if known:
            domain = known[0]
        elif _TENANT_HOST.fullmatch(host):
            asked = (query.get("domain") or [""])[0].strip().casefold()
            if asked and _DOMAIN.fullmatch(asked):
                domain = asked
            else:
                label = host.split(".", 1)[0]
                if label in _SHARED_LABELS:
                    return None
                # Tenant subdomains are named for the company: nvidia.eightfold.ai
                # serves nvidia.com. A wrong guess only costs an Unavailable.
                domain = f"{label}.com"
        else:
            return None

        found = _JOB_PATH.search(parsed.path or "")
        if found:
            job_id = found.group(1)
        elif (parsed.path or "").startswith("/careers"):
            # The older share link: /careers?pid=<id>&domain=<tenant>
            job_id = (query.get("pid") or [""])[0].strip()
            if not _PID.fullmatch(job_id):
                return None
        else:
            return None
        return _ref(host, domain, job_id)

    def detail(self, fetcher, ref: Ref) -> Detail:
        parts = _board_parts(ref.board)
        if parts is None:
            raise Unavailable(f"eightfold board {ref.board!r} is not host/domain")
        host, domain = parts
        query = urllib.parse.urlencode(
            {"position_id": ref.job_id, "domain": domain, "hl": "en"}
        )
        payload = fetch_json(fetcher, DETAIL.format(host=host, query=query))
        if not isinstance(payload, dict):
            raise Unavailable(f"unexpected eightfold detail shape for {ref.url}")
        position = payload.get("data") or {}
        if not isinstance(position, dict) or not position.get("id"):
            # The API mirrors its HTTP status in the body; a 404 there is the
            # same "Position not found" a real 404 carries.
            if payload.get("status") in (404, 410):
                error = payload.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                raise Gone(f"{ref.url}: {message or 'not found'}")
            raise Unavailable(f"eightfold detail for {ref.url} had no position")
        if str(position.get("id")) != ref.job_id:
            raise Unavailable(f"eightfold answered {position.get('id')} for {ref.job_id}")
        location = _locations(position)
        return Detail(
            ref=ref,
            company=_display_name(domain),
            title=str(position.get("name") or "").strip(),
            location=location,
            description_html=position.get("jobDescription") or None,
            remote=_remote(position, location),
            employment_type=_employment_type(position),
            deadline=None,
            age_days=_age_days(position.get("postedTs")),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        parts = _board_parts(board)
        if parts is None:
            # None, not []: an empty list reads as "answered with nothing" and
            # would let the close rule retire everything this board ever had.
            log.warning("eightfold board %r is not host/domain, skipping", board)
            return None
        host, domain = parts
        if not company or company == board:
            company = _display_name(domain)

        seen: dict[str, Posting] = {}
        answered = False
        first = True
        for term in terms or ():
            for page in range(MAX_PAGES):
                if not first:
                    time.sleep(PAUSE)
                first = False
                query = urllib.parse.urlencode(
                    {"domain": domain, "query": term, "location": US, "start": page * PAGE}
                )
                try:
                    payload = fetch_json(fetcher, SEARCH.format(host=host, query=query))
                except (Gone, Unavailable) as error:
                    # A partial board is not an answer: report it as absent.
                    log.warning("eightfold %s: query %r failed (%s), treating as absent", board, term, error)
                    return None
                data = (payload or {}).get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
                    log.warning("eightfold %s: unexpected search shape, treating as absent", board)
                    return None
                answered = True
                rows = data["positions"]
                for position in rows:
                    raw_id = position.get("id") if isinstance(position, dict) else None
                    job_id = "" if raw_id is None else str(raw_id)
                    if not _PID.fullmatch(job_id) or job_id in seen:
                        continue
                    ref = _ref(host, domain, job_id)
                    location = _locations(position)
                    seen[job_id] = Posting(
                        source=NAME,
                        board=board,
                        company=company,
                        title=str(position.get("name") or "").strip(),
                        location=location,
                        url=ref.url,
                        remote=_remote(position, location),
                        provider_id=ref.provider_id,
                        age_days=_age_days(position.get("postedTs")),
                    )
                total = data.get("count")
                if len(rows) < PAGE or (isinstance(total, int) and (page + 1) * PAGE >= total):
                    break
        return list(seen.values()) if answered else None


ADAPTER = _Eightfold()
