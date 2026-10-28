import pytest

from jobseer_bridge.profile_diff import apply, diff, editable


def current(call):
    return call("profile_get").data


def test_no_op_changes_nothing(call):
    revision = current(call)["revision"]
    reply = call("profile_update", set_fields={"skills": "Python, Linux, Git"}).data
    assert reply == {"changed": False, "revision": revision}


def test_set_field_previews_a_diff_then_saves(call):
    before = current(call)
    held = call("profile_update", set_fields={"phone": "555-0100"}).data
    assert held["needs_confirmation"] is True
    assert held["diff"]["fields"]["changed"][0]["changes"]["value"] == ["", "555-0100"]
    assert current(call)["revision"] == before["revision"]
    saved = call("profile_update", set_fields={"phone": "555-0100"}, confirm_id=held["confirm_id"]).data
    assert saved["changed"] is True
    after = current(call)
    assert after["revision"] == before["revision"] + 1
    assert next(row for row in after["fields"] if row["key"] == "phone")["value"] == "555-0100"


def test_removing_a_story_is_listed_as_removed(call):
    stories = current(call)["stories"]
    story = stories[0]
    held = call("profile_update", remove={"stories": [story["id"]]}).data
    assert held["diff"]["stories"]["removed"] == [story["title"]]
    call("profile_update", remove={"stories": [story["id"]]}, confirm_id=held["confirm_id"])
    assert [row["id"] for row in current(call)["stories"]] == [row["id"] for row in stories[1:]]


def test_upsert_adds_and_partially_updates(call):
    template = current(call)["answer_templates"][0]
    args = {"upsert": {"answer_templates": [{"id": template["id"], "body": "New body"}, {"name": "Why us", "body": "Because"}], "documents": [{"name": "Cover letter", "date": "2026-10-01", "url": ""}]}}
    held = call("profile_update", **args).data
    assert held["diff"]["answer_templates"]["added"] == ["Why us"]
    assert held["diff"]["documents"]["added"] == ["Cover letter"]
    call("profile_update", confirm_id=held["confirm_id"], **args)
    after = current(call)
    by_name = {row["name"]: row for row in after["answer_templates"]}
    assert by_name[template["name"]]["body"] == "New body"
    assert by_name["Why us"]["body"] == "Because"
    assert "Cover letter" in [row["name"] for row in after["documents"]]


def test_confirm_fails_if_profile_changed_after_preview(call, dashboard):
    args = {"set_fields": {"phone": "1"}}
    held = call("profile_update", **args).data
    dashboard.demo.save_profile({**dashboard.demo._profile})
    reply = call("profile_update", confirm_id=held["confirm_id"], **args)
    assert reply.error and "changed after the preview" in reply.text
    assert next(row for row in current(call)["fields"] if row["key"] == "phone")["value"] == ""


def test_confirm_id_is_bound_to_the_diff_request(call):
    held = call("profile_update", set_fields={"phone": "1"}).data
    reply = call("profile_update", set_fields={"phone": "2"}, confirm_id=held["confirm_id"])
    assert reply.error and "different arguments" in reply.text


def test_bad_requests_are_errors(call):
    assert call("profile_update", set_fields={"nope": "x"}).error
    assert call("profile_update", remove={"stories": ["missing"]}).error
    assert call("profile_update", upsert={"widgets": [{}]}).error
    assert call("profile_update", upsert={"stories": [{"situation": "no title"}]}).error


def test_diff_is_empty_for_equal_profiles_and_hides_computed_rows():
    profile = {"fields": [], "documents": [{"id": "a", "name": "A", "application_count": 3}], "answer_templates": [], "stories": [], "revision": 1}
    assert diff(editable(profile), apply(profile, None, None, None)) == {}


def test_long_values_are_clipped_in_the_diff():
    old = {"fields": [{"key": "k", "label": "K", "value": ""}]}
    new = {"fields": [{"key": "k", "label": "K", "value": "x" * 500}]}
    shown = diff(old, new)["fields"]["changed"][0]["changes"]["value"][1]
    assert len(shown) < 200 and shown.endswith("...")


def test_profile_update_is_marked_destructive(listing):
    tool = next(tool for tool in listing() if tool.name == "profile_update")
    assert tool.annotations.destructiveHint is True
