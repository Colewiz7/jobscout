import httpx
import pytest

from jobscout import http
from jobscout.http import Fetcher


def fetcher_with(handler):
    fetcher = Fetcher()
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler))
    return fetcher


@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(http.time, "sleep", waited.append)
    return waited


def test_a_429_gets_one_short_retry_then_gives_up(sleeps):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    with pytest.raises(RuntimeError) as raised:
        fetcher_with(handler).get("https://acme.example/jobs")
    assert len(calls) == 2
    assert sleeps == [http.RATE_LIMIT_DEFAULT_WAIT]
    # liveness reads the 429 off the cause to word its message
    assert raised.value.__cause__.response.status_code == 429


def test_retry_after_is_honored_but_capped(sleeps):
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "3600"})

    with pytest.raises(RuntimeError):
        fetcher_with(handler).get("https://acme.example/jobs")
    assert sleeps == [http.RATE_LIMIT_MAX_WAIT]


def test_a_429_that_clears_on_retry_succeeds(sleeps):
    answers = iter([httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(200, json={"ok": 1})])
    response = fetcher_with(lambda request: next(answers)).get("https://acme.example/jobs")
    assert response.json() == {"ok": 1}
    assert sleeps == [2]


def test_server_errors_still_use_the_long_backoff(sleeps):
    answers = iter([httpx.Response(503), httpx.Response(503), httpx.Response(200, json={})])
    fetcher_with(lambda request: next(answers)).get("https://acme.example/jobs")
    assert sleeps == [http.BACKOFF[0], http.BACKOFF[1]]
