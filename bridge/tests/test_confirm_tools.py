import pytest

from jobseer_bridge.client import BridgeError
from jobseer_bridge.confirm import Confirms


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_confirm_id_is_single_use_and_bound_to_args():
    clock = Clock()
    confirms = Confirms(ttl=10, clock=clock)
    first = confirms.issue("tool", {"a": 1})
    confirms.redeem(first, "tool", {"a": 1})
    with pytest.raises(BridgeError):
        confirms.redeem(first, "tool", {"a": 1})
    other = confirms.issue("tool", {"a": 1})
    with pytest.raises(BridgeError, match="different"):
        confirms.redeem(other, "tool", {"a": 2})
    wrong_tool = confirms.issue("tool", {"a": 1})
    with pytest.raises(BridgeError, match="different"):
        confirms.redeem(wrong_tool, "other", {"a": 1})


def test_confirm_id_expires():
    clock = Clock()
    confirms = Confirms(ttl=10, clock=clock)
    held = confirms.issue("tool", {})
    clock.now = 11
    with pytest.raises(BridgeError, match="expired"):
        confirms.redeem(held, "tool", {})


def test_pending_ids_are_capped():
    confirms = Confirms(ttl=1000, clock=Clock())
    for index in range(80):
        confirms.issue("tool", {"n": index})
    assert len(confirms._pending) <= 50


def hidden_keys(dashboard):
    return set(dashboard.demo._hidden)


def test_hide_previews_then_acts(call, dashboard):
    preview = call("jobs_hide", keys=["demo:2"]).data
    assert preview["needs_confirmation"] is True
    assert preview["preview"]["jobs"][0]["company"] == "Datadog"
    assert hidden_keys(dashboard) == set()
    done = call("jobs_hide", keys=["demo:2"], confirm_id=preview["confirm_id"]).data
    assert done["hidden"][0]["dedupe_key"] == "demo:2"
    assert hidden_keys(dashboard) == {"demo:2"}
    undone = call("jobs_hide_undo", undo_token=done["undo_token"]).data
    assert undone["restored"] == 1
    assert hidden_keys(dashboard) == set()


def test_confirm_id_cannot_be_reused_or_moved(call, dashboard):
    preview = call("jobs_hide", keys=["demo:2"]).data
    moved = call("jobs_hide", keys=["demo:1"], confirm_id=preview["confirm_id"])
    assert moved.error and "different arguments" in moved.text
    assert hidden_keys(dashboard) == set()
    again = call("jobs_hide", keys=["demo:2"], confirm_id=preview["confirm_id"])
    assert again.error
    assert hidden_keys(dashboard) == set()


def test_undo_twice_is_a_conflict_result(call):
    preview = call("jobs_hide", keys=["demo:2"]).data
    token = call("jobs_hide", keys=["demo:2"], confirm_id=preview["confirm_id"]).data["undo_token"]
    call("jobs_hide_undo", undo_token=token)
    second = call("jobs_hide_undo", undo_token=token)
    assert second.body["conflict"] is True


def test_bulk_tagging_needs_confirmation_only_above_ten(call, dashboard):
    call("jobs_tag", keys=["demo:1"], tag="small")
    assert call("inbox_list", tag="small").data["total"] == 1
    keys = ["demo:1", "demo:2"] + ["demo:%d" % n for n in range(3, 14)]
    held = call("jobs_tag", keys=keys, tag="big").data
    assert held["needs_confirmation"] is True
    assert held["preview"]["count"] == 13
    assert call("inbox_list", tag="big").data["total"] == 0


def test_delete_and_restore_record(call):
    rule = call("rule_save", name="a", kind="archive_title", pattern="x").data["rule"]
    held = call("delete_record", kind="rule", id=rule["id"]).data
    assert held["preview"]["record"]["name"] == "a"
    assert call("rules_list").data["rules"]
    call("delete_record", kind="rule", id=rule["id"], confirm_id=held["confirm_id"])
    assert call("rules_list").data["rules"] == []
    restored = call("restore_record", kind="rule", id=rule["id"]).data
    assert restored["rule"]["name"] == "a"


def test_delete_each_record_kind(call):
    import datetime as dt

    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
    interview = call("interview_save", key="demo:1", starts_at=soon).data["interview"]["id"]
    reminder = call("reminder_save", key="demo:1", due_at=soon, note="n").data["reminder"]["id"]
    contact = call("contact_save", company="Cloudflare", name="Sam").data["contact"]["id"]
    link = call("company_save", action="link", company="Cloudflare", label="l", url="https://example.com/x").data["link"]["id"]
    view = call("view_save", name="v").data["saved_view"]["id"]
    for kind, record in (("interview", interview), ("reminder", reminder), ("contact", contact), ("link", link), ("view", view)):
        held = call("delete_record", kind=kind, id=record).data
        assert held["preview"]["restorable"] is (kind != "reminder")
        done = call("delete_record", kind=kind, id=record, confirm_id=held["confirm_id"])
        assert not done.error and done.data == {"ok": True}
    assert call("tracker_get").data["interviews"] == []


def test_delete_missing_record_is_an_error(call):
    held = call("delete_record", kind="rule", id=99).data
    assert call("delete_record", kind="rule", id=99, confirm_id=held["confirm_id"]).error


