"""Push new matches to ntfy."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _headers(token: str, title: str, click: str = "") -> dict[str, str]:
    headers = {
        "Title": title,
        "Tags": "briefcase",
        "Priority": "default",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if click:
        headers["Click"] = click
    return headers


def push(fetcher, base_url: str, topic: str, token: str, title: str, body: str,
         click: str = "") -> bool:
    url = f"{base_url.rstrip('/')}/{topic}"
    response = fetcher.post(
        url, body.encode("utf-8"), _headers(token, title, click)
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
