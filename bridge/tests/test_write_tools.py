def status_of(dashboard, key):
    return next(row["status"] for row in dashboard.demo.jobs() if row["dedupe_key"] == key)


def test_set_status_sends_expected_values(call, dashboard):
    reply = call("job_set_status", key="demo:2", status="saved")
    assert reply.data["status"] == "saved"
    assert status_of(dashboard, "demo:2") == "saved"


def test_stale_status_comes_back_as_a_conflict_result(call, dashboard, client):
    client.jobs()
    dashboard.demo.save("demo:2", "archived", None, None, None)
    reply = call("job_set_status", key="demo:2", status="saved")
    assert not reply.error
    assert reply.body["conflict"] is True
    assert reply.body["current"] == {"status": "archived"}
    assert reply.body["target"].startswith("http://127.0.0.1")
    assert status_of(dashboard, "demo:2") == "archived"


def test_explicit_expected_status_wins(call, dashboard):
    reply = call("job_set_status", key="demo:2", status="saved", expected_status="queued")
    assert reply.body["conflict"] is True
    assert reply.body["current"] == {"status": "new"}


def test_notes_edit_checks_old_notes(call, dashboard):
    reply = call("job_set_status", key="demo:1", notes="call recruiter")
    assert reply.data["notes"] == "call recruiter"
    stale = call("job_set_status", key="demo:1", notes="other", expected_notes="not what it was")
    assert stale.body["conflict"] is True
    assert stale.body["current"] == {"notes": "call recruiter"}


def test_set_status_needs_something_to_change(call):
    reply = call("job_set_status", key="demo:1")
    assert reply.error and "status or notes" in reply.text


def test_bad_status_is_rejected_by_schema(call):
    assert call("job_set_status", key="demo:1", status="done").error


def test_unknown_job_is_an_error(call):
    assert call("job_set_status", key="nope", status="saved").error


def test_cache_is_dropped_after_a_write(call):
    assert call("inbox_list", status="saved").data["total"] == 0
    call("job_set_status", key="demo:2", status="saved")
    assert call("inbox_list", status="saved").data["total"] == 1


def test_tag_and_untag(call, dashboard):
    added = call("jobs_tag", keys=["demo:1", "demo:2"], tag="warm")
    assert added.data["tag"] == "warm"
    assert call("inbox_list", tag="warm").data["total"] == 2
    call("jobs_tag", keys=["demo:2"], tag="warm", remove=True)
    assert call("inbox_list", tag="warm").data["total"] == 1


def test_edit_posting_returns_edit_id(call):
    reply = call("job_edit", key="demo:2", title="Platform Intern")
    assert reply.data["edit_id"] == 1
    assert reply.data["job"]["title"] == "Platform Intern"
    assert call("job_edit", key="demo:2").error


def test_queue_reorder(call):
    reply = call("queue_reorder", keys=["demo:1"])
    assert reply.data == {"dedupe_keys": ["demo:1"]}
    assert call("queue_reorder", keys=[]).error


def test_board_reorder(call):
    call("job_set_status", key="demo:2", status="applied")
    reply = call("board_reorder", status="applied", keys=["demo:2"])
    assert reply.data["dedupe_keys"] == ["demo:2"]
    assert call("board_reorder", status="new", keys=[]).error


def test_write_tools_are_not_read_only_or_destructive(listing):
    tools = {tool.name: tool for tool in listing()}
    for name in ("job_set_status", "jobs_tag", "job_edit", "queue_reorder", "board_reorder"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
