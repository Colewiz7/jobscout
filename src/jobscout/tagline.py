"""One-line posting taglines: the model reads the posting, code builds the line.

The model returns structured facts. Everything that decides the fit colour or
raises a flag is checked here, because a small model invents blockers.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
import urllib.request

from . import db
from .overview_jobs import in_batch_window

log = logging.getLogger("jobscout.tagline")

# Bump when the output format or the checks change, so every tagline is rewritten.
FORMAT_VERSION = "v3"
UNCLEAR = "unclear, read posting"
TERM_UNCLEAR = "term unclear"
_THIN_DESCRIPTION = 150
_MAX_DESCRIPTION = 7000
_EMOJI = {"High": "🟢", "Med": "🟡", "Low": "🔴"}

# Hard flags force Low. Soft flags cap the fit at Med.
HARD_KINDS = ("major_excludes_it", "clearance_active", "grad_only", "term_outside", "non_us")
SOFT_KINDS = ("coding_interview",)

_SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "string", "enum": ["High", "Med", "Low", "Unclear"]},
        "work": {"type": "string"},
        "flags": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [*HARD_KINDS, *SOFT_KINDS]},
                    "text": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["kind", "text", "evidence"],
            },
        },
        "term": {"type": "string"},
        "pay": {"type": "string"},
        "location": {"type": "string"},
    },
    "required": ["fit", "work", "flags", "term", "pay", "location"],
}

_INSTRUCTIONS = """OUTPUT
Reply with one JSON object and nothing else:
- fit: High, Med or Low for this candidate. Unclear only if the description is missing or too thin to judge.
- work: what the work actually is, under 10 words.
- flags: blocking problems, each {kind, text, evidence}. Use an empty list when there are none.
  - text: a few words, for example "requires EE or ME degree".
  - evidence: an exact quote from the description that proves it. No quote, no flag.
  - kind is one of:
    - major_excludes_it: a list of required majors that leaves out IT and computing, with no "or related field" style wording.
    - clearance_active: an active security clearance is required at the start. Being able or willing to obtain one is NOT a flag.
    - grad_only: PhD or Master's students only.
    - term_outside: the dates fall outside Spring or Summer 2027.
    - non_us: the job is outside the U.S.
    - coding_interview: the role is coding-interview or algorithm heavy.
- term: the term and dates, or an empty string if the description gives none.
- pay: the pay, plus any housing stipend, or an empty string if none is listed.
- location: city and onsite, hybrid or remote.
Never raise a flag from the title, from citizenship or relocation wording, or from a major list that includes "or related field"."""


def prompt_hash(template: str, model: str) -> str:
    return hashlib.sha256(f"{FORMAT_VERSION}\0{model}\0{template}".encode()).hexdigest()[:16]


def load_prompt(path: str | None) -> str | None:
    """The prompt holds personal details, so it is mounted rather than committed."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    return text or None


def build_prompt(template: str, job: dict, description: str) -> str:
    # The mounted prompt describes the candidate and the rules. The output format
    # belongs to the code, so anything from its own OUTPUT heading on is replaced.
    head = re.split(r"^\s*OUTPUT\b", template, maxsplit=1, flags=re.M)[0].strip()
    examples = re.split(r"^\s*EXAMPLES\b", template, maxsplit=1, flags=re.M)
    calibration = (
        "\n\nCALIBRATION EXAMPLES\nThese show how hard to judge fit and how plainly to describe the work. "
        "Your reply is still the JSON object below.\n" + examples[1].strip()
        if len(examples) == 2 and examples[1].strip() else ""
    )
    facts = [
        ("Company", job.get("company")),
        ("Title", job.get("title")),
        ("Location", job.get("location")),
        ("Term listed", job.get("terms")),
        ("Pay listed", job.get("pay")),
    ]
    posting = "\n".join(f"{label}: {' '.join(str(value).split())}" for label, value in facts if value)
    return f"{head}{calibration}\n\n{_INSTRUCTIONS}\n\nPOSTING\n{posting}\n\nDESCRIPTION\n{description[:_MAX_DESCRIPTION]}"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


