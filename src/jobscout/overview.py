"""Posting-grounded local-model selection with deterministic coverage checks."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.request
from collections import OrderedDict


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
                },
                "required": ["id"],
            },
        }
    },
    "required": ["items"],
}


def candidates(sections: list[dict] | tuple[dict, ...]) -> list[dict[str, str]]:
    """Bound the model input to source-exact posting excerpts."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for section in sections:
        key = str(section.get("key") or "about")
        for line in str(section.get("text") or "").splitlines():
            line = re.sub(r"^[\s\-*•]+", "", line).strip()
            if len(line) > 320:
                pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
            else:
                pieces = [line]
            for piece in pieces:
                piece = piece.strip()
                normalized = re.sub(r"\s+", " ", piece).casefold()
                short_requirement = key in {"requirements", "nice_to_have"} and 6 <= len(piece) <= 320
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
    if re.search(r"\b(?:\d+[ -]?(?:to|[-–])[ -]?\d+[ -]?week|\d+[ -]?week|start(?:s|ing)? (?:in|on)|through (?:june|august|december)|spring 20\d\d|summer 20\d\d|fall 20\d\d)\b", lower):
        return "dates"
    if re.search(r"\b(?:remote|hybrid|on-site|onsite|relocation|willingness to work in |based in |work location|travel)\b", lower):
        return "location"
    if section == "nice_to_have" or re.search(r"\b(?:preferred|nice to have|bonus qualification)\b", lower):
        return "preferred"
    if section == "requirements":
        return "required"
    if section in {"role", "responsibilities"} and not re.search(r"\b(?:equal opportunity|great work environment|competitive compensation|employment decisions)\b", lower):
        return "work"
    return None


class OverviewService:
    def __init__(self, url: str, model: str, *, opener=None):
        self.url = url.rstrip("/") + "/api/generate"
        self.model = model
        self.opener = opener or urllib.request.urlopen
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, list[dict[str, str]]] = OrderedDict()

    def _cache_key(self, excerpts: list[dict[str, str]]) -> str:
        digest = hashlib.sha256(json.dumps(excerpts, sort_keys=True).encode()).hexdigest()
        return f"v2:{self.model}:{digest}"

    def cached_overview(self, sections: list[dict] | tuple[dict, ...]) -> list[dict[str, str]] | None:
        key = self._cache_key(candidates(sections))
        with self._lock:
            items = self._cache.get(key)
            if items is not None:
                self._cache.move_to_end(key)
            return items

    def overview(self, sections: list[dict] | tuple[dict, ...]) -> list[dict[str, str]]:
        excerpts = candidates(sections)
        if len(excerpts) < 3:
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
                "Return only IDs in the JSON schema. Do not create facts.\n\n"
                + numbered
            )
            payload = json.dumps({
                "model": self.model, "prompt": prompt, "stream": False,
                "think": False, "format": _SCHEMA,
                "options": {"temperature": 0, "num_predict": 256},
            }).encode()
            request = urllib.request.Request(
                self.url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with self.opener(request, timeout=30) as response:
                result = json.loads(response.read(65537))
            selected = json.loads(result["response"]).get("items", [])
            ranked = [item.get("id") for item in selected if isinstance(item, dict)]
            ranked = [index for index in ranked if type(index) is int and 0 <= index < len(excerpts)]
            ranked.extend(index for index in range(len(excerpts)) if index not in ranked)
            limits = {"work": 3, "required": 3, "preferred": 2, "pay": 1, "location": 2, "dates": 1}
            groups: dict[str, list[str]] = {kind: [] for kind in limits}
            for index in ranked:
                source = excerpts[index]
                kind = _excerpt_kind(source["section"], source["text"])
                if kind and len(groups[kind]) < limits[kind] and source["text"] not in groups[kind]:
                    groups[kind].append(source["text"])
            items = [
                {"kind": kind, "text": text}
                for kind in limits for text in groups[kind]
            ]
            self._cache[cache_key] = items
            if len(self._cache) > 256:
                self._cache.popitem(last=False)
            return items
