import threading

import pytest

from jobscout import scan_runs
from jobscout.dashboard import DashboardServer, DemoStore
from jobseer_bridge.client import Client
from jobseer_bridge.config import Config
from jobseer_bridge.server import build

from conftest import Reply, drive


@pytest.fixture(params=["on", "off"])
def scan(request):
    store = DemoStore()
    launcher = scan_runs.FakeLauncher(store, duration=3600) if request.param == "on" else None
    try:
        server = DashboardServer(("127.0.0.1", 0), store, require_auth=True, authentik_user="cole", scan_launcher=launcher)
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    config = Config(url="http://127.0.0.1:%d" % server.server_address[1], authentik_user="cole")
    client = Client(config)
    mcp = build(config, client)[0]
    run = lambda name, **args: Reply(drive(mcp, name, args))
    run.store = store
    run.base = config.url
    run.tools = lambda: drive(mcp, None, {}).tools
    run.enabled = request.param == "on"
    yield run
    client.close()
    server.shutdown()
    server.server_close()


@pytest.fixture
def enabled(scan):
    if not scan.enabled:
        pytest.skip("needs the feature on")
    return scan


def test_status_is_read_only_and_needs_the_feature(scan):
    reply = scan("scan_status")
    if scan.enabled:
        assert reply.data["running"] is False and reply.data["runs"] == []
    else:
        assert reply.error and "manual scans are off" in reply.text


def test_a_start_previews_before_it_does_anything(enabled):
    held = enabled("scan_run_start").data
    assert held["needs_confirmation"] is True
    assert "20 minutes" in held["preview"]["will_do"]
    assert enabled("scan_status").data["running"] is False
    assert enabled.store.scout_runs() == []


def test_a_confirmed_start_runs_once(enabled):
    held = enabled("scan_run_start").data
    done = enabled("scan_run_start", confirm_id=held["confirm_id"])
    assert not done.error and done.data["run"]["trigger"] == "manual"
    assert enabled("scan_status").data["running"] is True
    assert len(enabled.store.scout_runs()) == 1
    again = enabled("scan_run_start", confirm_id=held["confirm_id"])
    assert again.error and "confirm_id" in again.text


def test_the_preview_is_refused_while_a_scan_runs(enabled):
    enabled("scan_run_start", confirm_id=enabled("scan_run_start").data["confirm_id"])
    refused = enabled("scan_run_start")
    assert refused.error and "already running" in refused.text


def test_a_confirm_id_cannot_be_used_for_another_tool(enabled):
    held = enabled("scan_run_start").data
    other = enabled("jobs_hide", keys=["demo:1"], confirm_id=held["confirm_id"])
    assert other.error
    assert enabled.store.scout_runs() == []


def test_the_start_tool_takes_no_inputs_but_the_confirm_id(enabled):
    tools = {tool.name: tool for tool in enabled.tools()}
    assert set(tools["scan_run_start"].inputSchema["properties"]) == {"confirm_id"}
    assert tools["scan_run_start"].annotations.destructiveHint is True
    assert tools["scan_status"].annotations.readOnlyHint is True


def test_the_start_tool_is_audited_in_remote_mode(enabled, tmp_path, monkeypatch):
    import json

    from jobseer_bridge import bridge as module
    from jobseer_bridge.audit import AuditLog
    from jobseer_bridge.bridge import Bridge

    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(module, "current_identity", lambda: ("someone", "c1"))
    config = Config(url=enabled.base, authentik_user="cole")
    client = Client(config)
    bridge = Bridge(config, client=client, remote=True, audit=AuditLog(str(path)))
    spec, start = next((spec, fn) for spec, fn in bridge.tools() if spec.name == "scan_run_start")
    guarded = bridge.guard(start, spec)
    held = json.loads(guarded())["data"]
    guarded(confirm_id=held["confirm_id"])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(row["tool"], row["status"], row["confirm"]) for row in rows] == [
        ("scan_run_start", "ok", "previewed"), ("scan_run_start", "ok", "applied")]
    client.close()


def test_a_scan_started_between_preview_and_confirm_is_a_clean_conflict(enabled):
    held = enabled("scan_run_start").data
    other = Client(Config(url=enabled.base, authentik_user="cole"))
    try:
        other.send("POST", "/api/v1/scan-runs")
    finally:
        other.close()
    late = enabled("scan_run_start", confirm_id=held["confirm_id"])
    assert not late.error
    assert late.body["conflict"] is True and late.body["ok"] is False
    assert "already running" in late.body["message"]
    assert len(enabled.store.scout_runs()) == 1
