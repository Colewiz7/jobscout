from typing import Literal

from .client import BridgeError, key_path
from .common import drop_none, need
from .registry import tool


class ApplyTools:
    def _revision(self, record_id: int, *buckets: str) -> int:
        data = self.client.get("/api/v1/tracker")
        for bucket in buckets:
            for row in data.get(bucket, []):
                if row.get("id") == record_id:
                    return row["revision"]
        raise BridgeError("record %d not found" % record_id)

    @tool("write", "Drive an apply session. start: begin with the queue or listed keys. update: record applied or skipped jobs or end the session. check: live-check one posting. mark_applied: record the application with a resume document id. next_step: set a job's next step.", routes=("/api/v1/queue/session", "/api/v1/queue/session/", "/api/v1/queue/check", "/api/v1/applications/", "/applied", "/next-step"), openWorldHint=True)
    def apply_session(self, action: Literal["start", "update", "check", "mark_applied", "next_step"], key: str | None = None, keys: list[str] | None = None, session_id: int | None = None, applied: list[str] | None = None, skipped: list[dict] | None = None, ended: bool | None = None, expected_revision: int | None = None, document_id: str | None = None, next_step: str | None = None, expected_next_step: str | None = None) -> dict:
        if action == "start":
            return self.client.send("POST", "/api/v1/queue/session", drop_none({"dedupe_keys": keys}))
        if action == "update":
            if session_id is None or expected_revision is None:
                current = self.client.get("/api/v1/queue/session")["session"]
                if current is None:
                    raise BridgeError("no apply session to update")
                session_id = current["id"] if session_id is None else session_id
                expected_revision = current["revision"] if expected_revision is None else expected_revision
            body = drop_none({"applied": applied, "skipped": skipped, "ended": ended, "expected_revision": expected_revision})
            return self.client.send("PATCH", "/api/v1/queue/session/%d" % session_id, body)
        need(key, "key")
        if action == "check":
            return self.client.send("POST", "/api/v1/queue/check", {"dedupe_key": key})
        if action == "mark_applied":
            return self.client.send("POST", "/api/v1/applications/%s/applied" % key_path(key), {"document_id": document_id})
        if next_step is None:
            raise BridgeError("next_step is required")
        if expected_next_step is None:
            expected_next_step = (self.client.job(key) or {}).get("next_step") or ""
        body = {"next_step": next_step, "expected_next_step": expected_next_step}
        return self.client.send("PUT", "/api/v1/applications/%s/next-step" % key_path(key), body)

    @tool("write", "Create an interview (key and starts_at) or change one (id). Changes send the interview's current revision unless you pass expected_revision. Use cancelled to cancel.", routes=("/api/v1/interviews", "/api/v1/interviews/"))
    def interview_save(self, id: int | None = None, key: str | None = None, starts_at: str | None = None, ends_at: str | None = None, location: str | None = None, notes: str | None = None, cancelled: bool | None = None, expected_revision: int | None = None) -> dict:
        if id is None:
            need(key, "key")
            need(starts_at, "starts_at")
            return self.client.send("POST", "/api/v1/interviews", drop_none({"dedupe_key": key, "starts_at": starts_at, "ends_at": ends_at, "location": location, "notes": notes}))
        if expected_revision is None:
            expected_revision = self._revision(id, "interviews")
        body = drop_none({"starts_at": starts_at, "ends_at": ends_at, "location": location, "notes": notes, "cancelled": cancelled, "expected_revision": expected_revision})
        return self.client.send("PATCH", "/api/v1/interviews/%d" % id, body)

    @tool("write", "Create a follow-up (key, due_at, note) or act on one (id with action done, reopen, snooze or unsnooze). Actions send the current revision unless you pass expected_revision.", routes=("/api/v1/reminders", "/api/v1/reminders/"))
    def reminder_save(self, id: int | None = None, key: str | None = None, due_at: str | None = None, note: str | None = None, action: Literal["done", "reopen", "snooze", "unsnooze"] | None = None, until: str | None = None, expected_revision: int | None = None) -> dict:
        if id is None:
            need(key, "key")
            need(due_at, "due_at")
            return self.client.send("POST", "/api/v1/reminders", drop_none({"dedupe_key": key, "due_at": due_at, "note": note}))
        need(action, "action")
        if expected_revision is None:
            expected_revision = self._revision(id, "reminders", "done_reminders")
        return self.client.send("PUT", "/api/v1/reminders/%d" % id, drop_none({"action": action, "until": until, "expected_revision": expected_revision}))
