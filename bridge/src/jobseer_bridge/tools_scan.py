from .client import BridgeError
from .registry import tool

PATH = "/api/v1/scan-runs"


class ScanTools:
    def _scan_overview(self) -> dict:
        if not self.client.features().get("scan_run"):
            raise BridgeError("manual scans are off on this server")
        return self.client.get(PATH)

    @tool("read", "Show recent scout runs (when, ok or failed, new postings, notifications) and whether a scan is running now. Read only; use scan_run_start to start one.")
    def scan_status(self) -> dict:
        return self._scan_overview()

    @tool("confirm", "Start a scan of every job source now. It takes about 20 minutes and may send notifications. Two steps with confirm_id: the preview shows the current status and is refused while a scan is running or one started in the last 15 minutes.", routes=(PATH,))
    def scan_run_start(self, confirm_id: str | None = None) -> dict:
        def preview():
            status = self._scan_overview()
            if status.get("running"):
                raise BridgeError("a scan is already running")
            if status.get("cooldown_until"):
                raise BridgeError("a scan started recently; try again after %s" % status["cooldown_until"])
            return {
                "will_do": "start a scan of all job sources; it takes about 20 minutes and may send notifications",
                "current_status": status.get("runs", [])[:1],
                "next_scheduled_at": status.get("next_scheduled_at"),
            }

        held = self._gate("scan_run_start", {}, confirm_id, preview)
        if held:
            return held
        if not self.client.features().get("scan_run"):
            raise BridgeError("manual scans are off on this server")
        return self.client.send("POST", PATH)
