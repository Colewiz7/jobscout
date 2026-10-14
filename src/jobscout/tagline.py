"""One-line posting taglines from a private prompt and the local model."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import urllib.request

from . import db
from .overview_jobs import in_batch_window

log = logging.getLogger("jobscout.tagline")

UNCLEAR = "unclear, read posting"
_THIN_DESCRIPTION = 150
_MAX_DESCRIPTION = 7000
_FIT = ("🟢", "🟡", "🔴")


def prompt_hash(template: str) -> str:
    return hashlib.sha256(template.encode()).hexdigest()[:16]


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
    facts = [
        ("Company", job.get("company")),
        ("Title", job.get("title")),
        ("Location", job.get("location")),
        ("Term listed", job.get("terms")),
        ("Pay listed", job.get("pay")),
    ]
    posting = "\n".join(f"{label}: {' '.join(str(value).split())}" for label, value in facts if value)
    return f"{template}\n\nPOSTING\n{posting}\n\nDESCRIPTION\n{description[:_MAX_DESCRIPTION]}\n\nWrite the one line now."


# The prompt says to omit pay that is not listed; small models write it anyway.
_NO_PAY = re.compile(
    r"(?:pay|salary|compensation)?\s*(?:is |was )?(?:not|n/a|unlisted|unknown|none)\b[\w ]{0,20}\.?", re.I,
)


def _drop_missing_pay(line: str) -> str:
    parts = line.split(" · ")
    kept = [part for part in parts if not (
        re.match(r"(?:pay|salary|compensation)\b", part, re.I) and _NO_PAY.fullmatch(part)
    )]
    return " · ".join(kept)


def parse_tagline(raw: object) -> str | None:
    """Accept only the one-line format; anything else is a failed attempt."""
    if not isinstance(raw, str):
        return None
    for line in raw.splitlines():
        line = " ".join(line.strip().strip("`\"'*>- ").split())
        if line.startswith(_FIT) and " · " in line and len(line) <= 300:
            return _drop_missing_pay(line)
        if line.casefold() == UNCLEAR:
            return UNCLEAR
    return None


class TaglineService:
    def __init__(self, url: str, model: str, template: str, *, opener=None):
        self.url = url.rstrip("/") + "/api/generate"
        self.model = model
        self.template = template
        self.hash = prompt_hash(template + "\0" + model)
        self.opener = opener or urllib.request.urlopen

    def generate(self, job: dict, description: str) -> str:
        if len(description.strip()) < _THIN_DESCRIPTION:
            return UNCLEAR
        payload = json.dumps({
            "model": self.model, "prompt": build_prompt(self.template, job, description),
            "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": 160},
        }).encode()
        request = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.opener(request, timeout=60) as response:
            result = json.loads(response.read(65537))
        tagline = parse_tagline(result.get("response") if isinstance(result, dict) else None)
        if tagline is None:
            raise ValueError("The model did not return a one-line tagline")
        return tagline


def process(dsn: str, service: TaglineService, *, limit: int = 20,
            force: bool = False, now: dt.datetime | None = None) -> dict[str, int]:
    """Tag a bounded batch. Outside the quiet morning window only `force` runs."""
    from .dashboard import PostgresStore

    counts = {"ready": 0, "retry": 0, "skipped": 0}
    if not force and not in_batch_window(now):
        return counts
    store = PostgresStore(dsn)
    try:
        jobs = {job["dedupe_key"]: job for job in store.jobs()}
        with db.connect(dsn) as conn:
            db.require_schema(conn)
            keys = db.pending_taglines(conn, list(jobs), service.hash, limit)
        for key in keys:
            try:
                detail = store.description(key)
                text = (detail or {}).get("description_text") or ""
                if not text:
                    raise ValueError("Posting description is unavailable")
                tagline = service.generate(jobs[key], text)
                with db.connect(dsn) as conn:
                    db.save_tagline(conn, key, tagline, service.hash)
                counts["ready"] += 1
            except Exception as error:
                log.warning("tagline failed for %s: %s", key, error)
                with db.connect(dsn) as conn:
                    db.fail_tagline(conn, key, str(error), service.hash)
                counts["retry"] += 1
    finally:
        store.close()
    return counts
