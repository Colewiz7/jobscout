"""Deterministic eligibility and requirement evidence.

This module deliberately avoids probabilistic scoring. It only reports facts it
can point back to in the posting and exact, user-maintained profile values.
"""
from __future__ import annotations

import re
from typing import Iterable

from .models import normalise


_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_NUMBER = re.compile(r"\b([0-4](?:\.\d{1,2})?)\b")
_EXPLICIT = re.compile(
    r"\b(?:must|required|requirements?|minimum|at least|need(?:ed)? to|"
    r"must have|you have|we require|eligible)\b", re.I,
)
_NO_SPONSORSHIP = re.compile(
    r"\b(?:no|not (?:offer|provide|available for)|unable to (?:offer|provide)|"
    r"will not (?:offer|provide))\s+(?:employment\s+|visa\s+)?sponsorship\b|"
    r"\bwithout (?:current or future )?(?:employment\s+|visa\s+)?sponsorship\b",
    re.I,
)
_NEEDS_SPONSORSHIP = re.compile(
    r"\b(?:need|needs|require|requires|seeking)\s+(?:employment\s+|visa\s+)?sponsorship\b",
    re.I,
)
_NO_SPONSORSHIP_NEEDED = re.compile(
    r"\b(?:do not|does not|don't|doesn't|no)\s+(?:need|require)\s+sponsorship\b|"
    r"\b(?:u\.?s\.?\s+citizen|permanent resident|green card|authorized[^.]{0,50}without sponsorship)\b",
    re.I,
)
_CLEARANCE_REQUIRED = re.compile(
    r"\bclearance\b[^.!?\n]{0,60}\b(?:required|must|need)|"
    r"\b(?:required|must (?:have|hold|possess)|need)\b[^.!?\n]{0,80}\bclearance\b",
    re.I,
)
_NO_CLEARANCE = re.compile(r"^\s*(?:none|no|not held|do not have|don't have)\b", re.I)
_DEGREE_REQUIRED = re.compile(
    r"\b(?:ph\.?d|doctorate|master(?:'s|s)?|m\.?s\.?|bachelor(?:'s|s)?|b\.?s\.?)\b"
    r"[^.!?\n]{0,80}\b(?:required|minimum|must|need)|"
    r"\b(?:required|minimum|must (?:have|hold)|need)\b[^.!?\n]{0,80}"
    r"\b(?:ph\.?d|doctorate|master(?:'s|s)?|m\.?s\.?|bachelor(?:'s|s)?|b\.?s\.?)\b",
    re.I,
)
_GRADUATION = re.compile(r"\b(?:graduat(?:e|es|ed|ing|ion)|class of)\b", re.I)
_GPA = re.compile(r"\b(?:minimum|required|at least)\b[^.!?\n]{0,40}\bgpa\b|\bgpa\b[^.!?\n]{0,40}\b(?:minimum|required|at least)\b", re.I)
_LOCATION_REQUIRED = re.compile(
    r"\b(?:must be|need to be|required to be)\s+(?:based|located|onsite|on-site)\b",
    re.I,
)

# A deliberately editable, conservative vocabulary. A phrase is only marked
# matched when it appears verbatim in both posting evidence and Profile facts.
_SKILLS = (
    "amazon web services", "google cloud", "microsoft azure", "active directory",
    "artificial intelligence", "machine learning", "infrastructure as code",
    "incident response", "capacity planning", "systems thinking",
    "continuous integration", "continuous delivery", "technical communication",
    "c++", "c#", ".net", "node.js", "react", "typescript", "javascript",
    "python", "java", "golang", "rust", "ruby", "php", "swift", "kotlin",
    "linux", "unix", "windows", "kubernetes", "docker", "podman", "terraform",
    "ansible", "aws", "azure", "gcp", "sql", "postgresql", "mysql", "mongodb",
    "redis", "git", "github", "gitlab", "ci/cd", "rest", "graphql", "api",
    "security", "networking", "observability", "prometheus", "grafana", "splunk",
    "data structures", "algorithms", "distributed systems", "documentation",
    "collaboration", "communication", "performance",
)


def _sentences(text: str) -> list[str]:
    return [value.strip(" \t-*•") for value in _SENTENCE.split(text or "") if value.strip(" \t-*•")]


