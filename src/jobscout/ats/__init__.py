"""Adapters for applicant tracking systems past the original four.

Greenhouse, Lever, Ashby and Workday grew up inline in capture, descriptions,
liveness and the board scanner. Everything newer is one module here, and those
four places ask this registry instead of growing another branch each.

An adapter knows three things about one ATS:

- `match(parsed_url)` names the posting a URL points at, without any network.
  It only ever recognises hosts it owns (a fixed suffix like `.icims.com`, or
  an explicit map of company domains), so capture still never fetches an
  arbitrary URL someone pasted.
- `detail(fetcher, ref)` reads one posting through the provider's public API.
  It raises `Gone` when the provider says the posting no longer exists and
  `Unavailable` when it could not tell, so liveness never calls an ambiguous
  answer closed.
- `search(fetcher, board, company, terms)` is optional, for the scout. It
  returns postings, or None when the board did not answer, because an empty
  list means "nothing open" and lets the close rule retire jobs.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import urllib.parse
from typing import Callable, Protocol

from ..models import Posting

log = logging.getLogger(__name__)

# Registered adapter modules, each exposing ADAPTER. Order matters only when
# two could claim the same URL, which a fixed host suffix should prevent.
MODULES: tuple[str, ...] = (
    "icims",
    "eightfold",
    "oracle",
    "successfactors",
    "smartrecruiters",
    "workable",
    "ibm",
    "tiktok",
    "greenhouse_domains",
    "apple",
    "google",
    # Meta is built (ats/meta.py) but left out: metacareers.com's robots.txt
    # says automated collection needs Meta's written permission.
)


class Gone(Exception):
    """The provider answered and the posting is not there (404, 410, closed)."""


class Unavailable(Exception):
    """The provider could not be read: blocked, broken, or an unexpected shape."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class Ref:
    provider: str          # adapter name, also Posting.source
    board: str             # the company's identifier on that ATS
    job_id: str            # the native posting id
    url: str               # a public URL a person can open
    provider_id: str = ""  # dedupe key; defaults to provider:board:job_id

    def __post_init__(self):
        if not self.provider_id:
            object.__setattr__(self, "provider_id", f"{self.provider}:{self.board}:{self.job_id}")


@dataclasses.dataclass(frozen=True)
class Detail:
    ref: Ref
    company: str
    title: str
    location: str = ""
    description_html: str | None = None
    remote: bool = False
    employment_type: str | None = None
    # The provider's own closing date, ISO 8601, when it states one.
    deadline: str | None = None
    age_days: int | None = None

    def posting(self) -> Posting:
        return Posting(
            source=self.ref.provider,
            board=self.ref.board,
            company=self.company,
            title=self.title,
            location=self.location,
            url=self.ref.url,
            remote=self.remote or "remote" in self.location.casefold(),
            provider_id=self.ref.provider_id,
            age_days=self.age_days,
            employment_type=self.employment_type,
        )


class Adapter(Protocol):
    name: str

    def match(self, parsed: urllib.parse.SplitResult) -> Ref | None: ...

    def detail(self, fetcher, ref: Ref) -> Detail: ...

    # Optional. Absent means the scout cannot scan this ATS by company.
    search: Callable | None


def _status_check(response, url: str) -> None:
    if response is None:
        raise Unavailable(f"no response from {url}")
    if response.status_code in (404, 410):
        raise Gone(f"{url} returned {response.status_code}")
    if not response.is_success:
        raise Unavailable(f"{url} returned {response.status_code}", response.status_code)


def fetch_text(fetcher, url: str, *, method: str = "GET", body=None, headers: dict | None = None) -> str:
    """Body of a successful response. Gone on 404/410, Unavailable otherwise."""
    kwargs = {}
    if headers:
        kwargs["headers"] = headers
    if body is not None:
        kwargs["content"] = body if isinstance(body, bytes) else json.dumps(body).encode()
    response = fetcher.send(method, url, **kwargs)
    _status_check(response, url)
    return response.text


def fetch_json(fetcher, url: str, *, method: str = "GET", body=None, headers: dict | None = None):
    headers = {"Accept": "application/json", **(headers or {})}
    if body is not None and not isinstance(body, bytes):
        headers.setdefault("Content-Type", "application/json")
    text = fetch_text(fetcher, url, method=method, body=body, headers=headers)
    try:
        return json.loads(text)
    except ValueError:
        raise Unavailable(f"non-JSON body from {url}") from None


_adapters: list | None = None


def adapters() -> list:
    global _adapters
    if _adapters is None:
        _adapters = [importlib.import_module(f"{__name__}.{name}").ADAPTER for name in MODULES]
    return _adapters


def match(url: str) -> tuple[object, Ref] | None:
    """The adapter that owns this posting URL, and the posting it names."""
    try:
        parsed = urllib.parse.urlsplit(url or "")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    for adapter in adapters():
        ref = adapter.match(parsed)
        if ref is not None:
            return adapter, ref
    return None


def board_providers() -> dict[str, Callable]:
    """name -> search, in the shape the board scanner's PROVIDERS table uses."""
    return {a.name: a.search for a in adapters() if getattr(a, "search", None)}
