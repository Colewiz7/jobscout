import json
import threading

import httpx
import pytest

from jobscout.dashboard import DashboardServer, DemoStore
from jobseer_bridge.client import Client
from jobseer_bridge.config import Config


class Counting(httpx.HTTPTransport):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def handle_request(self, request):
        self.calls.append((request.method, request.url.path))
        return super().handle_request(request)

    def count(self, method: str, path: str) -> int:
        return self.calls.count((method, path))


@pytest.fixture
def dashboard():
    store = DemoStore()
    try:
        server = DashboardServer(("127.0.0.1", 0), store, require_auth=True, authentik_user="cole", quick_fill_enabled=True)
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    server.base = "http://127.0.0.1:%d" % server.server_address[1]
    server.demo = store
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def config(dashboard):
    return Config(url=dashboard.base, authentik_user="cole", cache_ttl=30.0)


@pytest.fixture
def transport():
    return Counting()


@pytest.fixture
def client(config, transport):
    client = Client(config, transport=transport)
    yield client
    client.close()


@pytest.fixture
def app(config, client):
    from jobseer_bridge.server import build

    return build(config, client)


def drive(server, name, args):
    import asyncio

    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as session:
            if name is None:
                return await session.list_tools()
            return await session.call_tool(name, args)

    return asyncio.run(go())


class Reply:
    def __init__(self, raw):
        self.raw = raw
        self.error = raw.isError
        self.text = "".join(getattr(part, "text", "") for part in raw.content)
        try:
            self.body = json.loads(self.text)
        except ValueError:
            self.body = {}

    @property
    def data(self):
        return self.body.get("data")


@pytest.fixture
def call(app):
    server, _ = app
    return lambda tool_name, **args: Reply(drive(server, tool_name, args))


@pytest.fixture
def listing(app):
    server, _ = app
    return lambda: drive(server, None, {}).tools


@pytest.fixture
def proxy(dashboard):
    from stubs import gate

    stub = gate(dashboard)
    yield stub
    stub.stop()
