import json
import os
import stat

import pytest

from jobseer_bridge import bridge as module
from jobseer_bridge.audit import AuditLog, digest
from jobseer_bridge.bridge import Bridge
from jobseer_bridge.client import BridgeError
from jobseer_bridge.config import Config
from jobseer_bridge.registry import Spec
from jobseer_bridge.server import build_remote
from jwks_stub import JwksStub, make_key, make_token, public_jwk
from remote_harness import Running, session_run, settings_for

APP_PW = "app-password-ZZ-9981"


def lines(path):
    return [json.loads(row) for row in path.read_text().splitlines()]


def test_record_has_hash_and_no_arguments(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(str(path))
    log.record("someone", "job_get", {"key": "plain-secret-value"}, "ok", "applied")
    log.close()
    row = lines(path)[0]
    assert set(row) == {"ts", "subject", "tool", "args_sha256", "status", "confirm"}
    assert row["args_sha256"] == digest({"key": "plain-secret-value"})
    assert row["confirm"] == "applied"
    assert "plain-secret-value" not in path.read_text()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_unopenable_path_refuses_to_start(tmp_path):
    with pytest.raises(ValueError):
        AuditLog(str(tmp_path / "missing" / "audit.jsonl"))


def test_scrub_removes_secrets_from_text_fields(tmp_path):
    config = Config(url="http://127.0.0.1:9", token=APP_PW)
    path = tmp_path / "audit.jsonl"
    log = AuditLog(str(path), config.scrub)
    log.record(APP_PW, "tool-" + config.basic_value(), {}, "ok")
    log.close()
    assert APP_PW not in path.read_text() and config.basic_value() not in path.read_text()


def test_bridge_audits_every_outcome(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    who = [("someone", "c1")]
    monkeypatch.setattr(module, "current_identity", lambda: who[0])
    bridge = Bridge(Config(), client=object(), remote=True, audit=AuditLog(str(path)))
    spec = Spec("demo", "confirm", "d")
    bridge.guard(lambda **kw: {"needs_confirmation": True}, spec)(a=1)
    bridge.guard(lambda **kw: {"done": True}, spec)(a=1, confirm_id="x")

    def boom(**kw):
        raise BridgeError("nope")

    with pytest.raises(BridgeError):
        bridge.guard(boom, spec)(a=1, confirm_id="x")
    who[0] = None
    with pytest.raises(BridgeError):
        bridge.guard(boom, spec)(a=1)
    rows = lines(path)
    assert [(row["status"], row["confirm"]) for row in rows] == [("ok", "previewed"), ("ok", "applied"), ("error", "rejected"), ("denied", None)]
    assert rows[0]["subject"] == "someone" and rows[3]["subject"] is None


@pytest.fixture(scope="module")
def key():
    return make_key()


def test_token_and_app_password_never_reach_the_file(tmp_path, dashboard, client, key):
    path = tmp_path / "audit.jsonl"
    stub = JwksStub()
    stub.keys = [public_jwk(key, "k1")]
    token = make_token(key)
    config = Config(url=dashboard.base, authentik_user="cole", token=None)
    server, _ = build_remote(config, settings_for(stub.url, JOBSEER_TOOLS="all", JOBSEER_AUDIT_LOG=str(path)), client)
    running = Running(server)
    first = client.jobs(True)[0]["dedupe_key"]

    async def work(session):
        await session.call_tool("job_get", {"key": token})
        await session.call_tool("job_get", {"key": APP_PW})
        preview = await session.call_tool("jobs_hide", {"keys": [first]})
        confirm_id = json.loads(preview.content[0].text)["data"]["confirm_id"]
        await session.call_tool("jobs_hide", {"keys": [first], "confirm_id": confirm_id})
        await session.call_tool("status", {})

    try:
        session_run(running, token, work)
    finally:
        running.stop()
        stub.stop()
    text = path.read_text()
    assert token not in text and token.split(".")[1] not in text and APP_PW not in text
    assert "Authorization" not in text and "Bearer" not in text
    rows = lines(path)
    assert [row["tool"] for row in rows] == ["job_get", "job_get", "jobs_hide", "jobs_hide", "status"]
    assert [row["confirm"] for row in rows[2:4]] == ["previewed", "applied"]
    assert {row["subject"] for row in rows} == {"someone"}
    assert all(set(row) == {"ts", "subject", "tool", "args_sha256", "status", "confirm"} for row in rows)
