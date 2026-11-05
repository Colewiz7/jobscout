import pytest

from jobseer_bridge.bridge import Bridge
from jobseer_bridge.config import Config
from jobseer_bridge.ratelimit import RateLimiter
from jobseer_bridge.remote import parse_tools
from jobseer_bridge.server import build_remote
from jwks_stub import JwksStub, make_key, make_token, public_jwk
from remote_harness import Running, session_run, settings_for


def names(selection, readonly=False):
    bridge = Bridge(Config(readonly=readonly), client=object(), selection=selection)
    return {spec.name for spec, _ in bridge.tools()}


def test_default_selection_in_serve_settings_is_read():
    assert parse_tools(None) == "read" and parse_tools("  ") == "read"
    assert parse_tools("ALL") == "all"
    assert parse_tools("job_get, status") == frozenset({"job_get", "status"})


def test_read_selection_lists_only_read_tools():
    found = names("read")
    assert "status" in found and "job_set_status" not in found and "jobs_hide" not in found


def test_all_selection_lists_everything_unless_readonly():
    assert "jobs_hide" in names("all")
    assert "jobs_hide" not in names("all", readonly=True)


def test_explicit_list_picks_exactly_those():
    assert names(frozenset({"status", "job_set_status"})) == {"status", "job_set_status"}


def test_stricter_of_readonly_and_list_wins():
    assert names(frozenset({"status", "job_set_status"}), readonly=True) == {"status"}


def test_unknown_names_refuse_to_start():
    with pytest.raises(ValueError) as caught:
        Bridge(Config(), client=object(), selection=frozenset({"status", "nope"}))
    assert "nope" in str(caught.value)


def test_limiter_slides_per_subject():
    now = [0.0]
    limiter = RateLimiter(2, 10, clock=lambda: now[0])
    assert limiter.hit("a") == 0 and limiter.hit("a") == 0
    assert limiter.hit("a") > 0
    assert limiter.hit("b") == 0
    now[0] = 10.5
    assert limiter.hit("a") == 0


def test_bridge_returns_a_clear_error_when_limited(monkeypatch):
    from jobseer_bridge import bridge as module
    from jobseer_bridge.client import BridgeError

    monkeypatch.setattr(module, "current_identity", lambda: ("someone", "client"))
    bridge = Bridge(Config(), client=object(), limiter=RateLimiter(1, 60), remote=True)
    run = bridge.guard(lambda: {"fine": True})
    assert "fine" in run()
    with pytest.raises(BridgeError) as caught:
        run()
    assert "rate limit" in str(caught.value)


def test_remote_calls_without_an_identity_are_refused():
    from jobseer_bridge.client import BridgeError

    bridge = Bridge(Config(), client=object(), remote=True)
    with pytest.raises(BridgeError):
        bridge.guard(lambda: {})()


@pytest.fixture(scope="module")
def key():
    return make_key()


@pytest.fixture
def stub(key):
    server = JwksStub()
    server.keys = [public_jwk(key, "k1")]
    yield server
    server.stop()


def serve(stub, dashboard, client, readonly=True, **extra):
    config = Config(url=dashboard.base, authentik_user="cole", readonly=readonly)
    server, _ = build_remote(config, settings_for(stub.url, **extra), client)
    return Running(server)


def test_served_list_follows_the_env(stub, dashboard, client, key):
    running = serve(stub, dashboard, client, readonly=False, JOBSEER_TOOLS="status,job_set_status")

    async def work(session):
        return {tool.name for tool in (await session.list_tools()).tools}

    try:
        assert session_run(running, make_token(key), work) == {"status", "job_set_status"}
    finally:
        running.stop()


def test_served_calls_hit_the_rate_limit(stub, dashboard, client, key):
    running = serve(stub, dashboard, client, JOBSEER_RATE_LIMIT="2", JOBSEER_RATE_WINDOW="60")

    async def work(session):
        return [await session.call_tool("status", {}) for _ in range(3)]

    try:
        first, second, third = session_run(running, make_token(key), work)
    finally:
        running.stop()
    assert not first.isError and not second.isError
    assert third.isError and "rate limit" in third.content[0].text


def test_all_selection_exposes_every_registered_tool():
    from jobseer_bridge.registry import collect

    bridge = Bridge(Config(), client=object(), selection="all")
    every = {spec.name for spec, _ in collect(bridge)}
    assert names("all") == every
    assert len(every) == 53
