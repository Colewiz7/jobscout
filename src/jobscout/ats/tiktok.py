"""TikTok and ByteDance careers, two portals on one ATS API.

TikTok (lifeattiktok.com, and the older careers.tiktok.com) and ByteDance
(joinbytedance.com) are Next.js front ends over the same public API. Both calls
are plain JSON POSTs with one constant header, `website-path`, which picks the
portal: "tiktok" for TikTok, and for ByteDance the language path "en" (there is
no "bytedance" portal, and the API rejects that value). Nothing is scraped from
a page first: no cookie, CSRF token or signature.

- search: POST <api>/search/job/posts with a keyword, limit and offset. Its
  location filter takes city codes only (a country code returns nothing), so
  the US cut is made here from each job's city chain.
- detail: POST <api>/job/posts/<id> with {"job_post_id"}. A posting that no
  longer exists comes back 200 with `job_post_detail: null`, which is the Gone
  signal. Because the API has that explicit answer, a 404 from it means the
  route moved, not that the posting closed, so it is read as Unavailable.

Neither call returns a posted date, so age_days stays empty.

Each portal is a Site, and the board names it: "tiktok" or "bytedance". TikTok
is read from api.lifeattiktok.com. joinbytedance.com's own bundle calls
jobs.bytedance.com/api/v1/public/supplier with `website-path: en` and
`accept-language: en-US`, so ByteDance is read there with those two. The page
also sends `x-tt-env` and `origin`, which change nothing in the answer.

Posting ids come from the one ATS behind both portals, and a few postings are
listed on both. Each portal still files them under its own board, so such a
posting shows once for TikTok and once for ByteDance.

careers.tiktok.com/position/<id>/detail redirects to lifeattiktok.com/search/<id>
with the same id, so both are matched. jobs.bytedance.com/en/position/<id>/detail
redirects to joinbytedance.com/search/<id> the same way, and
jobs.bytedance.com/en/resume/<id>/apply is joinbytedance.com's apply link. The
rest of jobs.bytedance.com (/experienced, /campus) is ByteDance's China site on
a different API, so only the /en/ paths are matched there.
"""
from __future__ import annotations

import dataclasses
import html
import logging
import re
import time
import urllib.parse

from ..models import Posting
from . import Detail, Gone, Ref, Unavailable, fetch_json

log = logging.getLogger(__name__)

NAME = "tiktok"
COMPANY = "TikTok"

API = "https://api.lifeattiktok.com/api/v1/public/supplier"
SEARCH = API + "/search/job/posts"
DETAIL = API + "/job/posts/{job_id}"
VIEW = "https://lifeattiktok.com/search/{job_id}"
HEADERS = {"website-path": "tiktok"}

HOSTS = {"lifeattiktok.com", "www.lifeattiktok.com", "careers.tiktok.com"}
US_NAMES = {"united states of america", "united states"}
US_CODE = "CN_6"

PAGE = 50
MAX_PAGES = 4
PAUSE = 1.0

# /search/<id>, /position/<id>, /position/<id>/detail, and the apply link
# /resume/<id>/apply, optionally under a locale segment.
_PATH = re.compile(
    r"(?:/[a-z]{2}(?:-[A-Za-z]{2})?)?/(?:search|position|resume)/(\d{10,20})(?:/(?:detail|apply))?/?"
)
# joinbytedance.com/search/<id>, the only posting path that site has.
_BYTEDANCE_PATH = re.compile(r"/search/(\d{10,20})/?")
# The /en/ (global) half of jobs.bytedance.com: the old posting page, which
# redirects to joinbytedance.com, and the apply page.
_BYTEDANCE_LEGACY_PATH = re.compile(r"/en/(?:position/(\d{10,20})(?:/detail)?|resume/(\d{10,20})/apply)/?")
_ID = re.compile(r"\d{10,20}")


