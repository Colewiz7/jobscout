import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    # callers need a retry delay
    def hit(self, subject: str) -> int:
        with self._lock:
            now = self.clock()
            seen = self._hits.setdefault(subject, deque())
            while seen and seen[0] <= now - self.window:
                seen.popleft()
            if len(seen) >= self.limit:
                return max(1, int(seen[0] + self.window - now) + 1)
            seen.append(now)
            return 0
