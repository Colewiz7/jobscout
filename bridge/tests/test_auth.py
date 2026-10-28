import base64

import httpx
import pytest
from stubs import FAKE_BASIC, FAKE_TOKEN, denier

from jobseer_bridge import __version__
from jobseer_bridge.bridge import Bridge
from jobseer_bridge.client import ApiError, AuthError, Client
from jobseer_bridge.config import Config, run_token_cmd
from jobseer_bridge.server import build

from conftest import Reply, drive

URL = "https://jobs.example.test"


def cfg(**kwargs):
    return Config(url=kwargs.pop("url", URL), token=kwargs.pop("token", FAKE_TOKEN), **kwargs)


# --- header ---


def test_basic_header_is_built_from_user_and_token():
    headers = cfg().auth_headers()
    assert headers == {"Authorization": "Basic " + FAKE_BASIC}
    assert base64.b64decode(FAKE_BASIC).decode() == "cole:" + FAKE_TOKEN


def test_user_override_changes_the_header():
    headers = Config.from_env({"JOBSEER_URL": URL, "JOBSEER_TOKEN": "t0k", "JOBSEER_USER": "kai"}).auth_headers()
    assert base64.b64decode(headers["Authorization"].split()[1]) == b"kai:t0k"


def test_no_header_without_a_token():
    assert Config(url=URL).auth_headers() == {}
    assert "Authorization" not in Config.from_env({"JOBSEER_TOKEN": "  "}).request_headers()


def test_user_agent_is_distinct():
    assert Config(url=URL).request_headers()["User-Agent"] == "jobseer-bridge/" + __version__


def test_token_wins_over_header_mode():
    config = cfg(authentik_user="cole")
    headers = config.request_headers()
    assert "Authorization" in headers
    assert not [key for key in headers if key.lower().startswith("x-authentik")]


def test_header_mode_still_works_without_a_token():
    headers = Config(authentik_user="cole").auth_headers()
    assert headers["X-authentik-username"] == "cole"


@pytest.mark.parametrize("bad", ["has space", "tab\there", "café"])
def test_odd_tokens_are_rejected_without_echo(bad):
    with pytest.raises(ValueError) as caught:
        cfg(token=bad)
    assert bad not in str(caught.value)


def test_user_with_a_colon_is_rejected():
    with pytest.raises(ValueError):
        cfg(user="a:b")


def test_userinfo_is_still_rejected_with_a_token():
    with pytest.raises(ValueError) as caught:
        Config(url="https://cole:" + FAKE_TOKEN + "@jobs.example.test", token=FAKE_TOKEN)
    assert FAKE_TOKEN not in str(caught.value)


# --- transport rules ---


@pytest.mark.parametrize("url", ["http://jobs.example.test", "http://192.168.0.5:8080", "http://10.0.0.2"])
def test_plain_http_to_a_remote_host_is_refused_with_a_token(url):
    with pytest.raises(ValueError) as caught:
        cfg(url=url)
    assert FAKE_TOKEN not in str(caught.value)
    assert "https" in str(caught.value)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080", "http://localhost:8080", "http://[::1]:8080", "https://jobs.example.test"])
def test_loopback_and_https_are_accepted_with_a_token(url):
    assert cfg(url=url).token == FAKE_TOKEN


def test_plain_http_is_fine_without_a_token():
    assert Config(url="http://jobs.example.test").token is None


# --- redaction ---


def test_repr_and_str_hide_the_token():
    config = cfg()
    for text in (repr(config), str(config), "%s" % config, "%r" % [config]):
        assert FAKE_TOKEN not in text and FAKE_BASIC not in text
    assert "redacted" in repr(config)


def test_redact_scrubs_both_forms():
    config = cfg()
    text = config.redact("x %s y %s" % (FAKE_TOKEN, FAKE_BASIC))
    assert FAKE_TOKEN not in text and FAKE_BASIC not in text


def test_target_label_has_no_token():
    bridge = Bridge(cfg(), client=object())
    assert FAKE_TOKEN not in str(bridge.label({}))
    assert bridge.label({})["target"] == URL


def test_transport_errors_are_scrubbed():
    def handler(request):
        raise httpx.ConnectError("boom %s" % FAKE_TOKEN, request=request)

    config = cfg()
    client = Client(config, transport=httpx.MockTransport(handler))
    server, _ = build(config, client)
    reply = Reply(drive(server, "status", {}))
    assert reply.error
    assert FAKE_TOKEN not in reply.text and "boom" in reply.text


# --- token command ---


def test_token_cmd_success_strips_the_newline():
    assert run_token_cmd("printf '%s\\n' abc123") == "abc123"


def test_token_cmd_is_used_by_from_env_but_explicit_token_wins():
    seen = []

    def runner(command):
        seen.append(command)
        return "from-cmd"

    env = {"JOBSEER_URL": URL, "JOBSEER_TOKEN_CMD": "anything"}
    assert Config.from_env(env, runner=runner).token == "from-cmd"
    assert Config.from_env({**env, "JOBSEER_TOKEN": "direct"}, runner=runner).token == "direct"
    assert seen == ["anything"]