def test_company_merge_and_undo(call):
    call("contact_save", company="Datadog", name="Pat")
    held = call("company_merge", source="Datadog", target="Cloudflare").data
    assert held["preview"]["moves"]["contacts"] == 1
    merged = call("company_merge", source="Datadog", target="Cloudflare", confirm_id=held["confirm_id"]).data
    assert merged["moved"]["contacts"] == 1
    undone = call("company_merge_undo", merge_id=merged["merge_id"]).data
    assert undone
    assert call("company_get", key="Datadog").data["contacts"][0]["name"] == "Pat"


def test_company_merge_undo_conflict(call):
    held = call("company_merge", source="Datadog", target="Cloudflare").data
    merged = call("company_merge", source="Datadog", target="Cloudflare", confirm_id=held["confirm_id"]).data
    reply = call("company_merge_undo", merge_id=merged["merge_id"], expected_revision=99)
    assert reply.body["conflict"] is True


def test_company_hide_and_restore(call, dashboard):
    held = call("company_hide", company="Datadog").data
    assert held["preview"]["hide"] == "Datadog"
    call("company_hide", company="Datadog", confirm_id=held["confirm_id"])
    assert dashboard.demo._companies["datadog"]["hidden_at"] is not None
    restored = call("company_hide", company="Datadog", hidden=False)
    assert not restored.error
    assert dashboard.demo._companies["datadog"]["hidden_at"] is None


def test_connections_import(call):
    people = [{"company": "Cloudflare", "name": "Ann Lee", "title": "Eng", "linkedin_url": "https://www.linkedin.com/in/ann"}]
    held = call("connections_import", contacts=people).data
    assert held["preview"]["count"] == 1
    done = call("connections_import", contacts=people, confirm_id=held["confirm_id"])
    assert not done.error
    assert call("connections_import", contacts=[]).error


def test_capture_previews_the_fetch(call):
    args = {"url": "https://example.com/careers/42", "company": "Acme", "title": "SRE Intern"}
    held = call("job_capture", **args).data
    assert held["preview"]["server_will_fetch"] == args["url"]
    assert not any(row["company"] == "Acme" for row in call("inbox_list").data["jobs"])
    done = call("job_capture", confirm_id=held["confirm_id"], **args).data
    assert done["job"]["company"] == "Acme"
    assert any(row["company"] == "Acme" for row in call("inbox_list").data["jobs"])


def block_demo_job(dashboard):
    dashboard.demo._profile["fields"] = [
        {"key": "work_authorization", "value": "Requires employment sponsorship"},
        {"key": "skills", "value": "Python, Linux"},
    ]
    dashboard.demo.description = lambda key, **kwargs: None if key != "demo:1" else {
        "description_text": "Python is required. We will not provide visa sponsorship.",
        "sections": [{"key": "requirements", "text": "Python is required"}],
    }


def test_eligibility_override_flow(call, dashboard):
    block_demo_job(dashboard)
    args = {"key": "demo:1", "action": "override", "note": "checked with recruiter"}
    held = call("eligibility_override", **args).data
    assert held["preview"]["eligibility"]["verdict"] == "do_not_apply"
    done = call("eligibility_override", confirm_id=held["confirm_id"], **args).data
    assert done["eligibility"]["verdict"] != "do_not_apply"
    assert done["override_ids"]
    revoked = call("eligibility_override", key="demo:1", action="revoke").data
    assert revoked["eligibility"]["verdict"] == "do_not_apply"
    restore = {"key": "demo:1", "action": "restore", "ids": revoked["ids"]}
    held = call("eligibility_override", **restore).data
    back = call("eligibility_override", confirm_id=held["confirm_id"], **restore).data
    assert back["eligibility"]["verdict"] != "do_not_apply"


def test_eligibility_restore_needs_ids(call):
    assert call("eligibility_override", key="demo:1", action="restore").error


def test_destructive_tools_carry_the_hint(listing):
    tools = {tool.name: tool for tool in listing()}
    for name in ("jobs_hide", "delete_record", "company_merge", "company_hide", "connections_import", "job_capture", "eligibility_override"):
        assert tools[name].annotations.destructiveHint is True
        assert tools[name].annotations.readOnlyHint is False
    for name in ("jobs_hide_undo", "restore_record", "company_merge_undo"):
        assert tools[name].annotations.destructiveHint is False
    assert tools["job_capture"].annotations.openWorldHint is True


@pytest.mark.parametrize("url", ["http://example.com/jobs/1", "https://localhost/jobs", "https://127.0.0.1/jobs", "https://10.0.0.5/jobs", "https://169.254.169.254/latest", "https://[::1]/jobs", "https://printer.local/jobs", "https://user:pw@example.com/jobs", "ftp://example.com/jobs"])
def test_capture_refuses_non_public_targets_before_any_preview(call, app, url):
    _, bridge = app
    reply = call("job_capture", url=url, company="Acme", title="SRE")
    assert reply.error
    assert bridge.confirms._pending == {}


def test_failing_preview_leaves_no_pending_id(call, app, monkeypatch):
    _, bridge = app
    monkeypatch.setattr(bridge, "_brief", lambda keys: (_ for _ in ()).throw(BridgeError("boom")))
    reply = call("jobs_hide", keys=["demo:2"])
    assert reply.error and "boom" in reply.text
    assert bridge.confirms._pending == {}
