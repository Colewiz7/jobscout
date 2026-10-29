from .client import BridgeError, key_path
from .common import need, slim
from .registry import tool


class OverviewTools:
    def _overview_path(self, key: str, tail: str = "") -> str:
        if not self.client.features().get("ai_overview"):
            raise BridgeError("AI overview is off on this server")
        return "/api/v1/jobs/%s/overview%s" % (key_path(need(key, "key")), tail)

    @tool("read", "Show the saved AI overview of a posting: status, items, revision and whether it was dismissed. Only reads what exists and never starts a generation; use overview_request for that.")
    def overview_get(self, key: str) -> dict:
        return self.client.get(self._overview_path(key), cached="1")

    @tool("write", "Ask for an AI overview of a posting. It runs on the server's model and may finish later; read it with overview_get.", routes=("/overview",))
    def overview_request(self, key: str) -> dict:
        return self.client.send("POST", self._overview_path(key))

    @tool("confirm", "Dismiss a posting's AI overview, or bring it back with dismissed=false. Two steps with confirm_id. A conflict means it was already in that state.", routes=("/overview/", "/dismiss"))
    def overview_dismiss(self, key: str, dismissed: bool = True, confirm_id: str | None = None) -> dict:
        path = self._overview_path(key, "/dismiss" if dismissed else "/restore")
        args = {"key": key, "dismissed": dismissed}
        held = self._gate("overview_dismiss", args, confirm_id, lambda: {"job": slim(self.client.job(key) or {"dedupe_key": key}), "action": "dismiss" if dismissed else "restore"})
        if held:
            return held
        return self.client.send("POST", path)

    @tool("write", "Throw away a posting's AI overview and generate a new one. The old one is kept so overview_regenerate_undo can bring it back.", routes=("/regenerate",))
    def overview_regenerate(self, key: str) -> dict:
        return self.client.send("POST", self._overview_path(key, "/regenerate"))

    @tool("write", "Bring back the AI overview from before the last regenerate. Pass the revision you last saw (from overview_get or the regenerate result); a conflict means it moved on.", routes=("/regenerate/undo",))
    def overview_regenerate_undo(self, key: str, expected_revision: int) -> dict:
        return self.client.send("POST", self._overview_path(key, "/regenerate/undo"), {"expected_revision": expected_revision})
