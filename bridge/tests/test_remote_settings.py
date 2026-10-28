import pytest

from jobseer_bridge.config import Config
from jobseer_bridge.remote import REQUIRED, RemoteSettings

GOOD = {
    "JOBSEER_AUTH_ISSUER": "https://issuer.example.test/o/app/",
    "JOBSEER_AUTH_JWKS_URL": "https://issuer.example.test/o/app/jwks/",
    "JOBSEER_AUTH_AUDIENCE": "client-abc",
    "JOBSEER_AUTH_SUBJECT": "someone",
    "JOBSEER_PUBLIC_URL": "https://mcp.example.test/mcp",
    "JOBSEER_ALLOWED_HOSTS": "mcp.example.test",
    "JOBSEER_ALLOWED_ORIGINS": "https://app.example.test, https://other.example.test",
}


def test_good_settings_and_defaults():
    settings = RemoteSettings.from_env(GOOD)
    assert settings.host == "127.0.0.1" and settings.port == 8000
    assert settings.allowed_origins == ("https://app.example.test", "https://other.example.test")
    assert settings.scope == "jobseer:mcp"


@pytest.mark.parametrize("name", list(REQUIRED))
@pytest.mark.parametrize("blank", [None, "", "  "])
def test_each_required_value_is_enforced(name, blank):
    env = {**GOOD, name: blank} if blank is not None else {key: value for key, value in GOOD.items() if key != name}
    with pytest.raises(ValueError) as caught:
        RemoteSettings.from_env(env)
    assert name in str(caught.value)


@pytest.mark.parametrize("name,value", [
    ("JOBSEER_AUTH_ISSUER", "http://issuer.example.test/"),
    ("JOBSEER_AUTH_JWKS_URL", "http://issuer.example.test/jwks"),
    ("JOBSEER_PUBLIC_URL", "http://mcp.example.test/mcp"),
    ("JOBSEER_PUBLIC_URL", "https://mcp.example.test/other"),
    ("JOBSEER_AUTH_ISSUER", "ftp://issuer.example.test/"),
    ("JOBSEER_AUTH_JWKS_URL", "https://user:pw@issuer.example.test/jwks"),
    ("JOBSEER_BIND_PORT", "0"),
    ("JOBSEER_BIND_PORT", "abc"),
])
def test_unsafe_values_are_refused(name, value):
    with pytest.raises(ValueError):
        RemoteSettings.from_env({**GOOD, name: value})


def test_loopback_http_is_allowed():
    env = {**GOOD, "JOBSEER_AUTH_ISSUER": "http://127.0.0.1:9000/", "JOBSEER_AUTH_JWKS_URL": "http://localhost:9000/jwks"}
    assert RemoteSettings.from_env(env)


def test_serve_defaults_to_read_only_until_told_otherwise():
    assert Config.from_env({}, readonly_default=True).readonly is True
    assert Config.from_env({"JOBSEER_READONLY": "0"}, readonly_default=True).readonly is False
    assert Config.from_env({"JOBSEER_READONLY": "maybe"}, readonly_default=True).readonly is True
    assert Config.from_env({}).readonly is False


def test_serve_command_refuses_to_start_unconfigured(monkeypatch):
    from jobseer_bridge.__main__ import main

    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as caught:
        main(["serve"])
    assert "JOBSEER_ALLOWED_HOSTS" in str(caught.value)


BAD_HOSTS = ["*", "*.example.test", "mcp.*", "mcp.example.test:*", ":*", "https://mcp.example.test", "mcp.example.test/", "mcp.example.test/x",
             "user@mcp.example.test", "mcp example.test", "mcp.example.test:0", "mcp.example.test:99999", "mcp.example.test:abc", "-bad.example.test",
             "mcp.example.test,,other.example.test", "mcp.example.test,", ",mcp.example.test", "[::1", "[zz]:80"]
BAD_ORIGINS = ["*", "https://*", "https://*.example.test", "https://app.example.test:*", "app.example.test", "//app.example.test", "https://", "https://app.example.test/",
               "https://app.example.test/path", "https://app.example.test?x=1", "https://u@app.example.test", "https://app.example.test:0",
               "ftp://app.example.test", "http://app.example.test", "https://app.example.test,,https://b.example.test", "https://a.example.test,", "null"]


@pytest.mark.parametrize("value", BAD_HOSTS)
def test_host_entries_must_be_exact(value):
    with pytest.raises(ValueError):
        RemoteSettings.from_env({**GOOD, "JOBSEER_ALLOWED_HOSTS": value})


@pytest.mark.parametrize("value", BAD_ORIGINS)
def test_origin_entries_must_be_exact_https(value):
    with pytest.raises(ValueError):
        RemoteSettings.from_env({**GOOD, "JOBSEER_ALLOWED_ORIGINS": value})


@pytest.mark.parametrize("value", ["mcp.example.test", "mcp.example.test:8443", "127.0.0.1:8000", "[::1]:8000", "a.example.test, b.example.test:9"])
def test_exact_hosts_are_accepted(value):
    assert RemoteSettings.from_env({**GOOD, "JOBSEER_ALLOWED_HOSTS": value}).allowed_hosts


@pytest.mark.parametrize("value", ["https://app.example.test", "https://app.example.test:8443", "https://a.example.test, https://b.example.test"])
def test_exact_origins_are_accepted(value):
    assert RemoteSettings.from_env({**GOOD, "JOBSEER_ALLOWED_ORIGINS": value}).allowed_origins


def test_loopback_http_origin_needs_a_loopback_bind():
    env = {**GOOD, "JOBSEER_ALLOWED_ORIGINS": "http://localhost:3000"}
    assert RemoteSettings.from_env(env).allowed_origins == ("http://localhost:3000",)
    with pytest.raises(ValueError):
        RemoteSettings.from_env({**env, "JOBSEER_BIND_HOST": "0.0.0.0"})
    with pytest.raises(ValueError):
        RemoteSettings.from_env({**GOOD, "JOBSEER_ALLOWED_ORIGINS": "http://app.example.test"})


@pytest.mark.parametrize("name", ["JOBSEER_ALLOWED_HOSTS", "JOBSEER_ALLOWED_ORIGINS"])
def test_empty_lists_are_refused(name):
    with pytest.raises(ValueError):
        RemoteSettings.from_env({**GOOD, name: " , "})
