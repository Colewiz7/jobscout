import io

import httpx
import pytest
from stubs import FAKE_BASIC, FAKE_TOKEN, denier

from jobseer_bridge import __main__ as entry
from jobseer_bridge.check import run_check
from jobseer_bridge.client import Client
from jobseer_bridge.config import Config


def run(config, client=None):
    out = io.StringIO()
    code = run_check(config, client, out)
    return code, out.getvalue()


def test_check_succeeds_through_the_gate_with_one_call(proxy):
    config = Config(url=proxy.base, token=FAKE_TOKEN)
    code, text = run(config)
    assert code == 0
    assert "target: " + proxy.base in text
    assert "http status: 200" in text
    assert "auth accepted: yes" in text
    assert "session user: cole" in text
    assert [(seen["method"], seen["path"]) for seen in proxy.seen] == [("GET", "/api/v1/session")]
    assert FAKE_TOKEN not in text and FAKE_BASIC not in text


def test_check_reports_a_denial_without_the_token_or_body():
    stub = denier(403, {"Server": "cloudflare", "CF-RAY": "x"})
    try:
        code, text = run(Config(url=stub.base, token=FAKE_TOKEN))
        assert code == 1
        assert "http status: 403" in text
        assert "auth accepted: no" in text
        assert "Cloudflare" in text
        assert FAKE_TOKEN not in text and FAKE_BASIC not in text
        assert len(stub.seen) == 1
    finally:
        stub.stop()


def test_check_with_a_wrong_token_fails(proxy):
    code, text = run(Config(url=proxy.base, token="other-fake-token"))
    assert code == 1
    assert "http status: 401" in text and "auth accepted: no" in text
    assert "session user" not in text


def test_check_works_in_header_mode_on_the_demo(config):
    code, text = run(config)
    assert code == 0
    assert "authentik headers" in text and "session user: cole" in text


def test_check_scrubs_transport_errors():
    def handler(request):
        raise httpx.ConnectError("down " + FAKE_TOKEN, request=request)

    config = Config(url="https://jobs.example.test", token=FAKE_TOKEN)
    code, text = run(config, Client(config, transport=httpx.MockTransport(handler)))
    assert code == 1
    assert "cannot reach" in text and FAKE_TOKEN not in text


def test_check_flags_a_non_session_reply():
    config = Config(url="https://jobs.example.test", token=FAKE_TOKEN)
    client = Client(config, transport=httpx.MockTransport(lambda request: httpx.Response(200, text="hello")))
    code, text = run(config, client)
    assert code == 1
    assert "not a dashboard session" in text


def test_main_check_exits_with_the_result(proxy, monkeypatch, capsys):
    monkeypatch.setenv("JOBSEER_URL", proxy.base)
    monkeypatch.setenv("JOBSEER_TOKEN", FAKE_TOKEN)
    with pytest.raises(SystemExit) as caught:
        entry.main(["check"])
    assert caught.value.code == 0
    out = capsys.readouterr()
    assert "session user: cole" in out.out
    assert FAKE_TOKEN not in out.out + out.err


def test_main_rejects_unknown_arguments():
    with pytest.raises(SystemExit) as caught:
        entry.main(["bogus"])
    assert "usage" in str(caught.value.code)


def test_main_config_errors_do_not_echo_the_token(monkeypatch):
    monkeypatch.setenv("JOBSEER_URL", "http://jobs.example.test")
    monkeypatch.setenv("JOBSEER_TOKEN", FAKE_TOKEN)
    with pytest.raises(SystemExit) as caught:
        entry.main(["check"])
    assert FAKE_TOKEN not in str(caught.value.code) and "https" in str(caught.value.code)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
