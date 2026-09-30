"""Posting-grounded local-model selection with deterministic coverage checks."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import urllib.request
from collections import OrderedDict

log = logging.getLogger(__name__)


_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": 0,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "terms": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                },
                "required": ["id"],
            },
        }
    },
    "required": ["items"],
}

_SKILLS = (
    "Python", "C/C++", "C++", "C#", "JavaScript", "TypeScript", "Java", "Rust", "Golang",
    "Kotlin", "Swift", "SQL", "PostgreSQL", "AWS", "Azure", "GCP", "Docker",
    "Kubernetes", "Terraform", "Linux", "Unix", "Windows", "macOS", "iOS",
    "Office 365", "Outlook", "Git", "React", "Node.js", "MATLAB",
    "Simulink", "SolidWorks", "AutoCAD", "CAD", "FPGA", "PCB", "ROS",
    "PyTorch", "TensorFlow", "machine learning", "data analysis", "statistics",
    "Claude", "Codex", "ChatGPT", "technical writing", "project management",
    "leadership", "communication", "problem solving", "cross-functional",
    "IP networking", "telecommunications", "protocol analyzers", "cyber security",
    "security analysis", "software test", "systems engineering", "mechanical engineering",
    "chemical engineering", "Design of Experiments", "DOE", "data analytics",
    "Rhino", "Siemens NX", "plastics molding", "mold design", "3D models",
)


def _exact_term(source: str, term: object) -> str | None:
    """Keep model terms only when the exact phrase occurs in the posting excerpt."""
    if not isinstance(term, str):
        return None
    term = re.sub(r"\s+", " ", term).strip(" ,.;:–—-")
    if not 2 <= len(term) <= 48 or len(term.split()) > 6:
        return None
    match = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", source, re.I)
    return match.group() if match else None


def _compact_fallback(source: str, kind: str) -> str:
    if kind == "pay":
        match = re.search(r"\$\s?\d[\d,.]*(?:\s*[-–]\s*\$?\s?\d[\d,.]*)?(?:\s*(?:/hr|per hour|hourly|per year))?", source, re.I)
        if match:
            return match.group()
    if kind == "dates":
        match = re.search(r"\b(?:\d+[ -]?(?:to|[-–])[ -]?\d+[ -]?weeks?|\d+[ -]?weeks?|(?:Spring|Summer|Fall|Winter)\s+20\d{2})\b", source, re.I)
        if match:
            return match.group()
    if kind == "required":
        match = re.search(r"\b(?:degree|major) in ([\w -]+?(?:Engineering|Science|Mathematics|Business))\b", source, re.I)
        if match:
            return match.group(1)
    if kind == "location":
        match = re.search(r"\b(?:work|based|located) in ([A-Z][\w ]{2,40})(?:[,.;]|$)", source)
        if match:
            return match.group(1).strip()
    prefix = re.split(r"[,;.]|\s+\b(?:including|such as)\b", source, maxsplit=1)[0].strip()
    if kind == "work":
        prefix = re.split(r"\s+\b(?:for|in order to)\b", prefix, maxsplit=1)[0].strip() or prefix
    return " ".join(prefix.split()[:8]).rstrip(" ,.;:")


def _explicit_skills(excerpts: list[dict[str, str]]) -> list[str]:
    text = "\n".join(item["text"] for item in excerpts)
    found = []
    for skill in sorted(_SKILLS, key=len, reverse=True):
        term = _exact_term(text, skill)
        if term and not any(term.casefold() in previous.casefold() for previous in found):
            found.append(term)
    return found[:12]


def _model_selections(raw: object) -> list[dict]:
    """Keep valid IDs even when a local model truncates its JSON response."""
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("local model returned malformed overview JSON; using valid IDs only")
        return [{"id": int(value)} for value in re.findall(r'"id"\s*:\s*(\d{1,3})(?!\d)', str(raw))[:10]]
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        return []
    return [item for item in document["items"][:10] if isinstance(item, dict)]


def _location_terms(section: str, source: str) -> list[str]:
    """Use only role-specific work mode or location phrases, never travel/HR boilerplate."""
    if section in {"about", "benefits", "nice_to_have"}:
        return []
    patterns = (
        (r"\b(?:not |no |fully |entirely |primarily |mostly |partially )?"
         r"(?:on[- ]site|onsite)(?: work| schedule| position| role| only)?\b", re.I),
        (r"\b(?:not |no |fully |entirely |primarily |mostly |partially )?"
         r"remote(?:[- ]first| work| schedule| position| role| job| only)\b", re.I),
        (r"\b(?:fully|entirely|primarily|mostly|partially) remote\b", re.I),
        (r"\b(?:partially |mostly )?hybrid(?: work| schedule| position| role| arrangement| model)\b", re.I),
        (r"\b(?i:hybrid|remote) in [A-Z][A-Za-z.]+(?:,?\s+[A-Z][A-Za-z.]+){0,3}\b", 0),
        (r"\b(?:work mode|work arrangement|location)\s*:\s*(?:remote|hybrid|on[- ]site|onsite)\b", re.I),
        (r"\b(?i:willingness to work|(?:required|expected) to work|you will work|based|located) in "
         r"[A-Z][A-Za-z.]+(?:,?\s+[A-Z][A-Za-z.]+){0,4}\b", 0),
        (r"\brelocation assistance (?:provided|available|offered)\b", re.I),
    )
    found: list[str] = []
    for pattern, flags in patterns:
        for match in re.finditer(pattern, source, flags):
            term = match.group().strip()
            if term.casefold() not in {existing.casefold() for existing in found}:
                found.append(term)
    return found[:3]


def candidates(sections: list[dict] | tuple[dict, ...]) -> list[dict[str, str]]:
    """Bound the model input to source-exact posting excerpts."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for section in sections:
        key = str(section.get("key") or "about")
        for line in str(section.get("text") or "").splitlines():
            line = re.sub(r"^[\s\-*•]+", "", line).strip()
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
            pieces = []
            for sentence in sentences:
                if len(sentence) <= 320:
                    pieces.append(sentence)
                    continue
                # Export-control and legal sentences can be long; split at
                # their own punctuation so a real requirement is not dropped.
                current = ""
                for clause in re.split(r"(?<=[,;])\s+", sentence):
                    if current and len(current) + len(clause) + 1 > 320:
                        pieces.append(current)
                        current = ""
                    if len(clause) > 320:
                        words = clause.split()
                        for word in words:
                            if current and len(current) + len(word) + 1 > 320:
                                pieces.append(current)
                                current = ""
                            current = f"{current} {word}".strip()
                    else:
                        current = f"{current} {clause}".strip()
                if current:
                    pieces.append(current)
            for piece in pieces:
                piece = piece.strip()
                normalized = re.sub(r"\s+", " ", piece).casefold()
                short_requirement = key in {"requirements", "nice_to_have"} and (
                    6 <= len(piece) <= 320 or any(piece.casefold() == skill.casefold() for skill in _SKILLS)
                )
                if (not (24 <= len(piece) <= 320 or short_requirement)
                        or piece.endswith(":")
                        or normalized in seen
                        or re.search(r"\b(?:equal opportunity employer|employment decisions are made|competitive compensation|vaccination mandates)\b", normalized)
                        or normalized.startswith((
                            "we are an equal opportunity", "for more information",
                            "click here", "your responsibilities may include",
                        ))):
                    continue
                seen.add(normalized)
                result.append({"section": key, "text": piece})
                if len(result) >= 80:
                    return result
    return result


