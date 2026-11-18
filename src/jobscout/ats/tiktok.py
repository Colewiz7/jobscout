"""TikTok careers (lifeattiktok.com, and the older careers.tiktok.com).

The site is a Next.js front end over a public API at api.lifeattiktok.com. Both
calls it needs are plain JSON POSTs with one constant header, `website-path:
tiktok`, which picks the portal. Nothing is scraped from a page first: no
cookie, CSRF token or signature.

- search: POST /api/v1/public/supplier/search/job/posts with a keyword, limit
  and offset. Its location filter takes city codes only (a country code
  returns nothing), so the US cut is made here from each job's city chain.
- detail: POST /api/v1/public/supplier/job/posts/<id> with {"job_post_id"}.
  A posting that no longer exists comes back 200 with `job_post_detail: null`,
  which is the Gone signal. Because the API has that explicit answer, a 404
  from it means the route moved, not that the posting closed, so it is read
  as Unavailable.

Neither call returns a posted date, so age_days stays empty.

careers.tiktok.com/position/<id>/detail redirects to lifeattiktok.com/search/<id>
with the same id, so both are matched. joinbytedance.com is a sibling site on
a different portal that this API does not serve (website-path "bytedance" is
rejected), so it is not matched.
"""
from __future__ import annotations

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
_ID = re.compile(r"\d{10,20}")
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


def _data(payload) -> dict:
    if not isinstance(payload, dict):
        raise Unavailable("tiktok api answered with something other than an object")
    if payload.get("code") != 0:
        raise Unavailable(f"tiktok api answered code {payload.get('code')}: {payload.get('message')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise Unavailable("tiktok api answered without data")
    return data


def _post(fetcher, url: str, body: dict):
    try:
        return fetch_json(fetcher, url, method="POST", body=body, headers=HEADERS)
    except Gone as error:
        # The API says "not found" with a null record, so a bare 404 or 410
        # is the route itself, and must never close a posting.
        raise Unavailable(f"tiktok api route: {error}", 404) from None


def _ref(job_id: str) -> Ref:
    return Ref(provider=NAME, board=NAME, job_id=job_id, url=VIEW.format(job_id=job_id))


class _TikTok:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = (parsed.hostname or "").rstrip(".").casefold()
        if host not in HOSTS:
            return None
        found = _PATH.fullmatch(parsed.path or "")
        return _ref(found.group(1)) if found else None

    def detail(self, fetcher, ref: Ref) -> Detail:
        if not _ID.fullmatch(ref.job_id or ""):
            raise Unavailable(f"tiktok job id {ref.job_id!r} is not numeric")
        data = _data(_post(fetcher, DETAIL.format(job_id=ref.job_id), {"job_post_id": ref.job_id}))
        if "job_post_detail" not in data:
            raise Unavailable("tiktok detail answered without job_post_detail")
        job = data["job_post_detail"]
        if job is None:
            raise Gone(f"tiktok job {ref.job_id} no longer exists")
        if not isinstance(job, dict) or str(job.get("id") or "") != ref.job_id:
            raise Unavailable(f"tiktok detail for {ref.job_id} named a different posting")
        title = str(job.get("title") or "").strip()
        if not title:
            raise Unavailable(f"tiktok job {ref.job_id} came back without a title")
        location = _location(job)
        return Detail(
            ref=_ref(ref.job_id),
            company=COMPANY,
            title=title,
            location=location,
            description_html=_description(job),
            remote="remote" in location.casefold(),
            employment_type=_employment_type(job),
        )

    def search(self, fetcher, board: str, company: str, terms=()) -> list[Posting] | None:
        company = company if company and company != board else COMPANY
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
                    data = _data(_post(fetcher, SEARCH, body))
                    rows = data.get("job_post_list") or []
                    if not isinstance(rows, list):
                        raise Unavailable("tiktok search answered without a job list")
                except (Gone, Unavailable) as error:
                    log.warning("tiktok: query %r failed (%s), treating the board as absent", term, error)
                    return None
                answered = True
                for job in rows:
                    if not isinstance(job, dict):
                        continue
                    job_id = str(job.get("id") or "")
                    title = str(job.get("title") or "").strip()
                    if not _ID.fullmatch(job_id) or not title or job_id in seen or not _in_us(job):
                        continue
                    ref = _ref(job_id)
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
