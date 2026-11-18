"""IBM careers, read through the search index behind ibm.com/careers/search.

IBM's postings live on careers.ibm.com, which is IBM's Avature portal (it also
answers as ibmglobal.avature.net). Avature's job pages and per-locale sitemaps
sit behind an AWS WAF JavaScript challenge: a plain client gets 202 with an
empty body and `x-amzn-waf-action: challenge`, so they are never fetched here.

The search page on www.ibm.com does not go through Avature. It posts an
Elasticsearch-style query to www-api.ibm.com with a fixed app id and scope and
no session, and every hit carries the Avature job id. So the scout and a single
posting read both use that index. The catch is the description: the index only
hands back IBM's teaser, about 250 characters, and refuses `body` in
`_source`, so description_html here is that teaser and nothing more.

Liveness reads the same index. A job id lookup that answers with zero hits
means IBM's own search no longer lists it, which is the closest thing to a
"no longer available" page this site offers without the browser challenge.
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

NAME = "ibm"
COMPANY = "IBM"

SEARCH = "https://www-api.ibm.com/search/api/v2"
VIEW = "https://careers.ibm.com/careers/JobDetail?jobId={job_id}"

HOSTS = {"careers.ibm.com", "ibmglobal.avature.net"}
US = "United States"

PAGE = 50
MAX_PAGES = 3
PAUSE = 1.0

# What the search page itself sends. The API rejects unknown `_source` names
# with a 400, so this list stays to fields it was seen to accept.
_SOURCE = [
    "_id", "title", "url", "description", "language",
    "field_keyword_05",   # country, "United States"
    "field_keyword_08",   # business unit, often null
    "field_keyword_17",   # work arrangement: Hybrid, Remote, or empty for on-site
    "field_keyword_18",   # level: Professional, Entry Level, Internship
    "field_keyword_19",   # "Dallas, US", "Multiple Cities", "No City, US"
    "field_text_01",      # the Avature job id
    "dcdate",             # date the posting went up, YYYY-MM-DD
]
# Title fields only. IBM's boilerplate says "hybrid cloud" on nearly every
# posting, so matching the body turns "cloud" into the whole board, and the
# scout judges on the title anyway.
_QUERY_FIELDS = ["title^3", "h1s_content^2"]

# /careers/JobDetail?jobId=123, optionally under a locale (/en_US/careers/...),
# or Avature's path form /careers/JobDetail/Some-Title/123.
_PATH = re.compile(r"/(?:[a-z]{2}_[A-Z]{2}/)?careers/JobDetail(?:/[^/?#]+)*/?")
_ID = re.compile(r"\d{1,12}")


def _base(query: dict, size: int, offset: int = 0) -> dict:
    return {
        "appId": "careers",
        "scopes": ["careers2"],
        "query": query,
        "size": size,
        "from": offset,
        "sort": [{"_score": "desc"}, {"pageviews": "desc"}],
        "lang": "zz",
        "localeSelector": {},
        "_source": _SOURCE,
    }


def _lookup_body(job_id: str) -> dict:
    return _base({"bool": {"must": [{"term": {"field_text_01": int(job_id)}}]}}, size=2)


def _search_body(term: str, offset: int) -> dict:
    query = {
        "bool": {
            "must": [{
                "simple_query_string": {
                    "query": term,
                    "fields": _QUERY_FIELDS,
                    "default_operator": "and",
                },
            }],
            "filter": [{"term": {"field_keyword_05": US}}],
        }
    }
    return _base(query, size=PAGE, offset=offset)


def _hits(payload) -> tuple[list[dict], int | None]:
    """Hit sources and the total, or Unavailable when the shape is wrong."""
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, dict) or not isinstance(hits.get("hits"), list):
        raise Unavailable("ibm search answered without hits")
    total = hits.get("total")
    if isinstance(total, dict):
        total = total.get("value")
    sources = [h.get("_source") for h in hits["hits"] if isinstance(h, dict)]
    return [s for s in sources if isinstance(s, dict)], total if isinstance(total, int) else None


def _job_id(source: dict) -> str:
    value = source.get("field_text_01")
    return str(value) if value is not None and _ID.fullmatch(str(value)) else ""


def _city(raw: str) -> str:
    # The index shouts some cities ("POUGHKEEPSIE"). Mixed case is left alone.
    return raw.title() if raw.isupper() and len(raw) > 3 else raw


def _location(source: dict) -> str:
    place = str(source.get("field_keyword_19") or "").strip()
    country = str(source.get("field_keyword_05") or "").strip()
    head, comma, _code = place.rpartition(",")
    city = head.strip() if comma else place
    if not city or city.casefold() == "no city":
        return country
    if not country:
        return place
    # "Dallas, United States" rather than IBM's "Dallas, US": the location
    # filter knows the country spelled out and not the bare code.
    return f"{_city(city)}, {country}"


def _remote(source: dict) -> bool:
    return str(source.get("field_keyword_17") or "").strip().casefold() == "remote"


def _employment_type(source: dict) -> str | None:
    # IBM files its apprenticeships under Internship too. That is IBM's own
    # label, so it is passed through rather than second-guessed.
    level = str(source.get("field_keyword_18") or "").strip().casefold()
    return "INTERN" if level == "internship" else None


def _age_days(value) -> int | None:
    try:
        posted = datetime.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None
    return max((datetime.date.today() - posted).days, 0)


def _ref(job_id: str) -> Ref:
    return Ref(provider=NAME, board=NAME, job_id=job_id, url=VIEW.format(job_id=job_id))


class _IBM:
    name = NAME

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None:
        host = (parsed.hostname or "").rstrip(".").casefold()
        if host not in HOSTS or not _PATH.fullmatch(parsed.path or ""):
            return None
        job_id = ""
        for value in urllib.parse.parse_qs(parsed.query).get("jobId", []):
            if _ID.fullmatch(value):
                job_id = value
                break
        if not job_id:
            last = (parsed.path or "").rstrip("/").rsplit("/", 1)[-1]
            job_id = last if _ID.fullmatch(last) else ""
        return _ref(job_id) if job_id else None

    def detail(self, fetcher, ref: Ref) -> Detail:
        if not _ID.fullmatch(ref.job_id or ""):
            raise Unavailable(f"ibm job id {ref.job_id!r} is not numeric")
        try:
            payload = fetch_json(fetcher, SEARCH, method="POST", body=_lookup_body(ref.job_id))
        except Gone as error:
            # A 404 here is the search endpoint moving, not the posting closing.
            raise Unavailable(f"ibm search endpoint: {error}", 404) from None
        sources, _total = _hits(payload)
        if not sources:
            raise Gone(f"ibm job {ref.job_id} is no longer in IBM's careers search")
        found = [s for s in sources if _job_id(s) == ref.job_id]
        if not found:
            # The term query only ever matches its own id, so this is a
            # changed index rather than an answer about this posting.
            raise Unavailable(f"ibm lookup for {ref.job_id} returned other postings")
        source = found[0]
        title = str(source.get("title") or "").strip()
        if not title:
            raise Unavailable(f"ibm job {ref.job_id} came back without a title")
        teaser = str(source.get("description") or "").strip()
        return Detail(
            ref=_ref(ref.job_id),
            company=COMPANY,
            title=title,
            location=_location(source),
            description_html=f"<p>{html.escape(teaser)}</p>" if teaser else None,
            remote=_remote(source),
            employment_type=_employment_type(source),
            age_days=_age_days(source.get("dcdate")),
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
                try:
                    payload = fetch_json(
                        fetcher, SEARCH, method="POST", body=_search_body(term, page * PAGE)
                    )
                    sources, total = _hits(payload)
                except (Gone, Unavailable) as error:
                    # A partial result would let the close rule retire whatever
                    # the failed query would have returned.
                    log.warning("ibm: query %r failed (%s), treating the board as absent", term, error)
                    return None
                answered = True
                for source in sources:
                    job_id = _job_id(source)
                    title = str(source.get("title") or "").strip()
                    if not job_id or not title or job_id in seen:
                        continue
                    ref = _ref(job_id)
                    location = _location(source)
                    seen[job_id] = Posting(
                        source=NAME,
                        board=board,
                        company=company,
                        title=title,
                        location=location,
                        url=ref.url,
                        remote=_remote(source) or "remote" in location.casefold(),
                        provider_id=ref.provider_id,
                        age_days=_age_days(source.get("dcdate")),
                        employment_type=_employment_type(source),
                    )
                if len(sources) < PAGE or (total is not None and (page + 1) * PAGE >= total):
                    break
        return list(seen.values()) if answered else None


ADAPTER = _IBM()
