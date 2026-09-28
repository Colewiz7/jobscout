"""Push new matches to ntfy."""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


def push(fetcher, base_url: str, topic: str, token: str, title: str, body: str,
         click: str = "") -> bool:
    """Publish as JSON rather than through headers.

    The title went in an HTTP header, which has to be ASCII, so a single
    non-ASCII character in a job title raised UnicodeEncodeError and took the
    whole run down before anything was marked notified. Real titles are full
    of them: RIT writes co-ops as "Co-Op - IT - ..." with en dashes, and a
    campus posting carrying a graduation-cap emoji is what actually found
    this. The JSON body is UTF-8 by definition and has none of that problem.
    """
    payload = {
        "topic": topic,
        "title": title,
        "message": body,
        "tags": ["briefcase"],
        "priority": 3,
    }
    if click:
        payload["click"] = click
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = base_url.rstrip("/")
    response = fetcher.post(
        url, json.dumps(payload).encode("utf-8"), headers
    )
    if response is None:
        log.error("ntfy rejected the push to %s", url)
        return False
    return True


def format_posting(row: dict) -> tuple[str, str]:
    """Returns (title, body) for one posting."""
    title = f"{row['company']}: {row['title']}"[:180]
    lines = [row["location"] or "location not stated"]
    if row.get("terms"):
        lines.append(row["terms"])
    lines.append(row["url"])
    return title, "\n".join(lines)


def format_held(rows: list[dict]) -> tuple[str, str]:
    """The tail that did not fit this run.

    Unlike a capped flood these are not being dropped, so the message says so:
    they stay unnotified and lead the next run.
    """
    title = f"{len(rows)} more queued"
    preview = [f"- {r['company']}: {r['title']}" for r in rows[:8]]
    if len(rows) > 8:
        preview.append(f"...and {len(rows) - 8} more")
    preview.append("")
    preview.append("Held for the next run, ranked below the ones just sent.")
    return title, "\n".join(preview)


def format_summary(rows: list[dict], cap: int) -> tuple[str, str]:
    """One message instead of a flood.

    A source changing format is the realistic way this job goes from 3 new
    matches to 400, and that must not become 400 phone buzzes.
    """
    title = f"{len(rows)} new matches (over the {cap} cap)"
    preview = [f"- {r['company']}: {r['title']}" for r in rows[:10]]
    if len(rows) > 10:
        preview.append(f"...and {len(rows) - 10} more")
    preview.append("")
    preview.append("Capped to one message. Check the postings table.")
    return title, "\n".join(preview)
