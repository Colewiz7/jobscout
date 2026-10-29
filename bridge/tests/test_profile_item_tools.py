import pytest

TOOLS = ("profile_field_set", "profile_story_save", "profile_template_save", "profile_document_save")


def current(call):
    return call("profile_get").data


def test_field_set_previews_then_saves(call):
    before = current(call)
    held = call("profile_field_set", key="phone", value="555-0100").data
    assert held["needs_confirmation"] is True
    assert held["diff"]["fields"]["changed"][0]["changes"]["value"] == ["", "555-0100"]
    assert current(call)["revision"] == before["revision"]
    saved = call("profile_field_set", key="phone", value="555-0100", confirm_id=held["confirm_id"]).data
    assert saved["changed"] is True
    assert next(row for row in current(call)["fields"] if row["key"] == "phone")["value"] == "555-0100"


def test_field_set_can_clear_a_value(call):
    held = call("profile_field_set", key="skills", value="").data
    call("profile_field_set", key="skills", value="", confirm_id=held["confirm_id"])
    assert next(row for row in current(call)["fields"] if row["key"] == "skills")["value"] == ""


def test_story_edit_keeps_the_other_parts(call):
    story = current(call)["stories"][0]
    args = {"id": story["id"], "result": "Cut recovery time in half"}
    held = call("profile_story_save", **args).data
    assert list(held["diff"]["stories"]["changed"][0]["changes"]) == ["result"]
    call("profile_story_save", confirm_id=held["confirm_id"], **args)
    after = current(call)["stories"][0]
    assert after["result"] == "Cut recovery time in half"
    assert after["situation"] == story["situation"]


def test_story_add_and_remove(call):
    args = {"title": "Led a migration", "situation": "Old stack", "competencies": ["Planning"]}
    held = call("profile_story_save", **args).data
    assert held["diff"]["stories"]["added"] == ["Led a migration"]
    call("profile_story_save", confirm_id=held["confirm_id"], **args)
    added = next(row for row in current(call)["stories"] if row["title"] == "Led a migration")
    gone = call("profile_story_save", id=added["id"], remove=True).data
    assert gone["diff"]["stories"]["removed"] == ["Led a migration"]
    call("profile_story_save", id=added["id"], remove=True, confirm_id=gone["confirm_id"])
    assert "Led a migration" not in [row["title"] for row in current(call)["stories"]]


def test_template_edit_add_remove(call):
    template = current(call)["answer_templates"][0]
    edit = {"id": template["id"], "body": "Short body"}
    held = call("profile_template_save", **edit).data
    call("profile_template_save", confirm_id=held["confirm_id"], **edit)
    assert current(call)["answer_templates"][0]["body"] == "Short body"
    new = {"name": "Strengths", "body": "Systems work"}
    held = call("profile_template_save", **new).data
    assert held["diff"]["answer_templates"]["added"] == ["Strengths"]
    call("profile_template_save", confirm_id=held["confirm_id"], **new)
    added = next(row for row in current(call)["answer_templates"] if row["name"] == "Strengths")
    gone = call("profile_template_save", id=added["id"], remove=True).data
    call("profile_template_save", id=added["id"], remove=True, confirm_id=gone["confirm_id"])
    assert "Strengths" not in [row["name"] for row in current(call)["answer_templates"]]


def test_document_default_moves_to_one_resume(call):
    docs = current(call)["documents"]
    args = {"id": docs[-1]["id"], "default": True}
    held = call("profile_document_save", **args).data
    call("profile_document_save", confirm_id=held["confirm_id"], **args)
    after = current(call)["documents"]
    assert [row["id"] for row in after if row["default"]] == [docs[-1]["id"]]


def test_document_add_and_remove(call):
    args = {"name": "Cover letter", "date": "2026-10-01", "url": "https://example.com/c"}
    held = call("profile_document_save", **args).data
    assert held["diff"]["documents"]["added"] == ["Cover letter"]
    call("profile_document_save", confirm_id=held["confirm_id"], **args)
    added = next(row for row in current(call)["documents"] if row["name"] == "Cover letter")
    gone = call("profile_document_save", id=added["id"], remove=True).data
    call("profile_document_save", id=added["id"], remove=True, confirm_id=gone["confirm_id"])
    assert "Cover letter" not in [row["name"] for row in current(call)["documents"]]


