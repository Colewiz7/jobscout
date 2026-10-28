import pytest

from jobseer_bridge.config import DEFAULT_URL, Config


def test_defaults_point_at_the_local_demo():
    config = Config.from_env({})
    assert config.url == DEFAULT_URL
    assert config.readonly is False
    assert config.auth_headers() == {}


def test_url_is_normalised_and_origin_has_no_path():
    config = Config.from_env({"JOBSEER_URL": "http://localhost:9000/"})
    assert config.url == "http://localhost:9000"
    assert config.origin == "http://localhost:9000"


@pytest.mark.parametrize("value", ["ftp://x", "localhost:80", "http://"])
def test_bad_url_is_rejected(value):
    with pytest.raises(ValueError):
        Config.from_env({"JOBSEER_URL": value})


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("0", False), ("", False)])
def test_readonly_flag(value, expected):
    assert Config.from_env({"JOBSEER_READONLY": value}).readonly is expected


def test_auth_headers_only_when_user_is_set():
    config = Config.from_env({"JOBSEER_AUTHENTIK_USER": "cole"})
    assert config.auth_headers() == {
        "X-authentik-username": "cole",
        "X-authentik-meta-app": "jobseer",
        "X-authentik-meta-outpost": "local",
    }


@pytest.mark.parametrize("value", ["http://user:pw@host:80", "http://user@host", "http://host?token=abc", "http://host/#frag", "http://host/?"])
def test_credentials_query_and_fragment_are_rejected(value):
    with pytest.raises(ValueError) as caught:
        Config.from_env({"JOBSEER_URL": value})
    assert "pw" not in str(caught.value) and "abc" not in str(caught.value)


def test_direct_construction_is_validated_too():
    with pytest.raises(ValueError):
        Config(url="http://user:pw@host")


def test_origin_drops_the_path():
    assert Config(url="http://127.0.0.1:9/base/path").origin == "http://127.0.0.1:9"