def _profile_map(profile: dict) -> dict[str, str]:
    fields = profile.get("fields") if isinstance(profile, dict) else []
    return {
        str(field.get("key") or "").casefold(): str(field.get("value") or "").strip()
        for field in fields or [] if isinstance(field, dict) and field.get("key")
    }


def _profile_corpus(profile: dict) -> str:
    pieces = list(_profile_map(profile).values())
    pieces.extend(str(value) for value in profile.get("highlights", []) if value)
    for story in profile.get("stories", []) or []:
        if not isinstance(story, dict):
            continue
        pieces.extend(str(value) for value in story.get("competencies", []) if value)
        pieces.extend(str(story.get(key) or "") for key in ("title", "action", "result"))
    return normalise(" ".join(pieces))


def _evidence(text: str, pattern: re.Pattern[str]) -> str | None:
    return next((sentence for sentence in _sentences(text) if pattern.search(sentence)), None)


def _degree_level(value: str) -> int:
    text = normalise(value)
    if re.search(r"\b(ph d|phd|doctorate|doctoral)\b", text):
        return 3
    if re.search(r"\b(master|masters|m s|msc|mba)\b", text):
        return 2
    if re.search(r"\b(bachelor|bachelors|b s|bsc|undergraduate)\b", text):
        return 1
    return 0


def _clearance_level(value: str) -> int:
    text = normalise(value)
    if "ts sci" in text or "top secret" in text:
        return 3
    if re.search(r"\bsecret\b", text):
        return 2
    if "clearance" in text and not _NO_CLEARANCE.search(value):
        return 1
    return 0


def _finding(key: str, label: str, evidence: str, comparison: str, *, hard: bool) -> dict:
    return {
        "key": key,
        "label": label,
        "evidence": evidence,
        "comparison": comparison,
        "hard": hard,
        "provenance": "explicit wording",
    }


def _blockers_and_warnings(text: str, profile: dict) -> tuple[list[dict], list[dict]]:
    fields = _profile_map(profile)
    blockers: list[dict] = []
    warnings: list[dict] = []

    sponsorship = _evidence(text, _NO_SPONSORSHIP)
    authorization = fields.get("work_authorization", "")
    if sponsorship:
        if _NEEDS_SPONSORSHIP.search(authorization) and not _NO_SPONSORSHIP_NEEDED.search(authorization):
            blockers.append(_finding(
                "sponsorship", "Sponsorship conflict", sponsorship,
                "Your Profile says you require sponsorship.", hard=True,
            ))
        elif not authorization:
            warnings.append(_finding(
                "sponsorship-unknown", "Check work authorization", sponsorship,
                "Work authorization is empty in Profile.", hard=False,
            ))

    clearance = _evidence(text, _CLEARANCE_REQUIRED)
    clearance_value = fields.get("clearance", "")
    if clearance:
        required_level = _clearance_level(clearance)
        profile_level = _clearance_level(clearance_value)
        if clearance_value and (_NO_CLEARANCE.search(clearance_value) or profile_level < required_level):
            blockers.append(_finding(
                "clearance", "Clearance mismatch", clearance,
                f"Profile lists {clearance_value}.", hard=True,
            ))
        elif not clearance_value:
            warnings.append(_finding(
                "clearance-unknown", "Check security clearance", clearance,
                "Security clearance is empty in Profile.", hard=False,
            ))

    degree = _evidence(text, _DEGREE_REQUIRED)
    degree_value = fields.get("degree", "")
    if degree:
        required_level = _degree_level(degree)
        profile_level = _degree_level(degree_value)
        if degree_value and required_level and profile_level and profile_level < required_level:
            blockers.append(_finding(
                "degree", "Degree mismatch", degree,
                f"Profile lists {degree_value}.", hard=True,
            ))
        elif not degree_value:
            warnings.append(_finding(
                "degree-unknown", "Check degree requirement", degree,
                "Degree is empty in Profile.", hard=False,
            ))

    graduation = next(
        (sentence for sentence in _sentences(text) if _GRADUATION.search(sentence) and _YEAR.search(sentence)),
        None,
    )
    graduation_value = fields.get("graduation", "")
    if graduation:
        allowed = [int(value) for value in _YEAR.findall(graduation)]
        profile_years = [int(value) for value in _YEAR.findall(graduation_value)]
        if graduation_value and profile_years and not min(allowed) <= profile_years[0] <= max(allowed):
            blockers.append(_finding(
                "graduation", "Graduation date mismatch", graduation,
                f"Profile lists {graduation_value}.", hard=True,
            ))
        elif not graduation_value:
            warnings.append(_finding(
                "graduation-unknown", "Check graduation window", graduation,
                "Graduation date is empty in Profile.", hard=False,
            ))

    gpa = _evidence(text, _GPA)
    gpa_value = fields.get("gpa", "")
    if gpa:
        required = [float(value) for value in _NUMBER.findall(gpa)]
        actual = [float(value) for value in _NUMBER.findall(gpa_value)]
        if actual and required and actual[0] < max(required):
            warnings.append(_finding(
                "gpa", "GPA needs review", gpa,
                f"Profile lists {gpa_value}.", hard=False,
            ))
        elif not gpa_value:
            warnings.append(_finding(
                "gpa-unknown", "Check GPA requirement", gpa,
                "GPA is empty in Profile.", hard=False,
            ))

    location = _evidence(text, _LOCATION_REQUIRED)
    if location:
        profile_location = fields.get("location", "")
        warnings.append(_finding(
            "location", "Check location requirement", location,
            f"Profile lists {profile_location}." if profile_location else "Location is empty in Profile.",
            hard=False,
        ))
    return blockers, warnings


