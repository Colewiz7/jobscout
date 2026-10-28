import asyncio
import socket
import threading
import time

import httpx
import pytest

from jobseer_bridge.remote import RemoteSettings
from jwks_stub import AUDIENCE, ISSUER, SUBJECT


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def settings_for(jwks_url, **extra):
    port = free_port()
    env = {
        "JOBSEER_BIND_PORT": str(port),
        "JOBSEER_AUTH_ISSUER": ISSUER,
        "JOBSEER_AUTH_JWKS_URL": jwks_url,
        "JOBSEER_AUTH_AUDIENCE": AUDIENCE,
        "JOBSEER_AUTH_SUBJECT": SUBJECT,
        "JOBSEER_AUTH_SCOPE": "bridge:use",
        "JOBSEER_PUBLIC_URL": "http://127.0.0.1:8000/mcp",
        "JOBSEER_ALLOWED_HOSTS": "127.0.0.1:%d" % port,
        "JOBSEER_ALLOWED_ORIGINS": "https://app.example.test",
    }
    env.update(extra)
    return RemoteSettings.from_env(env)


class Running:
    def __init__(self, server):
        import uvicorn

        self.http = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=server.settings.port, log_level="error"))
        self.thread = threading.Thread(target=self.http.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.http.started:
                break
            time.sleep(0.02)
        else:
            pytest.skip("the test sandbox does not permit a loopback listener")
        self.port = self.http.servers[0].sockets[0].getsockname()[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self.url = self.base + "/mcp"

    def stop(self):
        self.http.should_exit = True
        self.thread.join(timeout=5)


def rpc(method, params=None, ident=1):
    return {"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}


HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def post(running, body, token=None, **headers):
    sent = {**HEADERS, **headers}
    if token:
        sent["Authorization"] = "Bearer " + token
    return httpx.post(running.url, json=body, headers=sent, timeout=5)


def session_run(running, token, work):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=10) as http:
            async with streamable_http_client(running.url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await work(session)

    return asyncio.run(go())
