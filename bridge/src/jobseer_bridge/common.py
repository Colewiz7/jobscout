from typing import Literal

from .client import BridgeError

Status = Literal["new", "saved", "queued", "applying", "applied", "interviewing", "offer", "rejected", "archived"]
BoardStatus = Literal["applying", "applied", "interviewing", "offer", "rejected"]

SLIM = ("dedupe_key", "company", "title", "location", "status", "score", "tags", "age_days", "closed", "deadline", "next_step", "url")


def slim(job: dict) -> dict:
    return {key: job.get(key) for key in SLIM}


def need(value, what: str):
    if value is None or value == "":
        raise BridgeError("%s is required" % what)
    return value


def clip(items: list, limit: int, offset: int = 0) -> list:
    limit = max(1, min(int(limit), 100))
    return items[max(0, int(offset)):][:limit]


def drop_none(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not None}
