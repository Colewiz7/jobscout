import asyncio

import httpx
import pytest

from jobseer_bridge.config import Config
from jobseer_bridge.server import build_remote
from jwks_stub import JwksStub, make_key, make_token, public_jwk
from remote_harness import Running, post, rpc, session_run, settings_for

INIT = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})


@pytest.fixture(scope="module")
def key():
    return make_key()


@pytest.fixture
def stub(key):
    server = JwksStub()
    server.keys = [public_jwk(key, "k1")]
    yield server
    server.stop()


@pytest.fixture
def make_running(stub, dashboard, client):
    started = []

    def make(readonly=True, **extra):
        config = Config(url=dashboard.base, authentik_user="cole", readonly=readonly)
        server, _ = build_remote(config, settings_for(stub.url, **extra), client)
        running = Running(server)
        started.append(running)
        return running

    yield make
    for running in started:
        running.stop()


@pytest.fixture
def running(make_running):
    return make_running()


def test_no_token_gets_401_with_resource_metadata(running):
    reply = post(running, INIT)
    assert reply.status_code == 401
    challenge = reply.headers["www-authenticate"]
    assert challenge.startswith("Bearer") and "resource_metadata=" in challenge
    assert "/.well-known/oauth-protected-resource/mcp" in challenge
    document = httpx.get(running.base + "/.well-known/oauth-protected-resource/mcp", timeout=5).json()
    assert document["resource"] == "http://127.0.0.1:8000/mcp"
    assert document["authorization_servers"] == ["https://issuer.example.test/o/app/"]
    assert document["scopes_supported"] == ["bridge:use"]


@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
def test_bad_token_gets_401(running, token):
    assert post(running, INIT, token=token).status_code == 401


def test_token_for_wrong_subject_gets_401(running, key):
    assert post(running, INIT, token=make_token(key, sub="intruder")).status_code == 401


def test_missing_scope_gets_401(running, key):
    assert post(running, INIT, token=make_token(key, scope="openid")).status_code == 401


def test_key_set_down_gets_401(running, stub, key):
    stub.down = True
    assert post(running, INIT, token=make_token(key)).status_code == 401


def test_healthz_is_open_and_says_only_ok(running):
    reply = httpx.get(running.base + "/healthz", timeout=5)
    assert reply.status_code == 200 and reply.text == "ok"


def test_real_client_lists_and_calls_with_a_valid_token(running, key):
    async def work(session):
        listed = await session.list_tools()
        called = await session.call_tool("status", {})
        return [tool.name for tool in listed.tools], called

    names, called = session_run(running, make_token(key), work)
    assert "status" in names and "inbox_list" in names
    assert not called.isError and "target" in called.content[0].text


def test_read_only_is_the_default_so_writes_are_not_listed(running, key):
    async def work(session):
        return {tool.name for tool in (await session.list_tools()).tools}

    names = session_run(running, make_token(key), work)
    assert "job_set_status" not in names and "jobs_hide" not in names


def test_writes_need_both_read_write_mode_and_the_tool_list(make_running, key):
    enabled = make_running(readonly=False, JOBSEER_TOOLS="all")

    async def work(session):
        return {tool.name for tool in (await session.list_tools()).tools}

    assert "job_set_status" in session_run(enabled, make_token(key), work)


def test_wrong_host_is_rejected(running, key):
    reply = post(running, INIT, token=make_token(key), Host="evil.example.test")
    assert reply.status_code in {400, 421} and "result" not in reply.text


def test_wrong_origin_is_rejected(running, key):
    reply = post(running, INIT, token=make_token(key), Origin="https://evil.example.test")
    assert reply.status_code == 403


def test_allowed_origin_passes(running, key):
    reply = post(running, INIT, token=make_token(key), Origin="https://app.example.test")
    assert reply.status_code == 200


def test_oversized_body_is_refused(make_running, key):
    small = make_running(JOBSEER_MAX_BODY="1024")
    reply = post(small, rpc("tools/list", {"pad": "x" * 5000}), token=make_token(key))
    assert reply.status_code == 413
