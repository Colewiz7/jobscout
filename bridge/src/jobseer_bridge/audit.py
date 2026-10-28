import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone


def digest(args: dict) -> str:
    body = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str, scrub=lambda text: text):
        self.scrub = scrub
        self._lock = threading.Lock()
        try:
            self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError:
            raise ValueError("JOBSEER_AUDIT_LOG could not be opened") from None

    # arguments may hold secrets
    def record(self, subject, tool: str, args: dict, status: str, confirm=None) -> None:
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "subject": self.scrub(subject) if subject else None,
            "tool": self.scrub(tool),
            "args_sha256": digest(args),
            "status": status,
            "confirm": confirm,
        }
        data = (json.dumps(line, separators=(",", ":")) + "\n").encode()
        try:
            with self._lock:
                os.write(self._fd, data)
        except OSError:
            sys.stderr.write("jobseer-bridge: warning: audit log write failed\n")

    def close(self) -> None:
        os.close(self._fd)
