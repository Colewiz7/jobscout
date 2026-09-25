"""Does this posting belong in the feed."""
from __future__ import annotations

import re

from .config import Config, boundary_pattern
from .models import Posting

# "Austin, TX" / "Manassas, VA (HQ)" / Workday's "US-NY-Rochester". A code only
# counts when a comma or dash actually delimits it, so a title full of capitals
# still cannot fake a location match: an undelimited cell yields one field and
# is rejected outright. Splitting on both delimiters at once is what keeps
# "Winston-Salem, NC" working.
_LOC_DELIM = re.compile(r"[,\u2013\u2014-]")
_LEADING_CODE = re.compile(r"\s*([A-Z]{2})\b")
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


def _has_state_code(segment: str) -> bool:
    """A delimited two-letter field naming a US state or DC/PR.

    Workday writes country first, so "CA-ON-Toronto" leads with Canada, not
    California. Any three-field cell that opens with a country code other than
    US is therefore foreign no matter what follows, which is the one case where
    a valid state code must be ignored rather than trusted.
    """
    fields = _LOC_DELIM.split(segment)
    if len(fields) < 2:
        return False
    if len(fields) >= 3:
        head = _LEADING_CODE.fullmatch(fields[0].strip())
        if head:
            if head.group(1) != "US":
                return False
            fields = fields[1:]
    for field in fields:
        head = _LEADING_CODE.match(field)
        if head and head.group(1) in _VALID_CODES:
            return True
    return False


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
        if _has_state_code(segment):
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


# Workday collapses a multi-site posting to "3 Locations" and names none of
# them, so the cell has to be let through. When it is, the country is often
# still sitting in the title: Micron's "Technology Development, Research &
# Innovation Internship (Singapore)" arrived as "3 Locations".
_COLLAPSED_LOCATION = re.compile(r"^\s*(?:\d+|multiple|several)\s+locations\s*$", re.I)


def _location_is_collapsed(location: str) -> bool:
    return any(
        _COLLAPSED_LOCATION.match(part) for part in re.split(r"[;\n]", location or "")
    )


# Scoring. A match is binary but a feed is ordered, and the cap means the
# ordering decides what is actually seen.
_ROLE_STRONG = re.compile(
    r"devops|devsecops|\bsre\b|site reliability|production engineer|"
    r"platform engineer|infrastructure|sysadmin|systems? administrat|"
    r"systems? engineer|systems? development engineer|network engineer|"
    r"cloud engineer|cloud (?:hardware|support)|data cent(?:er|re)|"
    r"reliability engineer|(?:build|release) engineer|kubernetes|linux",
    re.I,
)
# Words that only hint at the right family. "Technology Intern" is worth
# hearing about; it should not outrank a posting that says DevOps.
_ROLE_WEAK = re.compile(r"technolog|cloud|platform|network|systems?|\bIT\b", re.I)
# Disciplines that share the vocabulary but are not this job.
_OFF_TARGET = re.compile(
    r"aerodynamic|fluid dynamics|mechanical|chemical|civil|biolog|materials|"
    r"quantum|applied science|actuarial|audit|photonic|optical|dram|wafer|"
    r"semiconductor|yield|firmware physical design",
    re.I,
)
_COOP = re.compile(r"co-?op", re.I)
_SEASON_ORDER = {"spring": 0, "summer": 1, "fall": 2, "autumn": 2, "winter": 3}


def _term_rank(term: str) -> tuple[int, int] | None:
    """(year, season) so two terms can be compared."""
    parts = term.split()
    if len(parts) != 2 or parts[0] not in _SEASON_ORDER:
        return None
    try:
        return int(parts[1]), _SEASON_ORDER[parts[0]]
    except ValueError:
        return None
_INTERNSHIP = re.compile(r"intern(ship)?\b", re.I)
# "Systems Engineer Intern - Charleston, SC" is the same job as the Honolulu
# one. Strip a trailing place so the twins collapse to a single entry.
_TRAILING_PLACE = re.compile(r"\s*[-\u2013\u2014,]\s*[A-Za-z .'\u2019]+,\s*[A-Z]{2}\.?\s*$")


def _title_key(title: str) -> str:
    return re.sub(r"\s+", " ", _TRAILING_PLACE.sub("", title or "")).strip().lower()


def score_breakdown(row, config: Config) -> tuple[int, dict]:
    """The score and why, so a ranking can be audited after the fact."""
    title = row.get("title") or ""
    detail: dict[str, int] = {}

    if _ROLE_STRONG.search(title):
        detail["role_named"] = 100
    elif _ROLE_WEAK.search(title):
        detail["role_hinted"] = 25
    if _OFF_TARGET.search(title):
        detail["off_target_discipline"] = -60
    if _COOP.search(title):
        detail["co_op"] = 40
    elif _INTERNSHIP.search(title):
        detail["internship"] = 20

    found = extract_terms((row.get("terms") or "") + " " + title)
    if config.wanted_terms:
        if found & config.wanted_terms:
            detail["wanted_term"] = 30
        elif found:
            # A posting that names a term, and none of them is one we want,
            # is advertising a cycle that has already gone.
            ours = [_term_rank(t) for t in config.wanted_terms]
            theirs = [_term_rank(t) for t in found]
            ours = [r for r in ours if r]
            theirs = [r for r in theirs if r]
            if ours and theirs and max(theirs) < min(ours):
                detail["stale_term"] = -70

    age = row.get("age_days")
    if age is not None and age <= 7:
        detail["fresh"] = 10

    return sum(detail.values()), detail


def score(row, config: Config) -> int:
    return score_breakdown(row, config)[0]


def rank(rows, config: Config) -> list:
    """Best first, one row per job.

    Booz Allen posts a single co-op once per city, eleven rows for one job,
    which would fill the notification budget on its own.
    """
    best: dict[tuple[str, str], tuple[int, dict]] = {}
    for row in rows:
        key = ((row.get("company") or "").lower(), _title_key(row.get("title")))
        points = score(row, config)
        if key not in best or points > best[key][0]:
            best[key] = (points, row)
    ordered = sorted(
        best.values(),
        key=lambda pair: (-pair[0], pair[1].get("age_days") if pair[1].get("age_days") is not None else 999),
    )
    return [row for _, row in ordered]


def keep(posting: Posting, config: Config) -> bool:
    if not title_matches(posting.title, config):
        return False
    if not location_matches(posting.location, config, posting.remote):
        return False
    # A collapsed cell hid the country, so give the deny list the title too.
    if (
        config.location_deny is not None
        and _location_is_collapsed(posting.location)
        and config.location_deny.search(posting.title or "")
    ):
        return False
    return terms_match(posting, config)
