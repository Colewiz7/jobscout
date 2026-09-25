"""Filter config, loaded from YAML so tuning is a ConfigMap edit not a rebuild."""
from __future__ import annotations

import dataclasses
import os
import pathlib
import re

import yaml


def boundary_pattern(terms) -> re.Pattern | None:
    """Alternation that will not match inside a longer word.

    Substring matching quietly breaks real places: "india" is inside
    "Indianapolis", so a plain `in` test would deny every Indiana posting.
    """
    terms = [t for t in terms if t]
    if not terms:
        return None
    body = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])", re.I)

# Workday's searchText is a substring match, so searching "intern" also returns
# every "Internal Audit" posting and narrows nothing. These are the words that
# actually cut the board down; the co-op rule is applied locally afterwards.
WORKDAY_TERMS = (
    "devops",
    "site reliability",
    "infrastructure",
    "platform engineer",
    "cloud engineer",
    "systems engineer",
    "network engineer",
    # Large employers title a co-op "Technology Internship Program" with no
    # infrastructure word anywhere, so the early-career words have to be swept
    # too. "intern" and "co-op" are deliberately absent: measured against four
    # tenants they return 59-94% and 93-100% of the board respectively.
    "internship",
    "coop",
    "early career",
    "university",
    "student",
)

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "filters.yaml"


@dataclasses.dataclass(frozen=True)
class Config:
    title: re.Pattern
    title_cased: re.Pattern | None
    kind: re.Pattern
    kind_negative: re.Pattern
    wanted_terms: frozenset[str]
    board_require_explicit_terms: bool
    location_deny: re.Pattern | None
    location_allow_extra: re.Pattern | None
    max_notify_per_run: int
    boards: dict[str, tuple[str, ...]]
    workday_search_terms: tuple[str, ...]
    board_companies: dict[str, dict[str, str]]

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        path = pathlib.Path(path or os.environ.get("JOBSCOUT_CONFIG") or DEFAULT_PATH)
        raw = yaml.safe_load(path.read_text()) or {}
        boards = raw.get("boards") or {}
        return cls(
            title=re.compile(raw["title_pattern"], re.I),
            # Deliberately not re.I: see the note in filters.yaml.
            title_cased=(
                re.compile(raw["title_pattern_cased"])
                if raw.get("title_pattern_cased")
                else None
            ),
            kind=re.compile(raw["kind_pattern"], re.I),
            kind_negative=re.compile(raw["kind_negative"], re.I),
            wanted_terms=frozenset(t.lower() for t in raw.get("wanted_terms", ())),
            board_require_explicit_terms=bool(raw.get("board_require_explicit_terms", False)),
            location_deny=boundary_pattern(raw.get("location_deny", ())),
            location_allow_extra=boundary_pattern(raw.get("location_allow_extra", ())),
            max_notify_per_run=int(raw.get("max_notify_per_run", 15)),
            boards={k: tuple(v or ()) for k, v in boards.items()},
            workday_search_terms=tuple(raw.get("workday_search_terms") or WORKDAY_TERMS),
            board_companies={
                k: dict(v or {}) for k, v in (raw.get("board_companies") or {}).items()
            },
        )