@dataclasses.dataclass(frozen=True)
class Site:
    board: str                       # board identifier, and the middle of provider_id
    company: str                     # display name
    api: str                         # base of both API calls
    headers: dict[str, str]          # constant headers that pick the portal
    view: str                        # public posting page, by {job_id}
    paths: dict[str, re.Pattern]     # exact host -> posting path pattern

    @property
    def search_url(self) -> str:
        return self.api + "/search/job/posts"

    def detail_url(self, job_id: str) -> str:
        return f"{self.api}/job/posts/{job_id}"


TIKTOK = Site(
    board="tiktok",
    company=COMPANY,
    api=API,
    headers=HEADERS,
    view=VIEW,
    paths={host: _PATH for host in sorted(HOSTS)},
)
BYTEDANCE = Site(
    board="bytedance",
    company="ByteDance",
    api="https://jobs.bytedance.com/api/v1/public/supplier",
    headers={"website-path": "en", "accept-language": "en-US"},
    view="https://joinbytedance.com/search/{job_id}",
    paths={"joinbytedance.com": _BYTEDANCE_PATH, "jobs.bytedance.com": _BYTEDANCE_LEGACY_PATH},
)
SITES: dict[str, Site] = {site.board: site for site in (TIKTOK, BYTEDANCE)}
_SITE_FOR_HOST: dict[str, Site] = {host: site for site in SITES.values() for host in site.paths}


def _site(board: str) -> Site:
    """The portal a board names: a site key or one of its hosts. Anything else
    is TikTok, which is all this adapter read before ByteDance was added."""
    board = (board or "").strip().casefold()
    return SITES.get(board) or _SITE_FOR_HOST.get(board) or TIKTOK
_BULLET = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")
_HEADING = re.compile(r"^[^.!?]{2,60}[:：]$")
_BRACKETED = re.compile(r"^【[^】]*】\s*")