# What the evidence must show, and wording around it that makes the flag wrong.
_REQUIRE = {
    "major_excludes_it": re.compile(r"degree|major|engineering|pursuing|studying|discipline|field|student", re.I),
    "clearance_active": re.compile(r"clearance|secret|ts/sci|top secret|polygraph", re.I),
    "grad_only": re.compile(r"ph\.?d|doctora|master|graduate", re.I),
    "term_outside": re.compile(r"fall|winter|autumn|20(?:2[0-6]|2[89])", re.I),
    "coding_interview": re.compile(
        r"leetcode|hackerrank|coding (?:interview|challenge|assessment|test)|algorithm|data structures"
        r"|whiteboard|competitive programming|technical screen", re.I),
}
# A quote that itself says the clearance is held or active at the start stands
# even when a nearby sentence talks about obtaining one.
_ACTIVE = re.compile(r"\bactive\b|currently (?:hold|possess)|must (?:hold|possess)|at (?:the )?start|upon start", re.I)
# A quote under a "preferred" style heading is a wish, not a requirement.
_PREFERRED = re.compile(r"stand out|preferred|nice to have|bonus|a plus|desired|ideal candidate|helpful", re.I)
_REJECT = {
    "major_excludes_it": re.compile(
        r"related|equivalent|similar|computing|information (?:technology|systems)|\bcit\b|\bit\b"
        r"|any (?:major|discipline)|stem\b|technical (?:field|discipline)", re.I),
    "clearance_active": re.compile(
        r"obtain|eligib|ability to|able to|willing|capable|sponsor|apply for|can get|be granted|process", re.I),
    "grad_only": re.compile(r"bachelor|undergrad|\bb\.?s\.?\b|junior|sophomore|senior\b", re.I),
    # Graduation windows and degree dates describe the student, not the internship.
    "term_outside": re.compile(r"graduat|expected completion|degree (?:date|completion)|class of", re.I),
}


def check_flags(raw: object, description: str) -> list[dict]:
    """Keep a flag only when its quote is in the posting and it passes its own test."""
    if not isinstance(raw, list):
        return []
    haystack = _norm(description)
    kept: dict[str, dict] = {}
    for flag in raw:
        if not isinstance(flag, dict):
            continue
        kind, text, evidence = flag.get("kind"), flag.get("text"), flag.get("evidence")
        if kind not in (*HARD_KINDS, *SOFT_KINDS) or kind in kept:
            continue
        if not isinstance(text, str) or not isinstance(evidence, str):
            continue
        needle = _norm(evidence)
        position = haystack.find(needle) if len(needle) >= 12 else -1
        if position < 0:
            continue
        # Judge the quote together with the words around it: "or related field"
        # often sits just outside what the model chose to quote.
        context = haystack[max(0, position - 80): position + len(needle) + 120]
        if kind in _REQUIRE and not _REQUIRE[kind].search(needle):
            continue
        if kind in HARD_KINDS and _PREFERRED.search(haystack[max(0, position - 250): position]):
            continue
        if kind in _REJECT and not (kind == "clearance_active" and _ACTIVE.search(needle)) \
                and _REJECT[kind].search(context if kind not in ("grad_only", "term_outside") else needle):
            continue
        text = " ".join(text.replace("⚠", "").split()).strip(" .,;:")
        if not text:
            continue
        kept[kind] = {"kind": kind, "text": text[:70], "tier": "hard" if kind in HARD_KINDS else "soft"}
    return list(kept.values())


def settle_fit(fit: str, flags: list[dict]) -> str:
    if any(flag["tier"] == "hard" for flag in flags):
        return "Low"
    if any(flag["tier"] == "soft" for flag in flags) and fit == "High":
        return "Med"
    return fit


_NO_VALUE = re.compile(r"(?:n/?a|none|unknown|unclear|not (?:listed|stated|specified|provided|disclosed)|unlisted|tbd)\.?", re.I)


def _clean(value: object) -> str:
    return " ".join(value.split()).strip(" .,;:") if isinstance(value, str) else ""


def _short_location(location: str) -> str:
    places = [place.strip() for place in re.split(r"[;|]", location) if place.strip()]
    if len(places) <= 2:
        return location
    return f"{places[0]}, {places[1]} +{len(places) - 2} more"


