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


def test_token_file_is_read_and_stripped(tmp_path):
    path = tmp_path / "token"
    path.write_text("  file-secret-1\n")
    path.chmod(0o600)
    config = Config.from_env({"JOBSEER_TOKEN_FILE": str(path)})
    assert config.token == "file-secret-1"
    assert "file-secret-1" not in repr(config)


def test_token_precedence_is_value_then_file_then_command(tmp_path):
    path = tmp_path / "token"
    path.write_text("from-file")
    path.chmod(0o600)
    ran = []

    def runner(command):
        ran.append(command)
        return "from-cmd"

    both = {"JOBSEER_TOKEN_FILE": str(path), "JOBSEER_TOKEN_CMD": "x"}
    assert Config.from_env(both, runner=runner).token == "from-file"
    assert Config.from_env({**both, "JOBSEER_TOKEN": "from-env"}, runner=runner).token == "from-env"
    assert Config.from_env({"JOBSEER_TOKEN_CMD": "x"}, runner=runner).token == "from-cmd"
    assert ran == ["x"]


def test_token_file_warns_when_others_can_read(tmp_path, capsys):
    path = tmp_path / "token"
    path.write_text("loose-secret")
    path.chmod(0o644)
    assert Config.from_env({"JOBSEER_TOKEN_FILE": str(path)}).token == "loose-secret"
    seen = capsys.readouterr()
    assert "readable by other users" in seen.err
    assert "loose-secret" not in seen.err + seen.out


def test_strict_mode_file_is_silent(tmp_path, capsys):
    path = tmp_path / "token"
    path.write_text("quiet-secret")
    path.chmod(0o400)
    Config.from_env({"JOBSEER_TOKEN_FILE": str(path)})
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("content", [b"", b"  \n", b"\xff\xfe\x00"])
def test_bad_token_file_content_is_rejected(tmp_path, content):
    path = tmp_path / "token"
    path.write_bytes(content)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        Config.from_env({"JOBSEER_TOKEN_FILE": str(path)})


def test_missing_token_file_error_does_not_echo_the_path_content(tmp_path):
    with pytest.raises(ValueError) as caught:
        Config.from_env({"JOBSEER_TOKEN_FILE": str(tmp_path / "absent")})
    assert "could not be read" in str(caught.value)
