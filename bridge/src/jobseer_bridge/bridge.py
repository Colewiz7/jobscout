import functools
import json

import httpx

from .client import ApiError, BridgeError, Client, Conflict
from .config import Config
from .registry import annotations, collect
from .tools_apply import ApplyTools
from .tools_read import ReadTools
from .tools_records import RecordTools
from .tools_write import WriteTools


# compact text only, no duplicate structured copy
def pack(payload) -> str:
    return json.dumps(payload, separators=(",", ":"), default=str)


class Bridge(ReadTools, WriteTools, ApplyTools, RecordTools):
    def __init__(self, config: Config, client: Client | None = None):
        self.config = config
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
            try:
                return pack(self.label(fn(*args, **kwargs)))
            except Conflict as conflict:
                return pack({"target": self.config.origin, "ok": False, "conflict": True, "message": conflict.message, "current": conflict.current})
            except ApiError as error:
                raise BridgeError("%s (HTTP %d) on %s" % (error.message, error.status, self.config.origin)) from error
            except httpx.TransportError as error:
                raise BridgeError("cannot reach %s: %s" % (self.config.origin, error)) from error

        run.__annotations__["return"] = str
        return run

    def register(self, server) -> None:
        from mcp.types import ToolAnnotations

        for spec, fn in self.tools():
            server.add_tool(self.guard(fn), name=spec.name, description=spec.description, annotations=ToolAnnotations(**annotations(spec)), structured_output=False)
