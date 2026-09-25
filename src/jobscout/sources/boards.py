"""Greenhouse, Lever and Ashby public job board APIs.

None of these need auth. A 404 means the slug is wrong or the board is gone,
which is normal for a derived slug list, so a bad slug logs and is skipped
rather than failing the run.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.parse

from ..models import Posting

# Each board names its timestamp differently, and Lever uses epoch milliseconds.
def _age_days(value) -> int | None:
    if value in (None, ""):
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    if isinstance(value, (int, float)):
        stamp = datetime.datetime.fromtimestamp(value / 1000, datetime.timezone.utc)
    else:
        try:
            stamp = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return max((now - stamp).days, 0)

log = logging.getLogger(__name__)

# Problems worth a human's attention rather than a log line nobody opens.
# fetch() clears this, so it describes the run that just happened.
NOTICES: list[tuple[str, str]] = []

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _greenhouse(payload, slug: str, company: str) -> list[Posting]:
    out = []
    for job in (payload or {}).get("jobs", []):
        location = ((job.get("location") or {}).get("name") or "").strip()
        out.append(
            Posting(
                source="greenhouse",
                board=slug,
                company=company,
                title=(job.get("title") or "").strip(),
                location=location,
                url=job.get("absolute_url") or "",
                remote="remote" in location.lower(),
                provider_id=f"greenhouse:{job.get('id')}",
                age_days=_age_days(job.get("updated_at")),
            )
        )
    return out


def _lever(payload, slug: str, company: str) -> list[Posting]:
    out = []
    for job in payload or []:
        categories = job.get("categories") or {}
        location = (categories.get("location") or "").strip()
        workplace = (job.get("workplaceType") or "").lower()
        out.append(
            Posting(
                source="lever",
                board=slug,
                company=company,
                title=(job.get("text") or "").strip(),
                location=location,
                url=job.get("hostedUrl") or "",
                remote=workplace == "remote" or "remote" in location.lower(),
                provider_id=f"lever:{job.get('id')}",
                age_days=_age_days(job.get("createdAt")),
            )
        )
    return out


def _ashby(payload, slug: str, company: str) -> list[Posting]:
    out = []
    for job in (payload or {}).get("jobs", []):
        if not job.get("isListed", True):
            continue
        # Ashby is the only board with structured secondary locations, so we
        # keep them all and let the location filter pass on any one of them.
        places = [(job.get("location") or "").strip()]
        for extra in job.get("secondaryLocations") or []:
            place = (extra.get("location") or "").strip()
            if place:
                places.append(place)
        out.append(
            Posting(
                source="ashby",
                board=slug,
                company=company,
                title=(job.get("title") or "").strip(),
                location="; ".join(p for p in places if p),
                url=job.get("jobUrl") or "",
                remote=bool(job.get("isRemote")),
                provider_id=f"ashby:{job.get('id')}",
                age_days=_age_days(job.get("publishedAt")),
            )
        )
    return out


# Workday needs three values, not a slug: the tenant, the numbered datacenter
# and the site name, written "mtb/wd5/MTB". They are all visible in the careers
# URL a company links to.
WORKDAY_SPEC = re.compile(r"^([A-Za-z0-9\-]+)/(wd\d+)/([A-Za-z0-9_\-]+)$")
WORKDAY = "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
WORKDAY_VIEW = "https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}{path}"
WORKDAY_PAGE = 20          # the API's own page size; asking for more is ignored
WORKDAY_MAX_PAGES = 3      # 60 hits per term is far past anything relevant

# Workday dates are prose: "Posted Today", "Posted Yesterday", "Posted 30+ Days
# Ago". There is no timestamp anywhere in the list response.
_WD_AGE = re.compile(r"(\d+)\+?\s*days?\s*ago", re.I)


def _workday_age(text: str) -> int | None:
    lowered = (text or "").lower()
    if "just posted" in lowered or "today" in lowered:
        return 0
    if "yesterday" in lowered:
        return 1
    found = _WD_AGE.search(lowered)
    return int(found.group(1)) if found else None


def _tenant_matches(company: str, tenant: str) -> bool:
    """Is this Workday tenant plausibly this employer's.

    Looser than the slug rule, because tenants are abbreviations as often as
    names: Booz Allen Hamilton is bah, M&T Bank is mtb, and JPL is citjpl
    because Caltech runs the lab. Any of a shared word, a prefix, or the
    initials is enough. What it still catches is a tenant with no relationship
    at all, which is how Discover's page offering Capital One's board reads.
    """
    tenant = re.sub(r"[^a-z0-9]", "", tenant.lower())
    base = company.split("(")[0]
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", base.lower()).split() if len(w) > 2]
    full = "".join(re.sub(r"[^a-z0-9]", "", base.lower()))
    if not tenant or not full:
        return True
    if tenant in full or full in tenant:
        return True
    if any(w in tenant for w in words):
        return True
    initials = "".join(w[0] for w in re.sub(r"[^a-z0-9 ]", " ", base.lower()).split() if w)
    if initials and (tenant == initials or tenant.startswith(initials)):
        return True
    # Last resort: an airline-style contraction such as swa for Southwest
    # Airlines. Weak on its own, but a tenant that is not even spelled out of
    # the employer's letters in order is not theirs.
    letters = iter(full)
    return all(character in letters for character in tenant)



def _workday_page(fetcher, url: str, term: str, offset: int):
    body = json.dumps(
        {"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": offset, "searchText": term}
    ).encode()
    response = fetcher.post(
        url, body, {"Content-Type": "application/json", "Accept": "application/json"}
    )
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        log.warning("non-JSON body from %s", url)
        return None


def _workday(fetcher, slug: str, company: str, terms=()):
    """One query per search term rather than one sweep of the whole board.

    Workday's searchText is a substring match, so "intern" also returns every
    "Internal Audit" posting: at M&T it matches 755 of 804 jobs and filters
    nothing. The infrastructure words are what actually narrow it, so the fan
    out is over those and the co-op rule is applied locally afterwards.
    """
    spec = WORKDAY_SPEC.match(slug)
    if spec is None:
        # None, not []: an empty list reads as "the board answered with nothing"
        # and would make every job it ever had eligible for the close rule.
        log.warning("workday board %r is not tenant/wdN/site, skipping", slug)
        return None
    tenant, dc, site = spec.groups()
    # A careers page can advertise somebody else's board: Discover's carries
    # Capital One's, which answers with 1800 postings and would file them all
    # under the wrong employer. The tenant is only a hint though, since JPL
    # legitimately posts under citjpl because Caltech runs the lab, so this
    # says so rather than dropping the board.
    if company and company != slug and not _tenant_matches(company, tenant):
        log.warning(
            "workday tenant %r does not look like %r; check the board is theirs",
            tenant, company,
        )
        NOTICES.append((
            f"workday-tenant:{slug}",
            f"{company}: board {slug} belongs to tenant {tenant!r}, "
            f"which does not look like them. Check it is the right board.",
        ))
    url = WORKDAY.format(tenant=tenant, dc=dc, site=site)

    seen: dict[str, Posting] = {}
    answered = False
    for term in terms or ():
        for page in range(WORKDAY_MAX_PAGES):
            payload = _workday_page(fetcher, url, term, page * WORKDAY_PAGE)
            if payload is None:
                # One board is many requests, so a single failure leaves the
                # result partial. Reporting it as answered would let the close
                # rule retire everything the failed query would have returned.
                log.warning("%s: query %r failed, treating the board as absent", slug, term)
                return None
            answered = True
            rows = payload.get("jobPostings") or []
            for job in rows:
                path = job.get("externalPath") or ""
                if not path or path in seen:
                    continue
                location = (job.get("locationsText") or "").strip()
                seen[path] = Posting(
                    source="workday",
                    board=slug,
                    company=company,
                    title=(job.get("title") or "").strip(),
                    location=location,
                    url=WORKDAY_VIEW.format(tenant=tenant, dc=dc, site=site, path=path),
                    remote="remote" in location.lower(),
                    provider_id=f"workday:{tenant}:{path}",
                    age_days=_workday_age(job.get("postedOn")),
                )
            if len(rows) < WORKDAY_PAGE:
                break
    return list(seen.values()) if answered else None


def _simple(template: str, normalise):
    """A provider that is one GET of one URL."""

    def run(fetcher, slug: str, company: str, terms=()):
        payload = fetcher.get_json(template.format(slug=slug))
        return None if payload is None else normalise(payload, slug, company)

    return run


# amazon.jobs has no documented API. This is the endpoint its own search page
# calls, so it is used sparingly: one request per configured query, a real
# User-Agent, and no paging. A board here is the query itself rather than a
# slug, which is what keeps the request count equal to the number of queries.
AMAZON = "https://www.amazon.jobs/en/search.json"
AMAZON_VIEW = "https://www.amazon.jobs{path}"
AMAZON_LIMIT = 100
_AMAZON_DATE = "%B %d, %Y"


def _amazon_age(text: str) -> int | None:
    if not text:
        return None
    try:
        stamp = datetime.datetime.strptime(text.strip(), _AMAZON_DATE)
    except ValueError:
        return None
    stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return max((datetime.datetime.now(datetime.timezone.utc) - stamp).days, 0)


def _amazon(fetcher, slug: str, company: str, terms=()):
    query = urllib.parse.urlencode(
        {
            "base_query": slug,
            "result_limit": AMAZON_LIMIT,
            "sort": "recent",
            "country": "USA",
        }
    )
    payload = fetcher.get_json(f"{AMAZON}?{query}")
    if payload is None:
        return None
    out = []
    for job in payload.get("jobs") or []:
        location = (job.get("normalized_location") or job.get("location") or "").strip()
        path = job.get("job_path") or ""
        job_id = job.get("id_icims") or job.get("id")
        if not path or job_id is None:
            continue
        out.append(
            Posting(
                source="amazon",
                board=slug,
                company=company,
                title=(job.get("title") or "").strip(),
                location=location,
                url=AMAZON_VIEW.format(path=path),
                remote="virtual" in location.lower() or "remote" in location.lower(),
                provider_id=f"amazon:{job_id}",
                age_days=_amazon_age(job.get("posted_date")),
            )
        )
    return out



# Phenom career sites render their results from a template, but every one of
# them publishes a sitemap listing each job, and each job page carries
# schema.org JobPosting. So the whole source is readable without a browser:
# one sitemap, then a detail fetch only for the slugs that could plausibly
# qualify. A board here is the careers host.
PHENOM_SITEMAP = "https://{host}/sitemap.xml"
PHENOM_DETAIL_PAUSE = 0.4          # these are somebody's careers site
PHENOM_MAX_DETAILS = 40            # a ceiling on one board's share of a run
_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
_JOB_SLUG = re.compile(r"/job/[^/]+/([^/?#]+)")
# Deliberately looser than the real filter. A slug is truncated and stripped of
# punctuation, so this only throws away what carries no role word at all; the
# genuine test runs against the title from the job page.
_SLUG_HINT = re.compile(
    r"devops|devsecops|sre|reliab|platform|infra|cloud|network|system|sysadmin|"
    r"data-?cent|kubernetes|linux|technolog|\bit\b|it-|-it-|engineer|operations|"
    r"intern|co-?op|technician|administrat",
    re.I,
)


def _phenom_jsonld(html: str) -> dict | None:
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html or "", re.S
    ):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for candidate in data if isinstance(data, list) else [data]:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return None


def _phenom_location(posting: dict) -> str:
    places = posting.get("jobLocation")
    if isinstance(places, dict):
        places = [places]
    parts = []
    for place in places or []:
        address = (place or {}).get("address") or {}
        cell = ", ".join(
            str(address[key])
            for key in ("addressLocality", "addressRegion", "addressCountry")
            if address.get(key)
        )
        if cell:
            parts.append(cell)
    return "; ".join(dict.fromkeys(parts))


def _phenom_age(value: str) -> int | None:
    return _age_days(value)


def _phenom(fetcher, slug: str, company: str, terms=()):
    sitemap = fetcher.get_text(PHENOM_SITEMAP.format(host=slug))
    if sitemap is None:
        # Same rule as a malformed Workday spec: a board that did not answer
        # must not look like a board that answered with nothing.
        log.warning("phenom %s: sitemap did not answer, treating as absent", slug)
        return None

    urls = [u for u in _LOC.findall(sitemap) if "/job/" in u]
    candidates = []
    for url in urls:
        found = _JOB_SLUG.search(url)
        if found and _SLUG_HINT.search(found.group(1)):
            candidates.append(url)
    log.info("phenom %s: %d jobs listed, %d worth opening", slug, len(urls), len(candidates))

    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for url in candidates[:PHENOM_MAX_DETAILS]:
        time.sleep(PHENOM_DETAIL_PAUSE)
        html = fetcher.get_text(url)
        if html is None:
            continue
        posting = _phenom_jsonld(html)
        if posting is None:
            continue
        closes = posting.get("validThrough")
        if closes:
            try:
                deadline = datetime.datetime.fromisoformat(str(closes).replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=datetime.timezone.utc)
                if deadline < now:
                    continue
            except ValueError:
                pass
        employment = posting.get("employmentType")
        if isinstance(employment, list):
            employment = employment[0] if employment else None
        location = _phenom_location(posting)
        out.append(
            Posting(
                source="phenom",
                board=slug,
                company=company,
                title=(posting.get("title") or "").strip(),
                location=location,
                url=url,
                remote="remote" in location.lower(),
                provider_id=f"phenom:{slug}:{url.rsplit('/', 2)[-2]}",
                age_days=_phenom_age(posting.get("datePosted")),
                employment_type=employment,
            )
        )
    return out



WORKDAY_DETAIL = "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"


def workday_resolve_location(fetcher, posting) -> str | None:
    """The real locations behind a collapsed "3 Locations" cell.

    The list view names none of them, so a posting in Taiwan and a posting in
    Texas are the same string. The detail record carries the primary location,
    any additional ones, and the country, which is what the deny list needs to
    do its job. Returns None when the detail cannot be read, so the caller can
    decide what to do with an unresolved posting rather than being told a
    wrong answer.
    """
    spec = WORKDAY_SPEC.match(posting.board or "")
    if spec is None:
        return None
    tenant, dc, site = spec.groups()
    path = (posting.provider_id or "").split(":", 2)[-1]
    if not path.startswith("/"):
        return None
    payload = fetcher.get_json(
        WORKDAY_DETAIL.format(tenant=tenant, dc=dc, site=site, path=path)
    )
    if not isinstance(payload, dict):
        return None
    info = payload.get("jobPostingInfo") or {}
    places = [info.get("location")]
    places.extend(info.get("additionalLocations") or [])
    country = info.get("country")
    if isinstance(country, dict):
        country = country.get("descriptor")
    cells = [str(p).strip() for p in places if p]
    if country and cells:
        # The country is stated once and applies to the primary location, so
        # it rides with it: "Miaoli" alone is not something a deny list can
        # read, while "Miaoli, Taiwan" is.
        cells[0] = f"{cells[0]}, {country}"
    return "; ".join(dict.fromkeys(cells)) or None



# Discovery probes a slug by asking for the board, which only means anything
# for the providers addressed by a single GET. Workday is deliberately absent:
# its three-part spec cannot be derived from an apply URL.
BOARD_TEMPLATES = {
    "greenhouse": GREENHOUSE,
    "lever": LEVER,
    "ashby": ASHBY,
}

PROVIDERS = {
    "greenhouse": _simple(GREENHOUSE, _greenhouse),
    "lever": _simple(LEVER, _lever),
    "ashby": _simple(ASHBY, _ashby),
    "workday": _workday,
    "amazon": _amazon,
    "phenom": _phenom,
}


def fetch(fetcher, boards: dict[str, tuple[str, ...]], names=None, search_terms=()):
    """Returns (postings, fetched) where `fetched` is the (source, slug) pairs
    that actually answered. Only those are eligible for the missing-run close
    rule: a board that 404s this run must not close every job it ever had.

    `names` maps a slug to a display company name. None of these APIs states the
    company anywhere in the payload, so without the map a notification reads
    "morsecorpcoop" instead of "MORSE Corp".
    """
    names = names or {}
    NOTICES.clear()
    postings: list[Posting] = []
    fetched: set[tuple[str, str]] = set()
    for provider, slugs in boards.items():
        run = PROVIDERS.get(provider)
        if run is None:
            log.warning("unknown board provider %r, skipping", provider)
            continue
        for slug in slugs:
            company = (names.get(provider) or {}).get(slug, slug)
            rows = run(fetcher, slug, company, search_terms)
            if rows is None:
                log.warning("%s/%s did not answer, skipping", provider, slug)
                continue
            postings.extend(rows)
            fetched.add((provider, slug))
            log.info("%s/%s: %d rows", provider, slug, len(rows))
    return postings, fetched