def _excerpt_kind(section: str, text: str) -> str | None:
    """Classify only explicit posting facts, not model-chosen labels."""
    lower = text.casefold()
    if re.search(r"\$\s?\d|\b(?:salary|hourly rate|pay range|compensation range)\b", lower) and re.search(r"\d|\$", lower):
        return "pay"
    if re.search(r"\b(?:\d+[ -]?(?:to|[-–])[ -]?\d+[ -]?weeks?|\d+[ -]?weeks?|start(?:s|ing)? (?:in|on)|begin(?:s|ning)? in|through (?:june|august|december)|spring 20\d\d|summer 20\d\d|fall 20\d\d)\b", lower):
        return "dates"
    if _location_terms(section, text):
        return "location"
    if section == "nice_to_have" or re.search(r"\b(?:preferred|nice to have|bonus qualification)\b", lower):
        return "preferred"
    if section == "requirements":
        return "required"
    if section == "responsibilities" and not re.search(r"\b(?:equal opportunity|great work environment|competitive compensation|employment decisions)\b", lower):
        return "work"
    if section == "role" and (
        re.search(r"\b(?:you will|you'll|in this role|as an? .{0,70} you|build|design|develop|implement|test|support|assist|work with)\b", lower)
        or re.match(r"(?:provide|understanding|cross.functional collaboration|coordinate|contribute|participate|manage|analyze|create)\b", lower)
    ):
        return "work"
    if section in {"about", "logistics"}:
        if re.search(r"\b(?:must|is required|experience with|familiar with|proficiency in|degree in|currently enrolled)\b", lower):
            return "required"
        if re.search(r"\b(?:you will|you'll|tasks may include|in this role|you will work|you'll work|you will support)\b", lower):
            return "work"
    return None


