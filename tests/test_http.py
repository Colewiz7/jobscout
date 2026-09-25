"""The Fetcher's retry window and response handling, offline.

The retry behaviour is the interesting part of this module: a pod gets a
minute of EACCES while Cilium settles its identity, so connection failures
retry past that window before the run gives up.
"""
import time

import httpx
import pytest

from jobscout.http import Fetcher


class ScriptedClient:
    """Returns scripted responses or raises scripted exceptions, records calls."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _resp(status, body=b"", headers=None):
    return httpx.Response(
        status, content=body, headers=headers or {},
        request=httpx.Request("GET", "https://x.example/"),
    )


def _fetcher(script, retry_seconds=60.0):
    fetcher = Fetcher(retry_seconds=retry_seconds)
    fetcher._client = ScriptedClient(script)
    return fetcher


def test_success_returns_the_response():
    fetcher = _fetcher([_resp(200, b"ok")])
    response = fetcher.get("https://x.example/")
    assert response.status_code == 200
    assert fetcher._client.calls == [("GET", "https://x.example/", {})]


def test_4xx_is_definitive_and_not_retried():
    fetcher = _fetcher([_resp(404)])
    assert fetcher.get("https://x.example/") is None
    assert len(fetcher._client.calls) == 1


def test_retryable_status_is_retried_until_it_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    fetcher = _fetcher([_resp(503), _resp(200, b"ok")])
    response = fetcher.get("https://x.example/")
    assert response.status_code == 200
    assert len(fetcher._client.calls) == 2


def test_connection_error_is_retried_until_it_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    fetcher = _fetcher([httpx.ConnectError("EACCES"), _resp(200, b"ok")])
    assert fetcher.get("https://x.example/").status_code == 200
    assert len(fetcher._client.calls) == 2


def test_running_past_the_deadline_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    fetcher = _fetcher([_resp(503)], retry_seconds=0)
    with pytest.raises(RuntimeError, match="giving up on"):
        fetcher.get("https://x.example/")


def test_connection_error_past_the_deadline_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    fetcher = _fetcher([httpx.ConnectError("EACCES")], retry_seconds=0)
    with pytest.raises(RuntimeError, match="giving up on"):
        fetcher.get("https://x.example/")


def test_backoff_sequence_is_respected(monkeypatch):
    """Exhausting the script means the client pops from an empty list, proving
    the loop attempts exactly as many times as scripted responses allow."""
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    # Three 503s: attempt 0 sleeps BACKOFF[0]=2, attempt 1 sleeps BACKOFF[1]=4,
    # then attempt 2 gets a success.
    fetcher = _fetcher([_resp(503), _resp(503), _resp(200, b"ok")], retry_seconds=60)
    assert fetcher.get("https://x.example/").status_code == 200
    assert sleeps == [2, 4]


def test_get_text_returns_body_text():
    fetcher = _fetcher([_resp(200, b"hello")])
    assert fetcher.get_text("https://x.example/") == "hello"


def test_get_text_returns_none_on_definitive_failure():
    fetcher = _fetcher([_resp(404)])
    assert fetcher.get_text("https://x.example/") is None


def test_get_json_parses_the_body():
    fetcher = _fetcher([_resp(200, b'{"jobs": [1, 2]}')])
    assert fetcher.get_json("https://x.example/") == {"jobs": [1, 2]}


def test_get_json_returns_none_on_a_non_json_body(caplog):
    fetcher = _fetcher([_resp(200, b"<html>not json</html>")])
    assert fetcher.get_json("https://x.example/") is None
    assert "non-JSON body" in caplog.text


def test_post_sends_content_and_headers():
    fetcher = _fetcher([_resp(200)])
    response = fetcher.post(
        "https://ntfy.example/jobs",
        b"body",
        {"Title": "t", "Authorization": "Bearer x"},
    )
    assert response.status_code == 200
    method, url, kwargs = fetcher._client.calls[0]
    assert method == "POST"
    assert url == "https://ntfy.example/jobs"
    assert kwargs["content"] == b"body"
    assert kwargs["headers"]["Authorization"] == "Bearer x"