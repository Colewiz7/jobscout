def test_tag_undo_reverses_an_add_and_a_remove(call):
    added = call("jobs_tag", keys=["demo:1", "demo:2"], tag="warm").data
    assert call("inbox_list", tag="warm").data["total"] == 2
    call("jobs_tag_undo", ids=added["ids"], undo="add")
    assert call("inbox_list", tag="warm").data["total"] == 0
    again = call("jobs_tag", keys=["demo:2"], tag="warm").data
    removed = call("jobs_tag", keys=["demo:2"], tag="warm", remove=True).data
    assert call("inbox_list", tag="warm").data["total"] == 0
    call("jobs_tag_undo", ids=removed["ids"], undo="remove")
    assert call("inbox_list", tag="warm").data["total"] == 1
    assert again["ids"]


def test_tag_undo_conflicts_when_another_tab_got_there(call):
    added = call("jobs_tag", keys=["demo:1"], tag="warm").data
    call("jobs_tag_undo", ids=added["ids"], undo="add")
    stale = call("jobs_tag_undo", ids=added["ids"], undo="add")
    assert not stale.error and stale.body["conflict"] is True


def test_tag_undo_needs_ids(call):
    assert call("jobs_tag_undo", ids=[], undo="add").error


def test_posting_edit_undo_restores_the_title(call):
    before = call("job_get", key="demo:2").data["job"]["title"]
    edit = call("job_edit", key="demo:2", title="Renamed role").data
    assert call("job_get", key="demo:2").data["job"]["title"] == "Renamed role"
    undone = call("job_edit_undo", edit_id=edit["edit_id"]).data
    assert undone["job"]["title"] == before


def test_posting_edit_undo_unknown_edit_is_an_error(call):
    assert call("job_edit_undo", edit_id=999).error


def test_rule_action_undo_brings_back_an_archived_job(call):
    job = call("job_get", key="demo:2").data["job"]
    call("rule_save", name="drop it", kind="archive_title", pattern=job["title"].split()[0])
    archived = call("job_get", key="demo:2").data["job"]
    assert archived["status"] == "archived" and archived["rule_action_id"]
    call("rule_action_undo", action_id=archived["rule_action_id"])
    assert call("job_get", key="demo:2").data["job"]["status"] != "archived"


def test_rule_action_undo_unknown_id_is_an_error(call):
    assert call("rule_action_undo", action_id=999).error


def test_activity_dismiss_hides_and_restores_an_entry(call):
    call("job_set_status", key="demo:2", status="saved")
    entry = call("tracker_get").data["history"][0]
    shown = call("activity_dismiss", id=entry["id"]).data
    assert shown["entry"]["dismissed_at"]
    assert call("tracker_get").data["history"] == []
    call("activity_dismiss", id=entry["id"], dismissed=False)
    assert [row["id"] for row in call("tracker_get").data["history"]] == [entry["id"]]


def test_activity_dismiss_twice_is_a_conflict(call):
    call("job_set_status", key="demo:2", status="saved")
    entry_id = call("tracker_get").data["history"][0]["id"]
    call("activity_dismiss", id=entry_id)
    again = call("activity_dismiss", id=entry_id)
    assert again.error or again.body.get("conflict") is True


def test_copy_mark_shows_in_the_profile(call):
    call("profile_copy_mark", job="demo:1", target="email")
    assert "email" in call("profile_get", job="demo:1").data["copied_fields"]


TEXT = "We are hiring a platform engineer to run build systems and on call. " * 3


def test_description_paste_previews_then_saves(call):
    held = call("job_description_paste", key="demo:1", text=TEXT).data
    assert held["needs_confirmation"] is True
    assert held["preview"]["chars"] == len(TEXT)
    saved = call("job_description_paste", key="demo:1", text=TEXT, confirm_id=held["confirm_id"]).data
    assert saved["description_chars"] > 100
    assert call("job_get", key="demo:1", fetch=True).data["description"]["description_text"]


def test_description_paste_confirm_is_bound_and_single_use(call):
    held = call("job_description_paste", key="demo:1", text=TEXT).data
    wrong = call("job_description_paste", key="demo:1", text=TEXT + "more", confirm_id=held["confirm_id"])
    assert wrong.error and "different arguments" in wrong.text
    held = call("job_description_paste", key="demo:1", text=TEXT).data
    call("job_description_paste", key="demo:1", text=TEXT, confirm_id=held["confirm_id"])
    again = call("job_description_paste", key="demo:1", text=TEXT, confirm_id=held["confirm_id"])
    assert again.error and "confirm_id" in again.text
    assert call("job_description_paste", key="demo:1", text=TEXT, confirm_id="nonsense").error


def test_description_paste_empty_text_is_an_error(call):
    assert call("job_description_paste", key="demo:1", text="").error


def test_description_paste_is_a_confirm_tool(listing):
    tool = next(item for item in listing() if item.name == "job_description_paste")
    assert tool.annotations.destructiveHint is True


def test_apply_session_get_loads_by_id(call):
    started = call("apply_session", action="start").data["session"]
    loaded = call("apply_session_get", session_id=started["id"]).data
    assert loaded["id"] == started["id"]
    assert call("apply_session_get", session_id=99999).error


class DescriptionStub:
    def __init__(self):
        self.text = "old posting text"
        self.fetched = "2026-10-01"
        self.sent = []

    def features(self):
        return {}

    def job(self, key):
        return {"dedupe_key": key}

    def get(self, path, **params):
        return {"description": {"description_text": self.text, "description_fetched_at": self.fetched}}

    def send(self, method, path, body=None):
        self.sent.append(body)
        return {"description": {"description_text": body["text"]}}


def paste_bridge():
    from jobseer_bridge.bridge import Bridge
    from jobseer_bridge.config import Config

    stub = DescriptionStub()
    return Bridge(Config(url="http://x"), client=stub), stub


def test_paste_preview_shows_the_text_it_replaces():
    bridge, stub = paste_bridge()
    held = bridge.job_description_paste("demo:1", "new text")
    assert held["preview"]["current_starts"] == "old posting text"
    assert held["preview"]["current_chars"] == len("old posting text")
    assert stub.sent == []


def test_paste_unchanged_description_saves():
    bridge, stub = paste_bridge()
    held = bridge.job_description_paste("demo:1", "new text")
    bridge.job_description_paste("demo:1", "new text", confirm_id=held["confirm_id"])
    assert stub.sent == [{"text": "new text"}]


def test_paste_conflicts_when_the_text_changed_after_the_preview():
    bridge, stub = paste_bridge()
    held = bridge.job_description_paste("demo:1", "new text")
    stub.text = "someone else pasted this"
    out = bridge.guard(bridge.job_description_paste)(key="demo:1", text="new text", confirm_id=held["confirm_id"])
    assert '"conflict":true' in out and "someone else pasted this" in out
    assert stub.sent == []


def test_paste_ignores_a_refetch_with_the_same_text():
    bridge, stub = paste_bridge()
    held = bridge.job_description_paste("demo:1", "new text")
    stub.fetched = "2026-10-02"
    bridge.job_description_paste("demo:1", "new text", confirm_id=held["confirm_id"])
    assert stub.sent == [{"text": "new text"}]
