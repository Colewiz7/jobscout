from .client import BridgeError

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
