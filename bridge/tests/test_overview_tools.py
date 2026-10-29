import threading

import pytest

from jobscout.dashboard import DashboardServer, DemoStore
from jobseer_bridge.client import Client
from jobseer_bridge.config import Config
from jobseer_bridge.server import build

from conftest import Reply, drive


class Service:
    def __init__(self):
        self.generated = 0

    def overview(self, sections):
        self.generated += 1
        return [{"kind": "role", "text": sections[0]["text"]}]

    def cached_overview(self, sections):
        return [{"kind": "role", "text": sections[0]["text"]}]

    def forget(self, sections):
        pass


def serve(overviews, readonly=False):
    try:
        server = DashboardServer(("127.0.0.1", 0), DemoStore(), require_auth=True, authentik_user="cole", overviews=overviews)
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    config = Config(url="http://127.0.0.1:%d" % server.server_address[1], authentik_user="cole", readonly=readonly)
    client = Client(config)
    return server, client, build(config, client)[0]


@pytest.fixture
def ai():
    service = Service()
    server, client, mcp = serve(service)
    run = lambda name, **args: Reply(drive(mcp, name, args))
    run.service = service
    run.tools = lambda: {tool.name: tool for tool in drive(mcp, None, {}).tools}
    yield run
    client.close()
    server.shutdown()
    server.server_close()


def test_overview_get_has_no_generate_option_and_never_generates(ai):
    tool = ai.tools()["overview_get"]
    assert set(tool.inputSchema["properties"]) == {"key"}
    assert tool.annotations.readOnlyHint is True
    assert ai("overview_get", key="demo:1").data["items"]
    assert ai.service.generated == 0
    ai("overview_request", key="demo:1")
    assert ai.service.generated == 1


def test_overview_get_reads_items(ai):
    shown = ai("overview_get", key="demo:1").data
    assert shown["items"] and shown["dismissed"] is False


def test_overview_request_returns_a_ready_overview(ai):
    done = ai("overview_request", key="demo:1").data
    assert done["status"] == "ready" and done["items"]


def dismiss(ai, key="demo:1", **args):
    held = ai("overview_dismiss", key=key, **args).data
    return ai("overview_dismiss", key=key, confirm_id=held["confirm_id"], **args)


def test_overview_dismiss_previews_before_acting(ai):
    held = ai("overview_dismiss", key="demo:1").data
    assert held["needs_confirmation"] is True and held["preview"]["action"] == "dismiss"
    assert ai("overview_get", key="demo:1").data["dismissed"] is False


def test_overview_dismiss_and_restore(ai):
    assert dismiss(ai).data["dismissed"] is True
    assert ai("overview_get", key="demo:1").data["dismissed"] is True
    assert dismiss(ai, dismissed=False).data["dismissed"] is False
    assert ai("overview_get", key="demo:1").data["dismissed"] is False


def test_overview_dismiss_confirm_is_bound_and_single_use(ai):
    held = ai("overview_dismiss", key="demo:1").data
    wrong = ai("overview_dismiss", key="demo:2", confirm_id=held["confirm_id"])
    assert wrong.error and "different arguments" in wrong.text
    flipped = ai("overview_dismiss", key="demo:1", dismissed=False, confirm_id=held["confirm_id"])
    assert flipped.error
    held = ai("overview_dismiss", key="demo:1").data
    assert ai("overview_dismiss", key="demo:1", confirm_id=held["confirm_id"]).data["dismissed"] is True
    again = ai("overview_dismiss", key="demo:1", confirm_id=held["confirm_id"])
    assert again.error and "confirm_id" in again.text
    assert ai("overview_dismiss", key="demo:1", confirm_id="nonsense").error


def test_overview_dismiss_twice_is_a_conflict(ai):
    dismiss(ai)
    again = dismiss(ai)
    assert not again.error and again.body["conflict"] is True


def test_overview_regenerate_clears_a_dismissal(ai):
    dismiss(ai)
    again = ai("overview_regenerate", key="demo:1").data
    assert again["status"] == "ready" and again["dismissed"] is False


class Stub:
    def __init__(self, fail=None):
        self.fail = fail
        self.reads = 0

    def features(self):
        return {"ai_overview": True}

    def get(self, path, **params):
        self.reads += 1
        return {"revision": 4}

    def send(self, method, path, body=None):
        if self.fail:
            raise self.fail
        self.sent = (method, path, body)
        return {"ok": True}


def test_regenerate_undo_sends_exactly_the_given_revision():
    from jobseer_bridge.bridge import Bridge

    stub = Stub()
    Bridge(Config(), client=stub).overview_regenerate_undo("demo:1", expected_revision=2)
    assert stub.sent == ("POST", "/api/v1/jobs/demo%3A1/overview/regenerate/undo", {"expected_revision": 2})
    assert stub.reads == 0


def test_regenerate_undo_requires_the_revision(ai):
    reply = ai("overview_regenerate_undo", key="demo:1")
    assert reply.error and "expected_revision" in reply.text


def test_regenerate_undo_stale_revision_is_a_conflict():
    from jobseer_bridge.bridge import Bridge
    from jobseer_bridge.client import Conflict

    bridge = Bridge(Config(url="http://x"), client=Stub(fail=Conflict(409, "changed", {"error": "changed", "revision": 5})))
    out = bridge.guard(bridge.overview_regenerate_undo)(key="demo:1", expected_revision=4)
    assert '"conflict":true' in out


def test_regenerate_undo_without_history_is_an_error(ai):
    assert ai("overview_regenerate_undo", key="demo:1", expected_revision=1).error


def test_unknown_job_is_an_error(ai):
    assert ai("overview_get", key="nope").error
    assert dismiss(ai, key="nope").error


def test_overview_tools_say_when_the_feature_is_off(call):
    for name in ("overview_get", "overview_request", "overview_dismiss", "overview_regenerate", "overview_regenerate_undo"):
        extra = {"expected_revision": 1} if name == "overview_regenerate_undo" else {}
        reply = call(name, key="demo:1", **extra)
        assert reply.error and "off on this server" in reply.text


def test_overview_writes_are_hidden_in_readonly_mode():
    server, client, mcp = serve(Service(), readonly=True)
    try:
        names = {tool.name for tool in drive(mcp, None, {}).tools}
    finally:
        client.close()
        server.shutdown()
        server.server_close()
    assert "overview_get" in names
    assert not names & {"overview_request", "overview_dismiss", "overview_regenerate", "overview_regenerate_undo"}