class OverviewService:
    def __init__(self, url: str, model: str, *, opener=None):
        self.url = url.rstrip("/") + "/api/generate"
        self.model = model
        self.opener = opener or urllib.request.urlopen
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, list[dict[str, str]]] = OrderedDict()
        self._fallback_keys: set[str] = set()

    def _cache_key(self, excerpts: list[dict[str, str]]) -> str:
        digest = hashlib.sha256(json.dumps(excerpts, sort_keys=True).encode()).hexdigest()
        return f"v5:{self.model}:{digest}"

    def cached_overview(self, sections: list[dict] | tuple[dict, ...]) -> list[dict[str, str]] | None:
        key = self._cache_key(candidates(sections))
        with self._lock:
            items = self._cache.get(key)
            if items is not None:
                self._cache.move_to_end(key)
            return items

    def is_fallback(self, sections: list[dict] | tuple[dict, ...]) -> bool:
        """True when the displayed facts were selected without a valid model ID."""
        key = self._cache_key(candidates(sections))
        with self._lock:
            return key in self._fallback_keys

    def overview(self, sections: list[dict] | tuple[dict, ...]) -> list[dict[str, str]]:
        excerpts = candidates(sections)
        if not excerpts:
            return []
        cache_key = self._cache_key(excerpts)
        with self._lock:
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            numbered = "\n".join(
                f"{index}. [{item['section']}] {item['text']}"
                for index, item in enumerate(excerpts)
            )
            prompt = (
                "Rank up to 10 numbered excerpts for a job applicant. First select what "
                "the person will actually do, then explicit technical skills and hard "
                "requirements, then preferred skills, pay, work mode and dates. Never "
                "choose headings, generic employer praise, or duplicate logistics. "
                "For each selected ID, add 1-3 short terms: exact contiguous phrases of "
                "at most six words copied from that excerpt (for example Python or "
                "Build reliable tooling). Do not paraphrase or create facts.\n\n"
                + numbered
            )
            payload = json.dumps({
                "model": self.model, "prompt": prompt, "stream": False,
                "think": False, "format": _SCHEMA,
                "options": {"temperature": 0, "num_predict": 512},
            }).encode()
            request = urllib.request.Request(
                self.url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with self.opener(request, timeout=30) as response:
                result = json.loads(response.read(65537))
            selected = _model_selections(result.get("response") if isinstance(result, dict) else None)
            ranked = [item.get("id") for item in selected if isinstance(item, dict)]
            ranked = [index for index in ranked if type(index) is int and 0 <= index < len(excerpts)]
            ranked.extend(index for index in range(len(excerpts)) if index not in ranked)
            model_terms = {
                item["id"]: item.get("terms", [])
                for item in selected if isinstance(item, dict) and type(item.get("id")) is int
                and 0 <= item["id"] < len(excerpts)
            }
            limits = {"work": 3, "required": 3, "preferred": 2, "pay": 1, "location": 2, "dates": 1}
            groups: dict[str, list[dict[str, str | list[str]]]] = {kind: [] for kind in limits}
            for index in ranked:
                source = excerpts[index]
                kind = _excerpt_kind(source["section"], source["text"])
                if kind and len(groups[kind]) < limits[kind] and not any(item["text"] == source["text"] for item in groups[kind]):
                    proposed = model_terms.get(index, [])
                    terms = (_location_terms(source["section"], source["text"]) if kind == "location"
                             else [_exact_term(source["text"], term) for term in proposed] if isinstance(proposed, list) else [])
                    terms = list(dict.fromkeys(term for term in terms if term))[:3]
                    groups[kind].append({"kind": kind, "text": source["text"], "terms": terms or [_compact_fallback(source["text"], kind)]})
            items = [
                item for kind in limits for item in groups[kind]
            ]
            skills = _explicit_skills(excerpts)
            if skills:
                items.insert(len(groups["work"]), {"kind": "skills", "text": ", ".join(skills), "terms": skills})
            self._cache[cache_key] = items
            if not any(type(item.get("id")) is int and 0 <= item["id"] < len(excerpts) for item in selected):
                self._fallback_keys.add(cache_key)
            if len(self._cache) > 256:
                removed, _ = self._cache.popitem(last=False)
                self._fallback_keys.discard(removed)
            return items
