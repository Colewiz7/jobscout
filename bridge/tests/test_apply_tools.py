import datetime as dt


def soon(days=2):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat()


def test_session_start_update_and_end(call):
    started = call("apply_session", action="start").data
    assert started["session"]["id"] == 1
    assert [job["dedupe_key"] for job in started["jobs"]] == ["demo:1"]
    updated = call("apply_session", action="update", applied=["demo:1"], ended=True).data
    assert updated["session"]["applied"] == ["demo:1"]
    assert updated["session"]["ended_at"]


def test_session_update_conflict_returns_current_session(call):
    call("apply_session", action="start")
    reply = call("apply_session", action="update", ended=True, expected_revision=99)
    assert reply.body["conflict"] is True
    assert reply.body["current"]["session"]["revision"] == 0


def test_session_update_without_a_session_is_an_error(call):
    assert call("apply_session", action="update", ended=True).error


def test_check_and_mark_applied(call):
    checked = call("apply_session", action="check", key="demo:1").data
    assert checked
    applied = call("apply_session", action="mark_applied", key="demo:1", document_id="resume-general").data
    assert applied["snapshot"]


def test_next_step_uses_current_value_as_expected(call, dashboard):
    reply = call("apply_session", action="next_step", key="demo:2", next_step="email recruiter")
    assert reply.data["next_step"] == "email recruiter"
    stale = call("apply_session", action="next_step", key="demo:2", next_step="x", expected_next_step="old")
    assert stale.body["conflict"] is True
    assert stale.body["current"] == {"next_step": "email recruiter"}


def test_next_step_needs_text(call):
    assert call("apply_session", action="next_step", key="demo:2").error


def test_interview_create_then_edit_with_auto_revision(call):
    created = call("interview_save", key="demo:1", starts_at=soon(), location="room 1").data
    interview_id = created["interview"]["id"]
    edited = call("interview_save", id=interview_id, location="room 2").data
    assert edited["interview"]["location"] == "room 2"
    assert edited["interview"]["revision"] == 1


def test_interview_stale_revision_is_a_conflict(call):
    interview_id = call("interview_save", key="demo:1", starts_at=soon()).data["interview"]["id"]
    call("interview_save", id=interview_id, notes="first")
    reply = call("interview_save", id=interview_id, notes="second", expected_revision=0)
    assert reply.body["conflict"] is True
    assert reply.body["current"]["interview"]["notes"] == "first"


def test_interview_create_needs_key_and_start(call):
    assert call("interview_save", key="demo:1").error


def test_reminder_create_and_actions(call):
    created = call("reminder_save", key="demo:1", due_at=soon(1), note="nudge").data
    reminder_id = created["reminder"]["id"]
    done = call("reminder_save", id=reminder_id, action="done").data
    assert done["reminder"]["status"] == "done"
    reopened = call("reminder_save", id=reminder_id, action="reopen").data
    assert reopened["reminder"]["status"] == "pending"


def test_reminder_stale_revision_is_a_conflict(call):
    reminder_id = call("reminder_save", key="demo:1", due_at=soon(1), note="n").data["reminder"]["id"]
    call("reminder_save", id=reminder_id, action="done")
    reply = call("reminder_save", id=reminder_id, action="reopen", expected_revision=0)
    assert reply.body["conflict"] is True
    assert reply.body["current"]["reminder"]["status"] == "done"


def test_reminder_needs_an_action_when_editing(call):
    assert call("reminder_save", id=1).error


def test_unknown_record_is_an_error(call):
    assert call("interview_save", id=77, notes="x").error