def build_tagline(facts: dict, description: str, listed_term: str = "") -> str:
    fit = facts.get("fit")
    if fit == "Unclear":
        return UNCLEAR
    if fit not in _EMOJI:
        raise ValueError("The model returned no usable fit")
    work = " ".join(_clean(facts.get("work")).split()[:10])
    if not work:
        raise ValueError("The model returned no description of the work")
    flags = check_flags(facts.get("flags"), description)
    term = _clean(facts.get("term"))
    if not term or _NO_VALUE.fullmatch(term):
        term = _clean(listed_term)
    pay = _clean(facts.get("pay"))
    location = _short_location(_clean(facts.get("location")))
    parts = [f"{_EMOJI[settle_fit(fit, flags)]} {settle_fit(fit, flags)}", work]
    parts += [f"⚠ {flag['text']}" for flag in flags]
    parts.append(TERM_UNCLEAR if not term or _NO_VALUE.fullmatch(term) else term)
    if pay and not _NO_VALUE.fullmatch(pay) and not re.match(r"(?:pay|salary|compensation)\b.*\bnot\b", pay, re.I):
        parts.append(pay)
    if location and not _NO_VALUE.fullmatch(location):
        parts.append(location)
    return " · ".join(parts)


class TaglineService:
    def __init__(self, url: str, model: str, template: str, *, opener=None):
        self.url = url.rstrip("/") + "/api/generate"
        self.model = model
        self.template = template
        self.hash = prompt_hash(template, model)
        self.opener = opener or urllib.request.urlopen

    def generate(self, job: dict, description: str) -> str:
        if len(description.strip()) < _THIN_DESCRIPTION:
            return UNCLEAR
        payload = json.dumps({
            "model": self.model, "prompt": build_prompt(self.template, job, description),
            "stream": False, "think": False, "format": _SCHEMA,
            "options": {"temperature": 0, "num_predict": 400},
        }).encode()
        request = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.opener(request, timeout=60) as response:
            result = json.loads(response.read(65537))
        try:
            facts = json.loads(result.get("response") if isinstance(result, dict) else None)
        except (TypeError, ValueError) as error:
            raise ValueError("The model did not return valid JSON") from error
        if not isinstance(facts, dict):
            raise ValueError("The model did not return a JSON object")
        return build_tagline(facts, description, str(job.get("terms") or ""))


def process(dsn: str, service: TaglineService, *, limit: int = 10, force: bool = False,
            now: dt.datetime | None = None, pause: float = 3.0, max_seconds: float = 150.0,
            max_load: float = 6.0, loadavg=os.getloadavg, clock=time.monotonic,
            sleep=time.sleep) -> dict[str, int]:
    """Tag a small batch, one posting at a time, and stop early when the node is busy.

    Only cached descriptions are used: fetching is the overview worker's job, so
    this step never goes to the network except to ask the model.
    """
    from .dashboard import PostgresStore

    counts = {"ready": 0, "unclear": 0, "retry": 0, "stopped": 0}
    if not force and not in_batch_window(now):
        return counts
    started = clock()
    store = PostgresStore(dsn)
    try:
        jobs = {job["dedupe_key"]: job for job in store.jobs()}
        with db.connect(dsn) as conn:
            db.require_schema(conn)
            keys = db.pending_taglines(conn, list(jobs), service.hash, limit)
        for index, key in enumerate(keys):
            if clock() - started > max_seconds or loadavg()[0] > max_load:
                counts["stopped"] = len(keys) - index
                log.info("taglines: stopping early (time budget or node load)")
                break
            try:
                with db.connect(dsn) as conn:
                    detail = db.cached_description(conn, key)
                text = (detail or {}).get("description_text") or ""
                # A provider that returned nothing readable will not start to; say
                # so on the tagline instead of retrying forever.
                tagline = service.generate(jobs[key], text) if text else UNCLEAR
                with db.connect(dsn) as conn:
                    db.save_tagline(conn, key, tagline, service.hash)
                counts["unclear" if tagline == UNCLEAR else "ready"] += 1
            except Exception as error:
                log.warning("tagline failed for %s: %s", key, error)
                with db.connect(dsn) as conn:
                    db.fail_tagline(conn, key, str(error), service.hash)
                counts["retry"] += 1
            sleep(pause)
    finally:
        store.close()
    return counts
