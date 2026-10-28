import httpx
import pytest

from jobseer_bridge.client import ApiError, Client, Conflict
from jobseer_bridge.config import Config


def test_session_gives_token_and_features(client):
    session = client.session()
    assert session["user"] == "cole"
    assert session["features"]["quick_fill"] is True
    assert client.session() is session


def test_write_sends_cookie_token_and_origin(client, dashboard):
    reply = client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved", "expected_status": "new"})
    assert reply["status"] == "saved"
    assert dashboard.demo.jobs()[1]["status"] == "saved"


def test_missing_auth_is_a_clear_error(dashboard):
    bare = Client(Config(url=dashboard.base))
    with pytest.raises(ApiError) as caught:
        bare.session()
    assert caught.value.status == 401
    assert "Authentik" in caught.value.message


def test_rotated_token_refetches_session_once(client, dashboard):
    client.session()
    dashboard.csrf_token = "rotated-after-restart"
    reply = client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"})
    assert reply["ok"] is True
    assert client.session_fetches == 2


def test_persistent_403_does_not_loop():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"user": "x", "csrf_token": "t", "features": {}})
        return httpx.Response(403, json={"error": "Security token missing or expired. Reload and retry."})

    client = Client(Config(), transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError) as caught:
        client.send("POST", "/api/v1/hidden", {"dedupe_keys": ["a"]})
    assert caught.value.status == 403
    assert seen.count(("GET", "/api/v1/session")) == 2
    assert seen.count(("POST", "/api/v1/hidden")) == 2


def test_conflict_carries_current_value(client):
    client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved", "expected_status": "new"})
    with pytest.raises(Conflict) as caught:
        client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "archived", "expected_status": "new"})
    assert caught.value.status == 409
    assert caught.value.current == {"status": "saved"}


def test_other_errors_keep_status_and_message(client):
    with pytest.raises(ApiError) as caught:
        client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "bogus"})
    assert caught.value.status == 400
    assert not isinstance(caught.value, Conflict)


def test_jobs_are_cached_until_a_write(client, transport):
    first = client.jobs()
    assert client.jobs() is first
    assert transport.count("GET", "/api/v1/jobs") == 1
    client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"})
    assert {row["dedupe_key"]: row["status"] for row in client.jobs()}["demo:2"] == "saved"
    assert transport.count("GET", "/api/v1/jobs") == 2


def test_cache_expires_after_ttl(dashboard, transport):
    client = Client(Config(url=dashboard.base, authentik_user="cole", cache_ttl=0.0), transport=transport)
    client.jobs()
    client.jobs()
    assert transport.count("GET", "/api/v1/jobs") == 2


def test_job_lookup_checks_closed_rows_too(client):
    assert client.job("demo:1")["company"] == "Cloudflare"
    assert client.job("nope") is None


def test_keys_are_path_safe():
    from jobseer_bridge.client import key_path

    assert key_path("a/b c:1") == "a%2Fb%20c%3A1"


def test_cache_is_dropped_even_when_the_reply_is_lost(dashboard):
    import httpx

    class Lossy(httpx.HTTPTransport):
        lose = False

        def handle_request(self, request):
            reply = super().handle_request(request)
            if self.lose and request.method == "PATCH":
                reply.close()
                raise httpx.ReadError("reply lost", request=request)
            return reply

    transport = Lossy()
    client = Client(Config(url=dashboard.base, authentik_user="cole", cache_ttl=60.0), transport=transport)
    assert {row["dedupe_key"]: row["status"] for row in client.jobs()}["demo:2"] == "new"
    transport.lose = True
    with pytest.raises(httpx.ReadError):
        client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"})
    assert {row["dedupe_key"]: row["status"] for row in client.jobs()}["demo:2"] == "saved"


def test_cache_is_dropped_before_the_write_is_sent(client, transport):
    client.jobs()
    seen = []
    original = client._write
    client._write = lambda *args: (seen.append(dict(client._jobs)), original(*args))[1]
    client.send("PATCH", "/api/v1/jobs/demo:2", {"status": "saved"})
    assert seen == [{}]
