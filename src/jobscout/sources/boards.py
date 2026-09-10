"""Greenhouse, Lever and Ashby public job board APIs.

None of these need auth. A 404 means the slug is wrong or the board is gone,
which is normal for a derived slug list, so a bad slug logs and is skipped
rather than failing the run.
"""
from __future__ import annotations

import datetime
import logging

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

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _greenhouse(payload, slug: str) -> list[Posting]:
    out = []
    for job in (payload or {}).get("jobs", []):
        location = ((job.get("location") or {}).get("name") or "").strip()
        out.append(
            Posting(
                source="greenhouse",
                board=slug,
                company=slug,
                title=(job.get("title") or "").strip(),
                location=location,
                url=job.get("absolute_url") or "",
                remote="remote" in location.lower(),
                provider_id=f"greenhouse:{job.get('id')}",
                age_days=_age_days(job.get("updated_at")),
            )
        )
    return out


def _lever(payload, slug: str) -> list[Posting]:
    out = []
    for job in payload or []:
        categories = job.get("categories") or {}
        location = (categories.get("location") or "").strip()
        workplace = (job.get("workplaceType") or "").lower()
        out.append(
            Posting(
                source="lever",
                board=slug,
                company=slug,
                title=(job.get("text") or "").strip(),
                location=location,
                url=job.get("hostedUrl") or "",
                remote=workplace == "remote" or "remote" in location.lower(),
                provider_id=f"lever:{job.get('id')}",
                age_days=_age_days(job.get("createdAt")),
            )
        )
    return out


def _ashby(payload, slug: str) -> list[Posting]:
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
                company=slug,
                title=(job.get("title") or "").strip(),
                location="; ".join(p for p in places if p),
                url=job.get("jobUrl") or "",
                remote=bool(job.get("isRemote")),
                provider_id=f"ashby:{job.get('id')}",
                age_days=_age_days(job.get("publishedAt")),
            )
        )
    return out


PROVIDERS = {
    "greenhouse": (GREENHOUSE, _greenhouse),
    "lever": (LEVER, _lever),
    "ashby": (ASHBY, _ashby),
}


def fetch(fetcher, boards: dict[str, tuple[str, ...]]):
    """Returns (postings, fetched) where `fetched` is the (source, slug) pairs
    that actually answered. Only those are eligible for the missing-run close
    rule: a board that 404s this run must not close every job it ever had.
    """
    postings: list[Posting] = []
    fetched: set[tuple[str, str]] = set()
    for provider, slugs in boards.items():
        spec = PROVIDERS.get(provider)
        if spec is None:
            log.warning("unknown board provider %r, skipping", provider)
            continue
        template, normalise = spec
        for slug in slugs:
            payload = fetcher.get_json(template.format(slug=slug))
            if payload is None:
                log.warning("%s/%s did not answer, skipping", provider, slug)
                continue
            rows = normalise(payload, slug)
            postings.extend(rows)
            fetched.add((provider, slug))
            log.info("%s/%s: %d rows", provider, slug, len(rows))
    return postings, fetched
