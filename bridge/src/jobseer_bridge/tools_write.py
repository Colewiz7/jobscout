from .client import BridgeError, key_path
from .common import BoardStatus, Status, drop_none, need
from .registry import tool

TAG_BULK = 10


class WriteTools:
    def _known(self, key: str) -> dict:
        job = self.client.job(key)
        if job is None:
            raise BridgeError("job not found: %s" % key)
        return job

    @tool("write", "Set a job's status and/or notes. Current values are sent as expected_* so a stale write comes back as a conflict with the live value.", routes=("/api/v1/jobs/",), idempotentHint=True)
    def job_set_status(self, key: str, status: Status | None = None, notes: str | None = None, expected_status: Status | None = None, expected_notes: str | None = None) -> dict:
        if status is None and notes is None:
            raise BridgeError("give a status or notes")
        job = self._known(key)
        body = drop_none({
            "status": status,
            "notes": notes,
            "expected_status": expected_status or job.get("status"),
            "expected_notes": (expected_notes if expected_notes is not None else job.get("notes") or "") if notes is not None else None,
        })
        return self.client.send("PATCH", "/api/v1/jobs/" + key_path(key), body)

    @tool("write", "Add or remove one tag on up to 500 jobs. More than 10 jobs takes two steps: repeat the call with the confirm_id from the preview.", routes=("/api/v1/job-tags",), idempotentHint=True)
    def jobs_tag(self, keys: list[str], tag: str, remove: bool = False, confirm_id: str | None = None) -> dict:
        need(keys, "keys")
        if len(keys) > TAG_BULK:
            args = {"keys": keys, "tag": tag, "remove": remove}
            held = self._gate("jobs_tag", args, confirm_id, lambda: {"tag": tag, "remove": remove, **self._brief(keys)})
            if held:
                return held
        return self.client.send("POST", "/api/v1/job-tags", {"dedupe_keys": keys, "tag": tag, "remove": remove})

    @tool("write", "Edit a posting's company, title, URL or pasted description text. Returns an edit id.", routes=("/posting",))
    def job_edit(self, key: str, company: str | None = None, title: str | None = None, url: str | None = None, description: str | None = None) -> dict:
        body = drop_none({"company": company, "title": title, "url": url, "description": description})
        if not body:
            raise BridgeError("give at least one field to change")
        reply = self.client.send("PATCH", "/api/v1/jobs/%s/posting" % key_path(key), body)
        text = (reply.get("description") or {}).get("description_text") or ""
        return {"edit_id": reply.get("edit_id"), "job": reply.get("job"), "description_chars": len(text)}

    @tool("write", "Reorder the apply queue. Must list every queued job exactly once.", routes=("/api/v1/queue/order",), idempotentHint=True)
    def queue_reorder(self, keys: list[str]) -> dict:
        return self.client.send("PUT", "/api/v1/queue/order", {"dedupe_keys": keys})

    @tool("write", "Reorder one tracker board column.", routes=("/api/v1/tracker/board-order",), idempotentHint=True)
    def board_reorder(self, status: BoardStatus, keys: list[str]) -> dict:
        return self.client.send("PUT", "/api/v1/tracker/board-order", {"status": status, "dedupe_keys": keys})