def test_token_cmd_runs_without_a_shell():
    assert run_token_cmd("echo a;b $HOME") == "a;b $HOME"


def test_token_cmd_failure_does_not_echo_stderr():
    with pytest.raises(ValueError) as caught:
        run_token_cmd("sh -c 'echo " + FAKE_TOKEN + " >&2; exit 3'")
    assert "exit code 3" in str(caught.value)
    clean_chain(caught.value)


def test_token_cmd_timeout():
    with pytest.raises(ValueError) as caught:
        run_token_cmd("sleep 5", timeout=0.2)
    assert "timed out" in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("command,text", [("true", "printed nothing"), ("definitely-not-a-command-zz", "could not be started"), ("'unbalanced", "valid command")])
def test_token_cmd_other_failures(command, text):
    with pytest.raises(ValueError) as caught:
        run_token_cmd(command)
    assert text in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


# --- gate ---


def test_handshake_and_write_work_through_the_gate(proxy, dashboard):
    client = Client(Config(url=proxy.base, token=FAKE_TOKEN))
    assert client.session()["user"] == "cole"
    reply = client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved", "expected_status": "new"})
    assert reply["status"] == "saved"
    assert dashboard.demo.jobs()[1]["status"] == "saved"
    write = [seen for seen in proxy.seen if seen["method"] == "PATCH"][0]
    assert write["headers"]["Authorization"] == "Basic " + FAKE_BASIC
    assert write["headers"]["Origin"] == proxy.base
    assert write["headers"]["User-Agent"] == "jobseer-bridge/" + __version__
    assert "jobseer_csrf=" in write["headers"]["Cookie"]
    client.close()


def test_every_request_carries_the_header(proxy):
    client = Client(Config(url=proxy.base, token=FAKE_TOKEN))
    client.jobs()
    client.session()
    assert proxy.seen and all(seen["headers"].get("Authorization") == "Basic " + FAKE_BASIC for seen in proxy.seen)
    client.close()


def test_wrong_token_is_a_clear_auth_error_through_the_gate(proxy):
    client = Client(Config(url=proxy.base, token="other-fake-token"))
    with pytest.raises(AuthError) as caught:
        client.session()
    assert caught.value.status == 401
    assert "check token and that header authentication is enabled" in caught.value.message
    assert "HTTP 401" in caught.value.message
    client.close()


def test_rotated_csrf_still_refetches_once_through_the_gate(proxy, dashboard):
    client = Client(Config(url=proxy.base, token=FAKE_TOKEN))
    client.session()
    dashboard.csrf_token = "rotated"
    assert client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"})["ok"] is True
    assert client.session_fetches == 2
    client.close()


# --- denials ---


def test_401_is_one_request_and_never_retried():
    stub = denier(401)
    try:
        client = Client(Config(url=stub.base, token=FAKE_TOKEN))
        with pytest.raises(AuthError) as caught:
            client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"})
        assert len(stub.seen) == 1
        assert FAKE_TOKEN not in caught.value.message
        assert "html" not in caught.value.message
        client.close()
    finally:
        stub.stop()


def test_cloudflare_style_403_is_an_auth_error_without_retry():
    stub = denier(403, {"Server": "cloudflare", "CF-RAY": "abc-IAD", "cf-mitigated": "challenge"})
    try:
        client = Client(Config(url=stub.base, token=FAKE_TOKEN))
        with pytest.raises(AuthError) as caught:
            client.session()
        assert caught.value.status == 403
        assert "Cloudflare" in caught.value.message
        assert len(stub.seen) == 1
        client.close()
    finally:
        stub.stop()


def test_non_json_403_on_a_write_does_not_trigger_the_csrf_retry():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"user": "x", "csrf_token": "t", "features": {}})
        return httpx.Response(403, text="<html>blocked</html>")

    client = Client(Config(), transport=httpx.MockTransport(handler))
    with pytest.raises(AuthError):
        client.send("POST", "/api/v1/hidden", {"dedupe_keys": ["a"]})
    assert seen == [("GET", "/api/v1/session"), ("POST", "/api/v1/hidden")]


def test_redirect_to_sign_in_is_an_auth_error():
    stub = denier(302, {"Location": "https://auth.example.test/login?next=" + FAKE_TOKEN})
    try:
        client = Client(Config(url=stub.base, token=FAKE_TOKEN))
        with pytest.raises(AuthError) as caught:
            client.session()
        assert FAKE_TOKEN not in caught.value.message and "auth.example" not in caught.value.message
        assert len(stub.seen) == 1
        client.close()
    finally:
        stub.stop()


