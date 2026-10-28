def test_status_names_the_target(call, config):
    reply = call("status")
    assert reply.body["target"] == config.url
    assert reply.data == {"user": "cole", "quick_fill": True, "readonly": False}


def test_inbox_list_filters_locally(call, transport):
    everything = call("inbox_list").data
    assert everything["total"] == 2
    assert {row["dedupe_key"] for row in everything["jobs"]} == {"demo:1", "demo:2"}
    assert call("inbox_list", text="datadog").data["jobs"][0]["dedupe_key"] == "demo:2"
    assert call("inbox_list", status="queued").data["total"] == 1
    assert call("inbox_list", company="nope").data["total"] == 0
    assert transport.count("GET", "/api/v1/jobs") == 1


def test_inbox_list_pages_and_caps(call):
    page = call("inbox_list", limit=1, offset=1).data
    assert page["total"] == 2 and len(page["jobs"]) == 1
    assert len(call("inbox_list", limit=5000).data["jobs"]) == 2


def test_inbox_slim_rows_drop_heavy_fields(call):
    row = call("inbox_list").data["jobs"][0]
    assert "score_detail" not in row and "notes" not in row


def test_job_get_adds_eligibility_and_optional_posting(call, transport):
    plain = call("job_get", key="demo:1").data
    assert plain["job"]["company"] == "Cloudflare"
    assert plain["eligibility"]["verdict"] == "clear"
    assert "description" not in plain
    assert transport.count("GET", "/api/v1/jobs/demo%3A1/description") == 0
    fetched = call("job_get", key="demo:1", fetch=True).data
    assert "reliable systems" in fetched["description"]["description_text"]


def test_job_get_unknown_key_is_an_error(call):
    reply = call("job_get", key="missing")
    assert reply.error and "job not found" in reply.text


def test_queue_get_lists_queued_jobs(call):
    data = call("queue_get").data
    assert [row["dedupe_key"] for row in data["queue"]] == ["demo:1"]
    assert data["session"] is None
    assert data["documents"][0]["id"] == "resume-general"


def test_tracker_get_returns_sections(call):
    data = call("tracker_get").data
    assert set(data) == {"applications", "reminders", "interviews"}


def test_companies_list_and_get(call):
    listed = call("companies_list").data["companies"]
    assert {row["key"] for row in listed} == {"cloudflare", "datadog"}
    one = call("company_get", key="cloudflare").data
    assert one["name"] == "Cloudflare"
    assert one["postings"][0]["dedupe_key"] == "demo:1"
    assert call("companies_list", name="cloudflare").data["companies"][0]["postings"] == 1


def test_rules_views_profile_and_ics(call):
    assert call("rules_list").data["rules"] == []
    assert call("views_list").data["saved_views"] == []
    profile = call("profile_get").data
    assert isinstance(profile["revision"], int)
    assert profile["stories"] and profile["answer_templates"]
    assert call("interviews_ics").data["ics"].startswith("BEGIN:VCALENDAR")


def test_every_read_tool_is_marked_read_only(listing):
    tools = {tool.name: tool for tool in listing()}
    assert {"status", "inbox_list", "job_get", "queue_get", "tracker_get", "companies_list", "company_get", "rules_list", "views_list", "profile_get", "interviews_ics"} <= set(tools)
    for tool in tools.values():
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False


def test_labels_and_errors_use_scheme_host_port_only():
    from conftest import Reply, drive
    from jobseer_bridge.config import Config
    from jobseer_bridge.server import build

    server, _ = build(Config(url="http://127.0.0.1:9/private/base"))
    reply = Reply(drive(server, "inbox_list", {}))
    assert reply.error
    assert "http://127.0.0.1:9" in reply.text
    assert "private" not in reply.text
