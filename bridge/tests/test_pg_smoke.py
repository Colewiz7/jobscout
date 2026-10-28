import datetime as dt
import os
import threading

import pytest

from jobscout import db as database
from jobscout.dashboard import DashboardServer, PostgresStore
from jobseer_bridge.client import Client
from jobseer_bridge.config import Config
from jobseer_bridge.server import build

from conftest import Reply, drive

DSN = os.environ.get("JOBSCOUT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")


@pytest.fixture
def pg_call():
    with database.connect(DSN) as conn:
        database.ensure_schema(conn)
    server = DashboardServer(("127.0.0.1", 0), PostgresStore(DSN), require_auth=True, authentik_user="cole", quick_fill_enabled=True)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    config = Config(url="http://127.0.0.1:%d" % server.server_address[1], authentik_user="cole")
    mcp, _ = build(config, Client(config))
    yield lambda tool_name, **args: Reply(drive(mcp, tool_name, args))
    server.shutdown()
    server.server_close()


def confirmed(call, tool_name, **args):
    first = call(tool_name, **args)
    assert not first.error, first.text
    held = first.data
    assert held["needs_confirmation"] is True
    reply = call(tool_name, confirm_id=held["confirm_id"], **args)
    assert not reply.error, reply.text
    return reply.data


def test_tools_against_postgres(pg_call):
    call = pg_call
    assert call("status").data["quick_fill"] is True
    assert call("inbox_list").data["total"] == 0

    confirmed(call, "job_capture", url="https://example.com/careers/1", company="Acme", title="SRE Intern")
    key = call("inbox_list").data["jobs"][0]["dedupe_key"]
    assert call("job_set_status", key=key, status="saved").data["status"] == "saved"
    assert call("job_get", key=key).data["eligibility"]
    stale = call("job_set_status", key=key, status="archived", expected_status="new")
    assert stale.body["conflict"] is True and stale.body["current"] == {"status": "saved"}
    assert call("jobs_tag", keys=[key], tag="warm").data["tag"] == "warm"
    assert call("inbox_list", tag="warm").data["total"] == 1

    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat()
    interview = call("interview_save", key=key, starts_at=soon).data["interview"]["id"]
    assert call("interview_save", id=interview, location="room 4").data["interview"]["location"] == "room 4"
    reminder = call("reminder_save", key=key, due_at=soon, note="nudge").data["reminder"]["id"]
    assert call("reminder_save", id=reminder, action="done").data["reminder"]["status"] == "done"

    rule = call("rule_save", name="drop sales", kind="archive_title", pattern="sales").data["rule"]["id"]
    assert call("rule_save", id=rule, enabled=False).data["rule"]["enabled"] is False
    confirmed(call, "delete_record", kind="rule", id=rule)
    assert call("restore_record", kind="rule", id=rule).data["rule"]["id"] == rule
    view = call("view_save", name="remote", filters={"remote": True}).data["saved_view"]["id"]
    assert call("view_save", id=view, sort="newest").data["view"]["sort"] == "newest"

    assert call("company_save", action="note", company="Acme", body="met at fair").data["note"]["body"] == "met at fair"
    contact = call("contact_save", company="Acme", name="Sam Lee").data["contact"]["id"]
    assert call("contact_save", company="Acme", id=contact, title="Recruiter").data["contact"]["title"] == "Recruiter"

    revision = call("profile_get").data["revision"]
    saved = confirmed(call, "profile_update", upsert={"fields": [{"key": "phone", "label": "Phone", "group": "Contact", "value": "555-0100"}]})
    assert saved["revision"] == revision + 1

    token = confirmed(call, "jobs_hide", keys=[key])["undo_token"]
    assert call("inbox_list").data["total"] == 0
    assert call("jobs_hide_undo", undo_token=token).data["restored"] == 1
    assert call("inbox_list").data["total"] == 1