def test_tool_result_for_a_denial_has_no_token_or_body():
    stub = denier(401)
    try:
        config = Config(url=stub.base, token=FAKE_TOKEN)
        server, _ = build(config, Client(config))
        reply = Reply(drive(server, "status", {}))
        assert reply.error
        assert FAKE_TOKEN not in reply.text
        assert "check token and that header authentication is enabled" in reply.text
        assert "HTTP 401" in reply.text
        assert len(stub.seen) == 1
    finally:
        stub.stop()


# --- echoed secrets ---

CSRF = "Security token missing or expired. Reload and retry."
ECHO = "echo %s and %s" % (FAKE_TOKEN, FAKE_BASIC)


def echoing(status, body):
    def handler(request):
        if request.url.path == "/api/v1/session" and status != 400:
            return httpx.Response(200, json={"user": "x", "csrf_token": "t", "features": {}})
        return httpx.Response(status, json=body)

    return handler


def chain_text(error):
    import traceback

    return "".join(traceback.format_exception(error)) + str(error) + repr(error)


def no_secrets(text):
    assert FAKE_TOKEN not in text and FAKE_BASIC not in text


def walk(error):
    seen = []
    while error is not None and error not in seen:
        seen.append(error)
        error = error.__cause__ or error.__context__
    return seen


def clean_chain(error):
    assert error.__context__ is None and error.__cause__ is None
    for item in walk(error):
        no_secrets(repr(vars(item)) + repr(item.args) + chain_text(item))


def guarded(status, body, call):
    config = cfg()
    client = Client(config, transport=httpx.MockTransport(echoing(status, body)))
    bridge = Bridge(config, client)
    return bridge.guard(lambda: call(client))


@pytest.mark.parametrize("status", [400, 404, 422, 500])
def test_echoed_error_bodies_do_not_reach_exceptions(status):
    run = guarded(status, {"error": ECHO, "detail": ECHO}, lambda client: client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"}))
    with pytest.raises(Exception) as caught:
        run()
    clean_chain(caught.value)
    assert "redacted" in str(caught.value)


def test_client_errors_that_escape_hold_only_scrubbed_bodies():
    config = cfg()
    client = Client(config, transport=httpx.MockTransport(echoing(400, {"error": ECHO, "detail": [ECHO]})))
    with pytest.raises(ApiError) as caught:
        client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"})
    clean_chain(caught.value)


@pytest.mark.parametrize("status,headers", [(401, {}), (403, {"cf-mitigated": "challenge"})])
def test_denials_leave_no_chain(status, headers):
    def handler(request):
        return httpx.Response(status, headers=headers, text=ECHO)

    config = cfg()
    bridge = Bridge(config, Client(config, transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception) as caught:
        bridge.guard(lambda: bridge.client.session())()
    clean_chain(caught.value)


def test_transport_failures_leave_no_chain():
    def handler(request):
        raise httpx.ConnectError("down " + ECHO, request=request)

    config = cfg()
    bridge = Bridge(config, Client(config, transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception) as caught:
        bridge.guard(lambda: bridge.client.session())()
    clean_chain(caught.value)
    assert "down" in str(caught.value)


def test_echoed_conflict_is_scrubbed_in_the_result():
    run = guarded(409, {"error": ECHO, "status": ECHO, ECHO: 1}, lambda client: client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"}))
    text = run()
    no_secrets(text)
    assert '"conflict":true' in text


def test_echoed_data_is_scrubbed_in_results():
    config = cfg()
    bridge = Bridge(config, client=object())
    no_secrets(bridge.guard(lambda: {"rows": [{"note": ECHO}], "n": 1})())


def test_echoed_error_body_never_reaches_a_tool_result():
    config = cfg()
    client = Client(config, transport=httpx.MockTransport(echoing(400, {"error": ECHO})))
    server, _ = build(config, client)
    reply = Reply(drive(server, "status", {}))
    assert reply.error
    no_secrets(reply.text)


# --- csrf versus proxy 403 ---


def forbidden_flow(body):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"user": "x", "csrf_token": "t", "features": {}})
        return httpx.Response(403, json=body)

    client = Client(Config(), transport=httpx.MockTransport(handler))
    return client, seen


@pytest.mark.parametrize("body", [{"error": "forbidden"}, {"error": "Forbidden by policy"}, {"message": CSRF}, {}])
def test_generic_json_403_is_never_retried(body):
    client, seen = forbidden_flow(body)
    with pytest.raises(AuthError):
        client.send("POST", "/api/v1/hidden", {"dedupe_keys": ["a"]})
    assert seen == [("GET", "/api/v1/session"), ("POST", "/api/v1/hidden")]


def test_real_csrf_403_gets_one_refetch_and_one_retry():
    client, seen = forbidden_flow({"error": CSRF})
    with pytest.raises(ApiError) as caught:
        client.send("POST", "/api/v1/hidden", {"dedupe_keys": ["a"]})
    assert not isinstance(caught.value, AuthError)
    assert seen == [("GET", "/api/v1/session"), ("POST", "/api/v1/hidden"), ("GET", "/api/v1/session"), ("POST", "/api/v1/hidden")]
