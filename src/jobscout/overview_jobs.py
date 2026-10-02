"""Persistent AI overview work, with a quiet morning batch window."""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from . import db

log = logging.getLogger("jobscout.overview_jobs")
LOCAL_TIME = ZoneInfo("America/New_York")


def in_batch_window(now: dt.datetime | None = None) -> bool:
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(LOCAL_TIME)
    return 6 <= current.hour < 11


def process(dsn: str, service, *, limit: int = 20,
            priority_only: bool | None = None, now: dt.datetime | None = None) -> dict[str, int]:
    """Process a bounded batch. Buttons can run priority work at any hour."""
    from .dashboard import PostgresStore

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    only_priority = not in_batch_window(now) if priority_only is None else priority_only
    counts = {"ready": 0, "retry": 0, "failed": 0}
    store = PostgresStore(dsn)
    try:
        for _ in range(limit):
            with db.connect(dsn) as conn:
                db.require_schema(conn)
                key = db.claim_overview(conn, priority_only=only_priority)
            if key is None:
                break
            try:
                detail = store.description(key)
                sections = detail.get("sections") or [] if detail else []
                if not detail or not detail.get("description_text") or not sections:
                    with db.connect(dsn) as conn:
                        db.fail_overview(conn, key, "Posting description is unavailable", terminal=True)
                    counts["failed"] += 1
                    continue
                items = service.overview(sections)
                if not items:
                    with db.connect(dsn) as conn:
                        db.fail_overview(conn, key, "No source-checked facts were found", terminal=True)
                    counts["failed"] += 1
                    continue
                source = "posting" if service.is_fallback(sections) else "ai"
                with db.connect(dsn) as conn:
                    db.finish_overview(conn, key, items, source, service.fingerprint(sections))
                counts["ready"] += 1
            except Exception as error:
                log.exception("overview failed for %s", key)
                with db.connect(dsn) as conn:
                    db.fail_overview(conn, key, str(error))
                    row = db.overview_job(conn, key)
                counts["failed" if row and row["status"] == "failed" else "retry"] += 1
    finally:
        store.close()
    return counts
