import pytest

from jobseer_bridge.bridge import Bridge
from jobseer_bridge.client import BridgeError, Client
from jobseer_bridge.config import Config
from jobseer_bridge.server import build

from conftest import Reply, drive

READS = {"status", "inbox_list", "job_get", "queue_get", "tracker_get", "companies_list", "company_get", "rules_list", "views_list", "profile_get", "interviews_ics", "apply_session_get", "overview_get", "scan_status"}
NEW_WRITES = {"jobs_tag_undo", "job_edit_undo", "rule_action_undo", "activity_dismiss", "profile_copy_mark", "job_description_paste", "overview_request", "overview_dismiss", "overview_regenerate", "overview_regenerate_undo", "profile_field_set", "profile_story_save", "profile_template_save", "profile_document_save"}


@pytest.fixture
def readonly_app(dashboard, transport):
    config = Config(url=dashboard.base, authentik_user="cole", readonly=True)
    return build(config, Client(config, transport=transport))


def test_readonly_registers_only_read_tools(readonly_app):
    server, _ = readonly_app
    tools = drive(server, None, {}).tools
    assert {tool.name for tool in tools} == READS
    assert all(tool.annotations.readOnlyHint for tool in tools)


def test_readonly_hides_every_new_write_tool(readonly_app):
    server, _ = readonly_app
    assert not {tool.name for tool in drive(server, None, {}).tools} & NEW_WRITES


def test_readonly_hides_write_tools_from_calls(readonly_app):
    server, _ = readonly_app
    reply = Reply(drive(server, "job_set_status", {"key": "demo:1", "status": "saved"}))
    assert reply.error and "Unknown tool" in reply.text


def test_readonly_status_says_so(readonly_app):
    server, _ = readonly_app
    assert Reply(drive(server, "status", {})).data["readonly"] is True


def test_client_refuses_writes_in_readonly_mode(dashboard):
    client = Client(Config(url=dashboard.base, authentik_user="cole", readonly=True))
    with pytest.raises(BridgeError, match="read-only"):
        client.send("PATCH", "/api/v1/jobs/demo:1", {"status": "saved"})


def test_full_tool_set_is_the_documented_size(listing):
    names = {tool.name for tool in listing()}
    assert READS <= names
    assert NEW_WRITES <= names
    assert len(names) == 53


def test_every_response_names_the_target(call, config):
    for name, args in (("status", {}), ("inbox_list", {}), ("tracker_get", {}), ("rules_list", {}), ("job_set_status", {"key": "demo:2", "status": "saved"}), ("job_set_status", {"key": "demo:2", "status": "saved", "expected_status": "archived"}), ("jobs_hide", {"keys": ["demo:2"]})):
        assert call(name, **args).body["target"] == config.url


def test_errors_name_the_target(call, config):
    reply = call("job_edit", key="demo:2", url="http://insecure.example")
    assert reply.error and config.url in reply.text


def test_unreachable_target_is_a_clear_error():
    config = Config(url="http://127.0.0.1:9", authentik_user="cole")
    server, _ = build(config)
    reply = Reply(drive(server, "inbox_list", {}))
    assert reply.error and "cannot reach http://127.0.0.1:9" in reply.text