def _requirement_candidates(sections: Iterable[dict], text: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        key = str(section.get("key") or "")
        if key not in {"requirements", "nice_to_have", "responsibilities"}:
            continue
        for sentence in _sentences(str(section.get("text") or "")):
            provenance = (
                "explicit wording" if _EXPLICIT.search(sentence)
                else "posting structure" if key == "requirements"
                else "estimate"
            )
            candidates.append((sentence, provenance))
    if not candidates:
        candidates.extend(
            (sentence, "explicit wording") for sentence in _sentences(text) if _EXPLICIT.search(sentence)
        )
    return candidates


def _requirements(sections: Iterable[dict], text: str, profile: dict) -> list[dict]:
    corpus = _profile_corpus(profile)
    padded_corpus = f" {corpus} "
    seen: set[str] = set()
    requirements: list[dict] = []
    for evidence, provenance in _requirement_candidates(sections, text):
        normalized = normalise(evidence)
        for skill in _SKILLS:
            skill_normalized = normalise(skill)
            if f" {skill_normalized} " not in f" {normalized} " or skill_normalized in seen:
                continue
            seen.add(skill_normalized)
            requirements.append({
                "skill": skill,
                "evidence": evidence,
                "provenance": provenance,
                "matched": f" {skill_normalized} " in padded_corpus,
                "required": provenance == "explicit wording",
            })
            if len(requirements) >= 12:
                return requirements
    return requirements


def analyze(
    *,
    text: str,
    sections: Iterable[dict],
    profile: dict,
    overrides: Iterable[dict] = (),
) -> dict:
    """Return evidence-only eligibility and skill matching for one posting."""
    blockers, warnings = _blockers_and_warnings(text or "", profile or {})
    override_keys = {
        (str(item.get("blocker_key") or ""), str(item.get("evidence") or ""))
        for item in overrides or ()
    }
    for blocker in blockers:
        blocker["overridden"] = (blocker["key"], blocker["evidence"]) in override_keys
    active = [item for item in blockers if not item["overridden"]]
    requirements = _requirements(sections, text or "", profile or {})
    matched = [item for item in requirements if item["matched"]]
    missing_required = [item for item in requirements if item["required"] and not item["matched"]]
    trusted_matches = [item for item in matched if item["provenance"] != "estimate"]
    if trusted_matches and not missing_required:
        band = "Strong direct evidence"
    elif matched:
        band = "Some direct evidence"
    else:
        band = "No direct skill evidence"
    return {
        "verdict": "do_not_apply" if active else "review" if warnings else "clear",
        "blockers": blockers,
        "warnings": warnings,
        "requirements": requirements,
        "match_band": band,
        "overridden": bool(blockers) and not active,
    }
