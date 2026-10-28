import functools
import json

import httpx

from .client import ApiError, AuthError, BridgeError, Client, Conflict
from .config import Config
from .confirm import Confirms
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
    def __init__(self, config: Config, client: Client | None = None, confirms: Confirms | None = None):
        self.config = config
        self.confirms = confirms or Confirms()
        self.client = client or Client(config)

    def tools(self) -> list:
        found = collect(self)
        if self.config.readonly:
            found = [(spec, fn) for spec, fn in found if spec.kind == "read"]
        return sorted(found, key=lambda pair: pair[0].name)

    def label(self, data) -> dict:
        return {"target": self.config.origin, "data": data}

    def guard(self, fn):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            # raising outside keeps the raw error unreachable
            failure = None
            try:
                return pack(self.config.scrub(self.label(fn(*args, **kwargs))))
            except Conflict as conflict:
                return pack(self.config.scrub({"target": self.config.origin, "ok": False, "conflict": True, "message": conflict.message, "current": conflict.current}))
            except AuthError as error:
                failure = "%s (on %s)" % (error.message, self.config.origin)
            except ApiError as error:
                failure = "%s (HTTP %d) on %s" % (self.config.redact(error.message), error.status, self.config.origin)
            except httpx.TransportError as error:
                failure = "cannot reach %s: %s" % (self.config.origin, self.config.redact(str(error)))
            raise BridgeError(failure)

        run.__annotations__["return"] = str
        return run

    def register(self, server) -> None:
        from mcp.types import ToolAnnotations

        for spec, fn in self.tools():
            server.add_tool(self.guard(fn), name=spec.name, description=spec.description, annotations=ToolAnnotations(**annotations(spec)), structured_output=False)
