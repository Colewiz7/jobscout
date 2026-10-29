import hashlib
from typing import Literal

from .client import BridgeError, Conflict, key_path
from .common import need, slim
from .registry import tool


class UndoTools:
    @tool("write", "Undo a tag change with the ids from the jobs_tag result. Say what you are undoing: an add or a remove. A conflict means another tab already changed those tags.", routes=("/api/v1/job-tags/undo",))
    def jobs_tag_undo(self, ids: list[int], undo: Literal["add", "remove"]) -> dict:
        need(ids, "ids")
        # the route names the new state
        return self.client.send("POST", "/api/v1/job-tags/undo", {"ids": ids, "remove": undo == "add"})

    @tool("write", "Undo a posting edit with the edit_id that job_edit returned. A conflict means the posting changed since.", routes=("/api/v1/posting-edits/",))
    def job_edit_undo(self, edit_id: int) -> dict:
        reply = self.client.send("POST", "/api/v1/posting-edits/%d/undo" % edit_id)
        text = (reply.get("description") or {}).get("description_text") or ""
        return {"job": reply.get("job"), "description_chars": len(text)}

    @tool("write", "Bring back a job a rule archived. Use the rule_action_id shown on the job from job_get.", routes=("/api/v1/rule-actions/",))
    def rule_action_undo(self, action_id: int) -> dict:
        return self.client.send("POST", "/api/v1/rule-actions/%d/undo" % action_id)

    @tool("write", "Hide an activity entry from the tracker feed, or bring it back with dismissed=false. Entry ids come from tracker_get history. Applied dates still count it.", routes=("/api/v1/activity/", "/dismiss", "/restore"), idempotentHint=True)
    def activity_dismiss(self, id: int, dismissed: bool = True) -> dict:
        return self.client.send("POST", "/api/v1/activity/%d/%s" % (id, "dismiss" if dismissed else "restore"))

    @tool("write", "Record that a quick-fill value was copied for a job, so the profile panel shows it as done. Needs quick-fill on.", routes=("/api/v1/profile/copy",), idempotentHint=True)
    def profile_copy_mark(self, job: str, target: str) -> dict:
        if not self.client.features().get("quick_fill"):
            raise BridgeError("quick-fill is off on this server")
        return self.client.send("POST", "/api/v1/profile/copy", {"dedupe_key": job, "target_key": need(target, "target")})

    def _saved_description(self, key: str) -> dict:
        return self.client.get("/api/v1/jobs/%s/description" % key_path(key))["description"]

    # lets confirm spot a changed description
    @staticmethod
    def _description_marker(detail: dict) -> str:
        text = detail.get("description_text") or ""
        return hashlib.sha256(text.encode()).hexdigest()

    @tool("confirm", "Save pasted posting text as a job's description, for pages the server cannot fetch. It replaces any description already saved; the preview shows what is there now and the save is refused as a conflict if that changed after the preview. Reading the current text may make the server fetch the posting. Two steps with confirm_id. job_edit with description also keeps an undo id.", routes=("/description",), openWorldHint=True)
    def job_description_paste(self, key: str, text: str, confirm_id: str | None = None) -> dict:
        need(key, "key")
        need(text, "text")
        args = {"key": key, "text": text}
        current = self._saved_description(key)
        before = current.get("description_text") or ""
        if confirm_id is None:
            shown = {"job": slim(self.client.job(key) or {"dedupe_key": key}), "current_chars": len(before), "current_starts": before[:200], "chars": len(text), "starts": text[:200]}
            return {
                "needs_confirmation": True,
                "confirm_id": self.confirms.issue("job_description_paste", args, self._description_marker(current), owner=self.caller()),
                "expires_in": int(self.confirms.ttl),
                "preview": shown,
                "next": "call job_description_paste again with the same arguments plus confirm_id",
            }
        seen = self.confirms.redeem(confirm_id, "job_description_paste", args, owner=self.caller())
        if seen != self._description_marker(current):
            raise Conflict(409, "The description changed after the preview.", {"error": "The description changed after the preview.", "current_chars": len(before), "current_starts": before[:200]})
        reply = self.client.send("POST", "/api/v1/jobs/%s/description" % key_path(key), {"text": text})
        saved = reply.get("description") or {}
        return {"description_chars": len(saved.get("description_text") or ""), "pay": saved.get("pay")}
