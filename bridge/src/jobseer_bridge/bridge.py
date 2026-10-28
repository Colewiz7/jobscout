import functools
import json

import httpx

from .client import ApiError, AuthError, BridgeError, Client, Conflict
from .config import Config
from .confirm import Confirms
from .identity import current_identity
from .ratelimit import RateLimiter
from .registry import annotations, collect
from .tools_apply import ApplyTools
from .tools_confirm import ConfirmTools
from .tools_profile import ProfileTools
from .tools_read import ReadTools
from .tools_records import RecordTools
from .tools_write import WriteTools


# duplicate copy doubles the size
def pack(payload) -> str:
    return json.dumps(payload, separators=(",", ":"), default=str)


class Bridge(ReadTools, WriteTools, ApplyTools, RecordTools, ConfirmTools, ProfileTools):
    def __init__(self, config: Config, client: Client | None = None, confirms: Confirms | None = None, *, selection="all", limiter: RateLimiter | None = None, remote: bool = False, audit=None, resolver=None):
        self.config = config
        self.confirms = confirms or Confirms()
        self.client = client or Client(config)
        self.selection = selection
        self.limiter = limiter
        self.remote = remote
        self.audit = audit
        self.resolver = resolver
        if isinstance(selection, frozenset):
            unknown = selection - {spec.name for spec, _ in collect(self)}
            if unknown:
                raise ValueError("JOBSEER_TOOLS names unknown tools: %s" % ", ".join(sorted(unknown)))

    # fail toward least privilege
    def allows(self, spec) -> bool:
        if self.config.readonly and spec.kind != "read":
            return False
        if self.selection == "read":
            return spec.kind == "read"
        return self.selection == "all" or spec.name in self.selection

    def tools(self) -> list:
        found = [(spec, fn) for spec, fn in collect(self) if self.allows(spec)]
        return sorted(found, key=lambda pair: pair[0].name)

    def caller(self):
        return current_identity() if self.remote else None

    def admit(self) -> None:
        if not self.remote:
            return
        who = current_identity()
        if who is None:
            raise BridgeError("not signed in")
        wait = self.limiter.hit(who[0]) if self.limiter else 0
        if wait:
            raise BridgeError("rate limit reached; try again in %d seconds" % wait)

    def label(self, data) -> dict:
        return {"target": self.config.origin, "data": data}

    def guard(self, fn, spec=None):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            who = current_identity() if self.remote else None
            subject = who[0] if who else None
            try:
                self.admit()
            except BridgeError:
                self.note(subject, spec, kwargs, "denied" if who is None else "rate_limited")
                raise
            try:
                text, data = self.invoke(fn, args, kwargs)
            except Exception:
                self.note(subject, spec, kwargs, "error", "rejected" if kwargs.get("confirm_id") else None)
                raise
            self.note(subject, spec, kwargs, "ok", self.outcome(spec, kwargs, data))
            return text

        run.__annotations__["return"] = str
        return run

    def note(self, subject, spec, kwargs, status, confirm=None) -> None:
        if self.audit and spec:
            self.audit.record(subject, spec.name, kwargs, status, confirm)

    def outcome(self, spec, kwargs, data):
        if spec is None or spec.kind != "confirm":
            return None
        if isinstance(data, dict) and data.get("needs_confirmation"):
            return "previewed"
        return "applied" if kwargs.get("confirm_id") else None

    def invoke(self, fn, args, kwargs):
        # keeps raw errors unreachable
        failure = None
        try:
            data = fn(*args, **kwargs)
            return pack(self.config.scrub(self.label(data))), data
        except Conflict as conflict:
            text = pack(self.config.scrub({"target": self.config.origin, "ok": False, "conflict": True, "message": conflict.message, "current": conflict.current}))
            return text, {"conflict": True}
        except AuthError as error:
            failure = "%s (on %s)" % (self.config.redact(error.message), self.config.origin)
        except ApiError as error:
            failure = "%s (HTTP %d) on %s" % (self.config.redact(error.message), error.status, self.config.origin)
        except httpx.TransportError as error:
            failure = "cannot reach %s: %s" % (self.config.origin, self.config.redact(str(error)))
        except BridgeError as error:
            failure = self.config.redact(str(error))
        except Exception:
            if not self.remote:
                raise
            failure = "unexpected error in the bridge"
        raise BridgeError(failure)

    def register(self, server) -> None:
        from mcp.types import ToolAnnotations

        for spec, fn in self.tools():
            server.add_tool(self.guard(fn, spec), name=spec.name, description=spec.description, annotations=ToolAnnotations(**annotations(spec)), structured_output=False)
