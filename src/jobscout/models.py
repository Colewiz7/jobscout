"""The one shape every source normalises down to."""
from __future__ import annotations

import dataclasses
import hashlib
import re

# Providers whose job id is stable enough to dedupe on directly. A posting that
# appears both in the Simplify README and on the company's own board is the
# same posting, and the provider id is the only thing both copies agree on.
_PROVIDER_ID = [
    # job-boards.greenhouse.io/acme/jobs/123 and boards.greenhouse.io/acme/jobs/123
    ("greenhouse", re.compile(r"(?:job-)?boards?\.greenhouse\.io/[^/]+/jobs/(\d+)")),
    # Embedded greenhouse: the company hosts the page, gh_jid carries the id.
    ("greenhouse", re.compile(r"[?&]gh_jid=(\d+)")),
    ("lever", re.compile(r"jobs\.lever\.co/[^/]+/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/[^/]+/([0-9a-f-]{36})")),
]

# Simplify's Age column: "0d", "9d", "1mo", "2y". Approximate is fine, it only
# ever decides sort order and what gets printed.
_AGE = re.compile(r"^(\d+)\s*(h|d|w|mo|y)$", re.I)
_AGE_DAYS = {"h": 0, "d": 1, "w": 7, "mo": 30, "y": 365}

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalise(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace, for hashing."""
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def parse_age(text: str) -> int | None:
    match = _AGE.match((text or "").strip())
    if not match:
        return None
    return int(match.group(1)) * _AGE_DAYS[match.group(2).lower()]


def age_label(days: int | None) -> str:
    if days is None:
        return "?"
    if days < 30:
        return f"{days}d"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def merge_locations(*values: str) -> str:
    """Union of every location mentioned, de-duplicated, stable order.

    A role posted in three cities collapses to one row on dedupe, and location
    is often what decides whether it is worth applying, so the row keeps all
    three rather than whichever one was inserted last.
    """
    seen: dict[str, None] = {}
    for value in values:
        for part in (value or "").split(";"):
            part = part.strip()
            if part:
                seen.setdefault(part, None)
    return "; ".join(seen)


def provider_id_from_url(url: str) -> str | None:
    """Return `provider:id` if the apply URL exposes one, else None."""
    for provider, pattern in _PROVIDER_ID:
        match = pattern.search(url or "")
        if match:
            return f"{provider}:{match.group(1)}"
    return None


@dataclasses.dataclass(frozen=True)
class Posting:
    source: str          # simplify-s27 | simplify-off | greenhouse | lever | ashby
    company: str
    title: str
    location: str
    url: str
    terms: str = ""      # "Spring 2027, Summer 2027" or "" when the source has no such column
    board: str | None = None   # board slug, for the missing-run close rule; None for Simplify
    remote: bool = False
    closed: bool = False
    provider_id: str | None = None   # set by board sources from the native id
    age_days: int | None = None      # how long the source says it has been posted

    @property
    def dedupe_key(self) -> str:
        """Provider job id when we have one, else a hash of the identity fields.

        Deliberately excludes the URL host: the same posting is reachable at a
        company careers page and at the provider's own domain, and hashing the
        host would file those as two different jobs.
        """
        return self.provider_id or provider_id_from_url(self.url) or self.fallback_key

    @property
    def fallback_key(self) -> str:
        """The hash form, computed even when a provider id exists.

        A locked Simplify row has no apply URL, so it cannot produce the
        provider id the same job was stored under while it was open. Keeping
        the hash on every row gives the close path something to match on.
        """
        raw = "|".join(
            (normalise(self.company), normalise(self.title), normalise(self.terms))
        )
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @property
    def source_id(self) -> str:
        """Unique within a source. Boards have a native id; Simplify does not."""
        return self.provider_id or self.dedupe_key
