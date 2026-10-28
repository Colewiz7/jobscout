import json

import httpx
import pytest

from jobseer_bridge.bridge import Bridge
from jobseer_bridge.client import ApiError, AuthError, BridgeError, Client
from jobseer_bridge.config import Config
from jobseer_bridge.server import build_remote
from jwks_stub import JwksStub, make_key, make_token, public_jwk
from remote_harness import Running, session_run, settings_for

APP_PW = "app-password-QQ-4417"


@pytest.fixture
def config():
    return Config(url="http://127.0.0.1:9", token=APP_PW)


def leaky(config):
    return "upstream said %s and %s" % (APP_PW, config.basic_value())


def clean(text, config):
    assert APP_PW not in text and config.basic_value() not in text


@pytest.mark.parametrize("remote", [False, True])
def test_every_error_path_is_scrubbed(config, remote):
    bridge = Bridge(config, client=object(), remote=False)
    bridge.remote = remote
    if remote:
        bridge.admit = lambda: None
    raisers = [
        AuthError(401, leaky(config)),
        ApiError(500, leaky(config)),
        BridgeError(leaky(config)),
        httpx.ConnectError(leaky(config)),
    ]
    for error in raisers:
        def boom(error=error):
            raise error

        with pytest.raises(BridgeError) as caught:
            bridge.guard(boom)()
        clean(str(caught.value), config)
        assert "[redacted]" in str(caught.value)


def test_unexpected_exceptions_are_opaque_in_remote_mode(config):
    bridge = Bridge(config, client=object(), remote=True)
    bridge.admit = lambda: None

    def boom():
        raise KeyError(leaky(config))

    with pytest.raises(BridgeError) as caught:
        bridge.guard(boom)()
    clean(str(caught.value), config)
    with pytest.raises(KeyError):
        Bridge(config, client=object()).guard(boom)()


def test_tool_error_over_http_hides_the_password(dashboard, monkeypatch):
    key = make_key()
    stub = JwksStub()
    stub.keys = [public_jwk(key, "k1")]
    settings_config = Config(url=dashboard.base, token=APP_PW)
    client = Client(settings_config)

    def refuse(self, refresh=False):
        raise AuthError(401, leaky(settings_config))

    monkeypatch.setattr(Client, "session", refuse)
    server, _ = build_remote(settings_config, settings_for(stub.url), client)
    running = Running(server)

    async def work(session):
        return await session.call_tool("status", {})

    try:
        reply = session_run(running, make_token(key), work)
    finally:
        running.stop()
        stub.stop()
        client.close()
    assert reply.isError
    clean(reply.content[0].text, settings_config)
    assert "[redacted]" in reply.content[0].text