def test_no_op_changes_nothing(call):
    revision = current(call)["revision"]
    value = next(row for row in current(call)["fields"] if row["key"] == "skills")["value"]
    assert call("profile_field_set", key="skills", value=value).data == {"changed": False, "revision": revision}


@pytest.mark.parametrize("name, args, other", [
    ("profile_field_set", {"key": "phone", "value": "1"}, {"key": "phone", "value": "2"}),
    ("profile_story_save", {"title": "A"}, {"title": "B"}),
    ("profile_template_save", {"name": "A", "body": "x"}, {"name": "A", "body": "y"}),
    ("profile_document_save", {"name": "A"}, {"name": "B"}),
])
def test_confirm_id_is_bound_to_the_request(call, name, args, other):
    held = call(name, **args).data
    reply = call(name, confirm_id=held["confirm_id"], **other)
    assert reply.error and "different arguments" in reply.text
    assert call(name, confirm_id="nonsense", **args).error


@pytest.mark.parametrize("name, args", [
    ("profile_field_set", {"key": "phone", "value": "1"}),
    ("profile_story_save", {"title": "Fresh story"}),
    ("profile_template_save", {"name": "Fresh", "body": "x"}),
    ("profile_document_save", {"name": "Fresh doc"}),
])
def test_stale_preview_is_refused_and_confirm_id_cannot_be_reused(call, dashboard, name, args):
    held = call(name, **args).data
    dashboard.demo.save_profile({**dashboard.demo._profile})
    stale = call(name, confirm_id=held["confirm_id"], **args)
    assert stale.error and "changed after the preview" in stale.text
    again = call(name, confirm_id=held["confirm_id"], **args)
    assert again.error and "confirm_id" in again.text


def test_missing_targets_are_errors(call):
    assert call("profile_field_set", key="nope", value="x").error
    assert call("profile_story_save").error
    assert call("profile_story_save", remove=True).error
    assert call("profile_story_save", id="missing", remove=True).error
    assert call("profile_template_save").error
    assert call("profile_document_save", remove=True).error


def test_tools_are_confirm_kind_and_hidden_in_readonly(listing):
    by_name = {tool.name: tool for tool in listing()}
    for name in TOOLS:
        assert by_name[name].annotations.destructiveHint is True
        assert by_name[name].annotations.readOnlyHint is False


def test_a_change_between_the_check_and_the_save_is_a_conflict(app, dashboard):
    _, bridge = app
    args = {"key": "phone", "value": "555-0100"}
    held = bridge.profile_field_set(**args)
    real = bridge.client.get

    # simulates a concurrent tab
    def racing(path, **params):
        out = real(path, **params)
        if path == "/api/v1/profile":
            dashboard.demo.save_profile({**dashboard.demo._profile})
        return out

    bridge.client.get = racing
    from jobseer_bridge.client import Conflict

    with pytest.raises(Conflict):
        bridge.profile_field_set(confirm_id=held["confirm_id"], **args)
    bridge.client.get = real
    assert next(row for row in bridge.profile_get()["fields"] if row["key"] == "phone")["value"] == ""


def test_the_save_carries_the_previewed_revision():
    from jobseer_bridge.bridge import Bridge
    from jobseer_bridge.config import Config

    profile = {"revision": 3, "name": "N", "email": "", "phone": "", "location": "", "highlights": [], "fields": [{"key": "phone", "label": "Phone", "value": ""}], "documents": [], "answer_templates": [], "stories": []}

    class Stub:
        sent = None

        def features(self):
            return {"quick_fill": True}

        def get(self, path, **params):
            return {"profile": profile}

        def send(self, method, path, body=None):
            self.sent = body
            return {"profile": {**profile, "revision": 4}}

    stub = Stub()
    bridge = Bridge(Config(), client=stub)
    held = bridge.profile_field_set(key="phone", value="1")
    bridge.profile_field_set(key="phone", value="1", confirm_id=held["confirm_id"])
    assert stub.sent["profile"]["revision"] == 3
