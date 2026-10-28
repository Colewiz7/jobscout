import hashlib
import json
import secrets
import time

from .client import BridgeError

TTL = 120.0
MAX_PENDING = 50


def fingerprint(tool: str, args: dict) -> str:
    body = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


class Confirms:
    def __init__(self, ttl: float = TTL, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self._pending: dict[str, tuple[float, str]] = {}

    def issue(self, tool: str, args: dict) -> str:
        now = self.clock()
        self._pending = {key: held for key, held in self._pending.items() if held[0] > now}
        if len(self._pending) >= MAX_PENDING:
            self._pending.pop(min(self._pending, key=lambda key: self._pending[key][0]))
        confirm_id = secrets.token_urlsafe(6)
        self._pending[confirm_id] = (now + self.ttl, fingerprint(tool, args))
        return confirm_id

    # single use, and only for the exact call that was previewed
    def redeem(self, confirm_id: str, tool: str, args: dict) -> None:
        held = self._pending.pop(confirm_id, None)
        if held is None:
            raise BridgeError("unknown or already used confirm_id; ask for a new preview")
        if held[0] <= self.clock():
            raise BridgeError("confirm_id expired; ask for a new preview")
        if held[1] != fingerprint(tool, args):
            raise BridgeError("confirm_id belongs to different arguments; ask for a new preview")