def _text_html(text: str) -> str:
    """Plain text with "- " bullets and "Heading:" lines, as simple HTML."""
    out: list[str] = []
    in_list = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        bullet = _BULLET.match(line)
        if bullet and line[bullet.end():].strip():
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(line[bullet.end():].strip())}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line:
            continue
        tag = "h3" if _HEADING.match(line) else "p"
        out.append(f"<{tag}>{html.escape(line)}</{tag}>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _description(job: dict) -> str | None:
    parts = [_text_html(str(job.get("description") or "")), _text_html(str(job.get("requirement") or ""))]
    info = job.get("job_post_info") or {}
    for item in (info.get("job_post_object_value_list") if isinstance(info, dict) else None) or []:
        if not isinstance(item, dict) or not item.get("field_value"):
            continue
        name = (item.get("field_name") or {}).get("en_name") or ""
        name = _BRACKETED.sub("", str(name)).strip()
        if name:
            parts.append(f"<h3>{html.escape(name)}</h3>")
        # Already HTML from TikTok's editor.
        parts.append(str(item["field_value"]))
    body = "".join(parts)
    return body or None


def _chain(city: dict) -> list[dict]:
    nodes = []
    while isinstance(city, dict) and len(nodes) < 6:
        nodes.append(city)
        city = city.get("parent")
    return nodes


def _cities(job: dict) -> list[dict]:
    cities = job.get("city_list")
    if isinstance(cities, list) and cities:
        return [c for c in cities if isinstance(c, dict)]
    city = job.get("city_info")
    return [city] if isinstance(city, dict) else []


def _place(city: dict) -> str:
    names = [str(n.get("en_name") or n.get("i18n_name") or "").strip() for n in _chain(city)]
    return ", ".join(name for name in names if name)


def _location(job: dict) -> str:
    return "; ".join(dict.fromkeys(p for p in (_place(c) for c in _cities(job)) if p))


def _in_us(job: dict) -> bool:
    for city in _cities(job):
        for node in _chain(city):
            if node.get("code") == US_CODE:
                return True
            if str(node.get("en_name") or "").strip().casefold() in US_NAMES:
                return True
    return False


def _employment_type(job: dict) -> str | None:
    kind = job.get("recruit_type") or {}
    label = str(kind.get("en_name") or "").strip().casefold() if isinstance(kind, dict) else ""
    return "INTERN" if label == "intern" else None


def _data(payload, site: Site = TIKTOK) -> dict:
    label = f"{site.board} api"
    if not isinstance(payload, dict):
        raise Unavailable(f"{label} answered with something other than an object")
    if payload.get("code") != 0:
        raise Unavailable(f"{label} answered code {payload.get('code')}: {payload.get('message')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise Unavailable(f"{label} answered without data")
    return data


def _post(fetcher, url: str, body: dict, site: Site = TIKTOK):
    try:
        return fetch_json(fetcher, url, method="POST", body=body, headers=dict(site.headers))
    except Gone as error:
        # The API says "not found" with a null record, so a bare 404 or 410
        # is the route itself, and must never close a posting.
        raise Unavailable(f"{site.board} api route: {error}", 404) from None


def _ref(job_id: str, site: Site = TIKTOK) -> Ref:
    return Ref(provider=NAME, board=site.board, job_id=job_id, url=site.view.format(job_id=job_id))


class _TikTok:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = (parsed.hostname or "").rstrip(".").casefold()
        site = _SITE_FOR_HOST.get(host)
        if site is None:
            return None
        found = site.paths[host].fullmatch(parsed.path or "")
        if not found:
            return None
        job_id = next(group for group in found.groups() if group)
        return _ref(job_id, site)

    def detail(self, fetcher, ref: Ref) -> Detail:
        site = _site(ref.board)
        if not _ID.fullmatch(ref.job_id or ""):
            raise Unavailable(f"{site.board} job id {ref.job_id!r} is not numeric")
        payload = _post(fetcher, site.detail_url(ref.job_id), {"job_post_id": ref.job_id}, site)
        data = _data(payload, site)
        if "job_post_detail" not in data:
            raise Unavailable(f"{site.board} detail answered without job_post_detail")
        job = data["job_post_detail"]
        if job is None:
            raise Gone(f"{site.board} job {ref.job_id} no longer exists")
        if not isinstance(job, dict) or str(job.get("id") or "") != ref.job_id:
            raise Unavailable(f"{site.board} detail for {ref.job_id} named a different posting")
        title = str(job.get("title") or "").strip()
        if not title:
            raise Unavailable(f"{site.board} job {ref.job_id} came back without a title")
        location = _location(job)
        return Detail(
            ref=_ref(ref.job_id, site),
            company=site.company,
            title=title,
            location=location,
            description_html=_description(job),
            remote="remote" in location.casefold(),
            employment_type=_employment_type(job),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        site = _site(board)
        company = company if company and company != board else site.company
        seen: dict[str, Posting] = {}
        answered = False
        first = True
        for term in terms or ():
            for page in range(MAX_PAGES):
                if not first:
                    time.sleep(PAUSE)
                first = False
                body = {
                    "recruitment_id_list": [],
                    "job_category_id_list": [],
                    "subject_id_list": [],
                    "location_code_list": [],
                    "keyword": term,
                    "limit": PAGE,
                    "offset": page * PAGE,
                }
                try:
                    data = _data(_post(fetcher, site.search_url, body, site), site)
                    rows = data.get("job_post_list") or []
                    if not isinstance(rows, list):
                        raise Unavailable(f"{site.board} search answered without a job list")
                except (Gone, Unavailable) as error:
                    log.warning("%s/%s: query %r failed (%s), treating the board as absent", NAME, site.board, term, error)
                    return None
                answered = True
                for job in rows:
                    if not isinstance(job, dict):
                        continue
                    job_id = str(job.get("id") or "")
                    title = str(job.get("title") or "").strip()
                    if not _ID.fullmatch(job_id) or not title or job_id in seen or not _in_us(job):
                        continue
                    ref = _ref(job_id, site)
                    location = _location(job)
                    seen[job_id] = Posting(
                        source=NAME,
                        board=board,
                        company=company,
                        title=title,
                        location=location,
                        url=ref.url,
                        remote="remote" in location.casefold(),
                        provider_id=ref.provider_id,
                        employment_type=_employment_type(job),
                    )
                total = data.get("count")
                if len(rows) < PAGE or (isinstance(total, int) and (page + 1) * PAGE >= total):
                    break
        return list(seen.values()) if answered else None


ADAPTER = _TikTok()
