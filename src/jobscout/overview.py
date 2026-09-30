"""Posting-grounded local-model overview, with no profile data or DB writes."""
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
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "kind": {"type": "string", "enum": ["role", "work", "requirements", "logistics"]},
                },
                "required": ["id", "kind"],
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
        if key == "benefits":
            continue
        for line in str(section.get("text") or "").splitlines():
            line = re.sub(r"^[\s\-*•]+", "", line).strip()
            if len(line) > 320:
                pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
            else:
                pieces = [line]
            for piece in pieces:
                piece = piece.strip()
                normalized = re.sub(r"\s+", " ", piece).casefold()
                if not (24 <= len(piece) <= 320) or normalized in seen:
                    continue
                seen.add(normalized)
                result.append({"section": key, "text": piece})
                if len(result) >= 80:
                    return result
    return result


class OverviewService:
    def __init__(self, url: str, model: str, *, opener=None):
        self.url = url.rstrip("/") + "/api/generate"
        self.model = model
        self.opener = opener or urllib.request.urlopen
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, list[dict[str, str]]] = OrderedDict()

    def overview(self, sections: list[dict] | tuple[dict, ...]) -> list[dict[str, str]]:
        excerpts = candidates(sections)
        if len(excerpts) < 3:
            return []
        digest = hashlib.sha256(json.dumps(excerpts, sort_keys=True).encode()).hexdigest()
        cache_key = f"{self.model}:{digest}"
        with self._lock:
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            numbered = "\n".join(
                f"{index}. [{item['section']}] {item['text']}"
                for index, item in enumerate(excerpts)
            )
            prompt = (
                "Choose 3 to 5 numbered excerpts that best help an applicant understand "
                "the actual role, day-to-day work, hard requirements, and logistics. "
                "Prefer specific role facts over company marketing. Return only the IDs "
                "and kinds in the JSON schema. Do not create or paraphrase facts.\n\n"
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
            items: list[dict[str, str]] = []
            used: set[int] = set()
            for item in selected:
                index = item.get("id") if isinstance(item, dict) else None
                if type(index) is not int or index < 0 or index >= len(excerpts) or index in used:
                    continue
                used.add(index)
                # Only source excerpts enter the response; generated text is discarded.
                items.append({"kind": excerpts[index]["section"], "text": excerpts[index]["text"]})
                if len(items) == 5:
                    break
            if len(items) < 3:
                raise ValueError("model did not select enough posting excerpts")
            self._cache[cache_key] = items
            if len(self._cache) > 256:
                self._cache.popitem(last=False)
            return items
