"""Does this posting belong in the feed."""
from __future__ import annotations

import re

from .config import Config, boundary_pattern
from .models import Posting

# "Austin, TX" / "Manassas, VA (HQ)". A two-letter code only counts after a
# comma, so a title full of capitals cannot fake a location match.
_STATE_CODE = re.compile(r",\s*([A-Z]{2})\b")
_VALID_CODES = frozenset(
    """AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT
    NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR""".split()
)
_STATE_NAMES = boundary_pattern(
    """alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|
    georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|
    massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|
    new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|
    oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|
    texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming""".replace(
        "\n", ""
    ).split("|")
)

# "Summer 2027", "Summer '27" and "Fall Term 2026" all name a season.
_TERM = re.compile(
    r"\b(spring|summer|fall|autumn|winter)(?:\s+term)?\s*'?(\d{2,4})\b", re.I
)


def extract_terms(text: str) -> set[str]:
    """Pull every `season year` out of a string, normalised to 'summer 2027'."""
    out = set()
    for season, year in _TERM.findall(text or ""):
        season = season.lower()
        if season == "autumn":
            season = "fall"
        if len(year) == 2:
            year = "20" + year
        out.add(f"{season} {year}")
    return out


def title_matches(title: str, config: Config) -> bool:
    """Infrastructure-shaped AND internship-shaped.

    The negative pattern is applied by blanking the offending words first, so
    "Internal Cloud Platform Intern" still qualifies on the real "Intern" while
    "Internal Cloud Platform Engineer" does not.
    """
    title = title or ""
    infra = config.title.search(title) or (
        config.title_cased is not None and config.title_cased.search(title)
    )
    if not infra:
        return False
    return bool(config.kind.search(config.kind_negative.sub(" ", title)))


def location_matches(location: str, config: Config, remote: bool = False) -> bool:
    """US or remote. Any qualifying segment carries the whole cell."""
    location = location or ""
    denied = config.location_deny.search if config.location_deny else lambda _s: None
    allowed = config.location_allow_extra.search if config.location_allow_extra else lambda _s: None

    if remote and not denied(location):
        return True
    for segment in re.split(r"[;\n]", location):
        segment = segment.strip()
        if not segment or denied(segment):
            continue
        if {c for c in _STATE_CODE.findall(segment) if c in _VALID_CODES}:
            return True
        if _STATE_NAMES and _STATE_NAMES.search(segment):
            return True
        if allowed(segment):
            return True
    return False


def terms_match(posting: Posting, config: Config) -> bool:
    """Season filter. Simplify has a Terms column; boards only have the title."""
    if not config.wanted_terms:
        return True
    if posting.source == "simplify-s27":
        # The whole README is one cycle, so there is nothing to check.
        return True
    if posting.source == "simplify-off":
        return bool(extract_terms(posting.terms) & config.wanted_terms)
    found = extract_terms(posting.terms or posting.title)
    if not found:
        # Only 17% of real board internship titles name a season, so an unnamed
        # term is not evidence of the wrong cycle.
        return not config.board_require_explicit_terms
    return bool(found & config.wanted_terms)


def keep(posting: Posting, config: Config) -> bool:
    return (
        title_matches(posting.title, config)
        and location_matches(posting.location, config, posting.remote)
        and terms_match(posting, config)
    )
