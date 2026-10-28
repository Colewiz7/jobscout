"""The dashboard is a small HTTP boundary, so exercise it as one."""
import datetime as dt
import json
import pathlib
import threading

import httpx
import pytest

from jobscout.dashboard import DashboardServer, DemoStore, _annotate_reposts, load_profile

AUTH = {
    "X-authentik-username": "cole",
    "X-authentik-meta-app": "jobseer",
    "X-authentik-meta-outpost": "authentik Embedded Outpost",
}


@pytest.fixture
def dashboard():
    store = DemoStore()
    try:
        server = DashboardServer(
            ("127.0.0.1", 0),
            store,
            require_auth=True,
            authentik_user="cole",
        )
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, store
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def quick_fill_dashboard():
    store = DemoStore()
    try:
        server = DashboardServer(("127.0.0.1", 0), store, require_auth=True, authentik_user="cole", quick_fill_enabled=True)
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_address[1], store
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def authenticated_client(base):
    client = httpx.Client(base_url=base, headers=AUTH, timeout=2)
    session = client.get("/api/v1/session")
    assert session.status_code == 200
    return client, session.json()["csrf_token"]


def test_default_profile_is_a_non_secret_seed(monkeypatch):
    monkeypatch.delenv("JOBSCOUT_PROFILE_NAME", raising=False)
    profile = load_profile()
    assert profile["name"] == "Example Applicant"
    assert profile["phone"] == ""
    assert profile["highlights"] == []


def test_private_profile_scalars_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("JOBSCOUT_PROFILE_EMAIL", "private@example.com")
    assert load_profile()["email"] == "private@example.com"


def test_seeded_performance_fixture_has_2000_deterministic_jobs():
    fixture = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "dashboard_2000.seed.json").read_text()
    )
    first = DemoStore(**fixture).jobs()
    second = DemoStore(**fixture).jobs()

    assert len(first) == 2_000
    assert [job["dedupe_key"] for job in first] == [job["dedupe_key"] for job in second]
    assert [job["score"] for job in first] == [job["score"] for job in second]


def test_reposts_are_observed_within_ninety_days_without_claiming_intent():
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    jobs = [{
        "dedupe_key": f"job:{index}", "company": "Acme, Inc.",
        "title": f"Platform Intern — Summer {2027 + index % 2}",
        "first_seen": start + dt.timedelta(days=30 * index),
    } for index in range(3)]
    _annotate_reposts(jobs)
    assert "repost_count" not in jobs[0]
    assert jobs[1]["repost_count"] == 2
    assert jobs[2]["repost_count"] == 3
    assert jobs[2]["ghost_job"] is True


def test_dashboard_serves_shell_and_history_routes(dashboard):
    base, _ = dashboard
    shell = httpx.get(f"{base}/inbox", timeout=2)
    nested = httpx.get(f"{base}/queue/session/example", timeout=2)

    assert shell.status_code == 200
    assert "Search or run a command" in shell.text
    assert "default-src 'self'" in shell.headers["content-security-policy"]
    assert nested.status_code == 200
    assert nested.text == shell.text
    assert '<link rel="icon" href="/static/icons/jobseer-fan-transparent-112.png?v=2"' in shell.text
    assert "/static/icons/jobseer-fan-transparent-112.png" in shell.text
    icon = httpx.get(f"{base}/static/icons/jobseer-fan-32.png", timeout=2)
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"
    brand_icon = httpx.get(f"{base}/static/icons/jobseer-fan-transparent-112.png", timeout=2)
    assert brand_icon.status_code == 200
    assert brand_icon.headers["content-type"] == "image/png"


def test_company_names_have_real_icons(dashboard):
    base, _ = dashboard
    manifest = httpx.get(f"{base}/static/company-logos/manifest.json", timeout=2).json()
    for company in (
        "GE HealthCare", "General Electric", "MORSE Corp", "RF-SMART",
        "Dow Chemical Company", "Southwest Airlines", "MetLife",
        "NJM Insurance Group", "Point72", "Regeneron Pharmaceuticals", "Stryten",
    ):
        icon = httpx.get(f"{base}/static/company-logos/{manifest[company]}", timeout=2)
        assert icon.status_code == 200
        assert icon.headers["content-type"] == "image/png"
        assert icon.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_dynamic_company_icons_are_authenticated_and_same_origin(dashboard):
    base, store = dashboard
    store.company_icon_keys = lambda: ["new brand"]
    store.company_icon = lambda name: {"logo_data": b"icon", "media_type": "image/png"} if name == "New Brand" else None
    assert httpx.get(f"{base}/api/v1/company-icons", timeout=2).status_code == 401
    client, _ = authenticated_client(base)
    assert client.get("/api/v1/company-icons").json() == {"keys": ["new brand"]}
    image = client.get("/api/v1/company-icons/New%20Brand")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.content == b"icon"
    assert client.get("/api/v1/company-icons/Unknown").status_code == 404


def test_v1_api_requires_authentik(dashboard):
    base, _ = dashboard
    response = httpx.get(f"{base}/api/v1/jobs", timeout=2)
    assert response.status_code == 401
    assert "Authentik" in response.json()["error"]


def test_ai_overview_is_flagged_and_auth_protected():
    class FakeOverviews:
        def overview(self, sections):
            return [{"kind": "role", "text": sections[0]["text"]}]

        def cached_overview(self, sections):
            return self.overview(sections)

    server = DashboardServer(
        ("127.0.0.1", 0), DemoStore(), require_auth=True,
        authentik_user="cole", overviews=FakeOverviews(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert httpx.get(f"{base}/api/v1/jobs/fixture%3A0000/overview").status_code == 401
        with httpx.Client(base_url=base, headers=AUTH) as client:
            assert client.get("/api/v1/session").json()["features"]["ai_overview"] is True
            key = DemoStore().jobs()[0]["dedupe_key"]
            assert client.post(f"/api/v1/jobs/{key}/overview").status_code == 403
            token = client.get("/api/v1/session").json()["csrf_token"]
            queued = client.post(f"/api/v1/jobs/{key}/overview", headers={"X-CSRF-Token": token})
            assert queued.status_code == 200
            assert queued.json()["status"] == "ready"
            response = client.get(f"/api/v1/jobs/{key}/overview")
            assert response.status_code == 200
            assert response.json()["items"][0]["text"].startswith("Build reliable systems")
            cached = client.get(f"/api/v1/jobs/{key}/overview?cached=1")
            assert cached.status_code == 200
            assert cached.json()["items"] == response.json()["items"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_manual_capture_requires_auth_and_csrf_then_adds_a_job(dashboard):
    base, _ = dashboard
    payload = {
        "url": "https://example.org/jobs/platform-intern",
        "company": "Example Labs", "title": "Platform Intern",
        "location": "Boston, MA",
    }
    assert httpx.post(f"{base}/api/v1/capture", json=payload).status_code == 401
    with httpx.Client(base_url=base, headers=AUTH) as client:
        assert client.post("/api/v1/capture", json=payload).status_code == 403
        token = client.get("/api/v1/session").json()["csrf_token"]
        response = client.post("/api/v1/capture", json=payload, headers={"X-CSRF-Token": token})
        assert response.status_code == 201
        assert response.json()["job"]["company"] == "Example Labs"


def test_hidden_jobs_leave_every_view_until_undone(dashboard):
    base, store = dashboard
    store.rows[0]["status"] = "applied"
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    jobs = lambda: [job["dedupe_key"] for job in client.get("/api/v1/jobs?closed=1").json()["jobs"]]
    assert client.post("/api/v1/hidden", json={"dedupe_keys": ["demo:1"]}).status_code == 403
    assert client.post("/api/v1/hidden", json={"dedupe_keys": []}, headers=headers).status_code == 400

    hidden = client.post("/api/v1/hidden", json={"dedupe_keys": ["demo:1", "missing"]}, headers=headers)
    assert hidden.json() == {"hidden": [{"id": 1, "dedupe_key": "demo:1"}]}
    assert jobs() == ["demo:2"]
    assert client.get("/api/v1/tracker").json()["applications"] == []
    recaptured = client.post("/api/v1/capture", headers=headers, json={
        "url": "https://example.org/jobs/sre", "company": "Cloudflare, Inc.",
        "title": "Site Reliability Engineer Intern - Summer 2027",
    })
    assert recaptured.status_code == 400
    assert recaptured.json()["error"] == "You hid this job, so it stays hidden."

    undone = client.post("/api/v1/hidden/undo", json={"ids": [1]}, headers=headers)
    assert undone.json() == {"restored": 1}
    assert jobs() == ["demo:1", "demo:2"]


def test_undo_from_a_stale_tab_cannot_reverse_an_earlier_hide(dashboard):
    base, _ = dashboard
    first_tab, token = authenticated_client(base)
    second_tab, second_token = authenticated_client(base)
    hide = {"dedupe_keys": ["demo:1"]}
    first = first_tab.post("/api/v1/hidden", json=hide, headers={"X-CSRF-Token": token}).json()["hidden"]
    second = second_tab.post("/api/v1/hidden", json=hide, headers={"X-CSRF-Token": second_token}).json()["hidden"]
    assert len(first) == 1 and second == []

    undo = second_tab.post("/api/v1/hidden/undo", json={"ids": [entry["id"] for entry in second]},
                           headers={"X-CSRF-Token": second_token})
    assert undo.status_code == 400
    jobs = [job["dedupe_key"] for job in second_tab.get("/api/v1/jobs").json()["jobs"]]
    assert jobs == ["demo:2"]


def test_writes_to_a_hidden_job_are_refused(quick_fill_dashboard):
    base, store = quick_fill_dashboard
    store.rows[0]["status"] = "applied"
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    interview = client.post("/api/v1/interviews", headers=headers,
                            json={"dedupe_key": "demo:1", "starts_at": "2026-11-01T15:00:00Z"}).json()["interview"]
    store.rows[0]["status"] = "applied"
    before = dict(store.rows[0])
    client.post("/api/v1/hidden", json={"dedupe_keys": ["demo:1"]}, headers=headers)
    writes = [
        ("PATCH", f"/api/v1/interviews/{interview['id']}", {"notes": "stale"}),
        ("DELETE", f"/api/v1/interviews/{interview['id']}", None),
        ("POST", f"/api/v1/interviews/{interview['id']}/restore", None),
        ("PATCH", "/api/v1/jobs/demo%3A1", {"status": "saved", "notes": "stale"}),
        ("PUT", "/api/v1/applications/demo%3A1/next-step", {"next_step": "stale"}),
        ("POST", "/api/v1/applications/demo%3A1/applied", {}),
        ("POST", "/api/v1/interviews", {"dedupe_key": "demo:1", "starts_at": "2026-11-01T15:00:00Z"}),
        ("POST", "/api/v1/jobs/demo%3A1/description", {"text": "stale"}),
        ("POST", "/api/v1/jobs/demo%3A1/eligibility/override", {"note": "stale"}),
        ("POST", "/api/v1/queue/check", {"dedupe_key": "demo:1"}),
        ("POST", "/api/v1/profile/copy", {"dedupe_key": "demo:1", "target_key": "name"}),
        ("PUT", "/api/v1/profile/context", {"dedupe_key": "demo:1", "company": "Cloudflare"}),
        ("PUT", "/api/v1/queue/order", {"dedupe_keys": ["demo:1"]}),
        ("PUT", "/api/v1/tracker/board-order", {"status": "applied", "dedupe_keys": ["demo:1"]}),
        ("PATCH", "/api/v1/jobs/demo%3A1/posting", {"title": "stale"}),
    ]
    for method, path, body in writes:
        response = client.request(method, path, json=body, headers=headers)
        assert (path, response.status_code) == (path, 410)
        assert response.json()["error"] == "This job is hidden, so it can't change."
    assert store.rows[0] == before
    assert store._interviews[0]["notes"] == "" and not store._interviews[0].get("deleted")
    assert client.patch("/api/v1/jobs/demo%3A2", json={"status": "saved"}, headers=headers).status_code == 200


def test_posting_edits_need_auth_and_csrf_and_undo_in_order(dashboard):
    base, _ = dashboard
    path = "/api/v1/jobs/demo%3A2/posting"
    assert httpx.patch(f"{base}{path}", json={"title": "x"}).status_code == 401
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    assert client.patch(path, json={"title": "x"}).status_code == 403
    for body, error in [
        ({"title": "  "}, "Role title can't be empty. Add one and save again."),
        ({"url": "http://example.com/job"}, "Posting URL must be a public HTTPS address"),
        ({"description": "too short"}, "Paste at least 40 and no more than 100,000 characters from the posting"),
        ({"location": "Remote"}, "location can't be edited"),
        ({}, "Change the company, role title, URL or description, then save"),
    ]:
        response = client.patch(path, json=body, headers=headers)
        assert (response.status_code, response.json()["error"]) == (400, error)
    assert client.patch("/api/v1/jobs/missing/posting", json={"title": "x"}, headers=headers).status_code == 404

    text = "Build the internal platform with a small team.\nRequirements\nGo\nKubernetes"
    first = client.patch(path, headers=headers, json={
        "company": "Datadog, Inc.", "title": "Platform Intern",
        "url": "https://careers.example.com/platform", "description": text,
    })
    assert first.status_code == 200
    body = first.json()
    assert body["job"]["company"] == "Datadog, Inc."
    assert body["job"]["url"] == "https://careers.example.com/platform"
    assert body["description"]["description_text"].startswith("Build the internal platform")
    assert "description_html" not in body["description"]
    second = client.patch(path, headers=headers, json={"title": "Platform Engineering Intern"}).json()
    assert client.patch(path, headers=headers, json={"title": "Platform Engineering Intern"}).json()["edit_id"] is None
    titles = lambda: {job["dedupe_key"]: job["title"] for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert titles()["demo:2"] == "Platform Engineering Intern"

    stale = client.post(f"/api/v1/posting-edits/{body['edit_id']}/undo", headers=headers)
    assert stale.status_code == 409
    assert client.post(f"/api/v1/posting-edits/{second['edit_id']}/undo").status_code == 403
    assert client.post(f"/api/v1/posting-edits/{second['edit_id']}/undo", headers=headers).json()["job"]["title"] == "Platform Intern"
    restored = client.post(f"/api/v1/posting-edits/{body['edit_id']}/undo", headers=headers).json()
    assert (restored["job"]["company"], restored["job"]["title"]) == ("Datadog", "Cloud Platform Engineering Intern")
    assert restored["description"]["description_text"] == "Build reliable systems with a thoughtful engineering team."
    assert client.post(f"/api/v1/posting-edits/{body['edit_id']}/undo", headers=headers).status_code == 409
    assert client.post("/api/v1/posting-edits/99/undo", headers=headers).status_code == 404


def test_the_longest_description_fits_the_posting_edit_body(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    longest = "\u0001" * 100_000  # escapes to six bytes each
    response = client.patch("/api/v1/jobs/demo%3A2/posting", json={"description": longest}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    text = ("Line of posting text\n" * 5000)[:100_000]
    response = client.patch("/api/v1/jobs/demo%3A2/posting", json={"description": text}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200


def test_undoing_an_edit_to_a_hidden_job_is_refused(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    edit = client.patch("/api/v1/jobs/demo%3A1/posting", json={"title": "Edited"}, headers=headers).json()
    client.post("/api/v1/hidden", json={"dedupe_keys": ["demo:1"]}, headers=headers)
    response = client.post(f"/api/v1/posting-edits/{edit['edit_id']}/undo", headers=headers)
    assert (response.status_code, response.json()["error"]) == (410, "This job is hidden, so it can't change.")


def test_forged_username_without_outpost_metadata_is_rejected(dashboard):
    base, _ = dashboard
    response = httpx.get(
        f"{base}/api/v1/jobs",
        headers={"X-authentik-username": "cole"},
        timeout=2,
    )
    assert response.status_code == 401


def test_wrong_authentik_application_or_user_is_rejected(dashboard):
    base, _ = dashboard
    wrong_app = httpx.get(
        f"{base}/api/v1/jobs",
        headers={**AUTH, "X-authentik-meta-app": "another-app"},
        timeout=2,
    )
    wrong_user = httpx.get(
        f"{base}/api/v1/jobs",
        headers={**AUTH, "X-authentik-username": "someone-else"},
        timeout=2,
    )
    assert wrong_app.status_code == 401
    assert wrong_user.status_code == 401


def test_profile_is_not_shipped_in_phase_one(dashboard):
    base, _ = dashboard
    response = httpx.get(f"{base}/api/v1/profile", headers=AUTH, timeout=2)
    write = httpx.put(f"{base}/api/v1/profile", headers=AUTH, json={}, timeout=2)
    context = httpx.put(f"{base}/api/v1/profile/context", headers=AUTH, json={}, timeout=2)
    copied = httpx.post(f"{base}/api/v1/profile/copy", headers=AUTH, json={}, timeout=2)
    eligibility = httpx.get(f"{base}/api/v1/jobs/demo%3A1/eligibility", headers=AUTH, timeout=2)
    override = httpx.post(
        f"{base}/api/v1/jobs/demo%3A1/eligibility/override", headers=AUTH, json={}, timeout=2,
    )
    assert response.status_code == 404
    assert write.status_code == 404
    assert context.status_code == 404
    assert copied.status_code == 404
    assert eligibility.status_code == 404
    assert override.status_code == 404


def test_eligibility_verdict_and_override_are_feature_gated_and_logged():
    store = DemoStore()
    store._profile["fields"] = [
        {"key": "work_authorization", "value": "Requires employment sponsorship"},
        {"key": "skills", "value": "Python, Linux"},
    ]
    store.description = lambda key: None if key != "demo:1" else {
        "description_text": "Python is required. We will not provide visa sponsorship.",
        "sections": [{"key": "requirements", "text": "Python is required"}],
    }
    try:
        server = DashboardServer(
            ("127.0.0.1", 0), store, require_auth=True,
            authentik_user="cole", quick_fill_enabled=True,
        )
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        client, csrf = authenticated_client(base)
        try:
            before = client.get("/api/v1/jobs/demo%3A1/eligibility")
            blocked_queue = client.patch(
                "/api/v1/jobs/demo%3A1",
                headers={"X-CSRF-Token": csrf},
                json={"status": "queued", "notes": ""},
            )
            rejected = client.post(
                "/api/v1/jobs/demo%3A1/eligibility/override", json={"note": "Reviewed"},
            )
            overridden = client.post(
                "/api/v1/jobs/demo%3A1/eligibility/override",
                headers={"X-CSRF-Token": csrf}, json={"note": "Reviewed manually"},
            )
            allowed_queue = client.patch(
                "/api/v1/jobs/demo%3A1",
                headers={"X-CSRF-Token": csrf},
                json={"status": "queued", "notes": ""},
            )
        finally:
            client.close()
        assert before.json()["eligibility"]["verdict"] == "do_not_apply"
        assert before.json()["eligibility"]["requirements"][0]["matched"] is True
        assert blocked_queue.status_code == 409
        assert rejected.status_code == 403
        assert overridden.status_code == 201
        assert overridden.json()["eligibility"]["overridden"] is True
        assert allowed_queue.status_code == 200
        assert store._eligibility_overrides["demo:1"][0]["note"] == "Reviewed manually"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_quick_fill_profile_requires_explicit_feature_flag():
    store = DemoStore()
    try:
        server = DashboardServer(
            ("127.0.0.1", 0),
            store,
            require_auth=True,
            authentik_user="cole",
            quick_fill_enabled=True,
        )
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client(base_url=base, headers=AUTH, timeout=2) as client:
            session = client.get("/api/v1/session")
            response = client.get("/api/v1/profile")
            csrf = session.json()["csrf_token"]
            profile = response.json()["profile"]
            profile["fields"][0]["value"] = "Edited Applicant"
            rejected = client.put("/api/v1/profile", json={"profile": profile})
            saved = client.put(
                "/api/v1/profile",
                headers={"X-CSRF-Token": csrf},
                json={"profile": profile},
            )
            rejected_context = client.put(
                "/api/v1/profile/context",
                json={"dedupe_key": "demo:2", "company": "Datadog"},
            )
            context = client.put(
                "/api/v1/profile/context",
                headers={"X-CSRF-Token": csrf},
                json={
                    "dedupe_key": "demo:2",
                    "company": "Datadog",
                    "answer_overrides": {"Why this role?": "This answer is specific to Datadog."},
                    "company_account": {
                        "account_exists": True,
                        "sign_in_email": "applicant@example.invalid",
                        "password_manager_url": "https://vault.example.invalid/datadog",
                    },
                },
            )
            copied = client.post(
                "/api/v1/profile/copy",
                headers={"X-CSRF-Token": csrf},
                json={"dedupe_key": "demo:2", "target_key": "field:email"},
            )
            contextual_profile = client.get(
                "/api/v1/profile", params={"job": "demo:2", "company": "Datadog"}
            )
            unsafe_link = client.put(
                "/api/v1/profile/context",
                headers={"X-CSRF-Token": csrf},
                json={
                    "dedupe_key": "demo:2",
                    "company": "Datadog",
                    "answer_overrides": {},
                    "company_account": {"password_manager_url": "javascript:alert(1)"},
                },
            )
        assert response.status_code == 200
        assert response.json()["profile"]["fields"][0]["key"] == "name"
        assert len(response.json()["profile"]["stories"]) == 5
        assert session.json()["features"]["quick_fill"] is True
        assert rejected.status_code == 403
        assert saved.status_code == 200
        assert saved.json()["profile"]["fields"][0]["value"] == "Edited Applicant"
        assert rejected_context.status_code == 403
        assert context.status_code == 200
        assert copied.status_code == 201
        contextual = contextual_profile.json()["profile"]
        assert contextual_profile.status_code == 200
        assert contextual["copied_fields"] == ["field:email"]
        assert contextual["answer_overrides"]["Why this role?"] == "This answer is specific to Datadog."
        assert contextual["company_account"]["account_exists"] is True
        assert unsafe_link.status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_authenticated_jobs_round_trip(dashboard):
    base, _ = dashboard
    with httpx.Client(base_url=base, headers=AUTH, timeout=2) as client:
        response = client.get("/api/v1/jobs")
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 2


def test_job_description_is_plain_structured_data(dashboard):
    base, _ = dashboard
    response = httpx.get(
        f"{base}/api/v1/jobs/demo%3A1/description",
        headers=AUTH,
        timeout=2,
    )
    assert response.status_code == 200
    detail = response.json()["description"]
    assert detail["sections"][0]["key"] == "about"
    assert "description_html" not in detail


def test_manual_description_requires_csrf_and_returns_plain_sections(dashboard):
    base, _ = dashboard
    payload = {"text": "Responsibilities\nBuild reliable infrastructure with Python and document the tests.\nPay range: $28-$30 per hour."}
    with httpx.Client(base_url=base, headers=AUTH, timeout=2) as client:
        assert client.post("/api/v1/jobs/demo%3A1/description", json=payload).status_code == 403
        token = client.get("/api/v1/session").json()["csrf_token"]
        response = client.post(
            "/api/v1/jobs/demo%3A1/description", json=payload,
            headers={"X-CSRF-Token": token},
        )
    assert response.status_code == 200
    detail = response.json()["description"]
    assert detail["sections"][0]["key"] == "responsibilities"
    assert detail["pay"] == "$28–$30/hr"
    assert "description_html" not in detail


def test_application_state_round_trips_with_csrf(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        response = client.patch(
            "/api/v1/jobs/demo%3A2",
            headers={"X-CSRF-Token": csrf},
            json={"status": "applied", "notes": "Submitted on Friday"},
        )
        jobs = client.get("/api/v1/jobs").json()["jobs"]
    finally:
        client.close()
    changed = next(job for job in jobs if job["dedupe_key"] == "demo:2")

    assert response.status_code == 200
    assert changed["status"] == "applied"
    assert changed["notes"] == "Submitted on Friday"


def test_application_can_be_corrected_and_requeued_without_losing_snapshot(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    try:
        applying = client.patch("/api/v1/jobs/demo%3A2", headers=headers,
                                json={"status": "applying", "notes": ""})
        applied = client.post("/api/v1/applications/demo%3A2/applied", headers=headers,
                              json={"document_id": None})
        requeued = client.patch("/api/v1/jobs/demo%3A2", headers=headers,
                                json={"status": "queued", "notes": ""})
        tracker = client.get("/api/v1/tracker").json()
        jobs = client.get("/api/v1/jobs").json()["jobs"]
    finally:
        client.close()

    corrected = next(job for job in jobs if job["dedupe_key"] == "demo:2")
    assert applying.status_code == applied.status_code == requeued.status_code == 200
    assert applied.json()["snapshot"]["dedupe_key"] == "demo:2"
    assert requeued.json()["status"] == "queued"
    assert corrected["status"] == "queued"
    assert all(job["dedupe_key"] != "demo:2" for job in tracker["applications"])


def test_application_state_rejects_missing_csrf(dashboard):
    base, _ = dashboard
    response = httpx.patch(
        f"{base}/api/v1/jobs/demo%3A2",
        headers=AUTH,
        json={"status": "saved", "notes": ""},
        timeout=2,
    )
    assert response.status_code == 403


def test_application_state_rejects_unknown_status(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        response = client.patch(
            "/api/v1/jobs/demo%3A2",
            headers={"X-CSRF-Token": csrf},
            json={"status": "maybe", "notes": ""},
        )
    finally:
        client.close()
    assert response.status_code == 400


def test_application_state_rejects_cross_origin_write(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        response = client.patch(
            "/api/v1/jobs/demo%3A2",
            headers={"Origin": "https://attacker.example", "X-CSRF-Token": csrf},
            json={"status": "saved", "notes": ""},
        )
    finally:
        client.close()
    assert response.status_code == 403


def test_queue_session_rechecks_liveness_reorders_and_snapshots(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        queued = client.patch(
            "/api/v1/jobs/demo%3A2",
            headers={"X-CSRF-Token": csrf},
            json={"status": "queued", "notes": "Apply after Cloudflare"},
        )
        reordered = client.put(
            "/api/v1/queue/order",
            headers={"X-CSRF-Token": csrf},
            json={"dedupe_keys": ["demo:2", "demo:1"]},
        )
        session = client.post(
            "/api/v1/queue/session",
            headers={"X-CSRF-Token": csrf},
        )
        applied = client.post(
            "/api/v1/applications/demo%3A2/applied",
            headers={"X-CSRF-Token": csrf},
            json={"document_id": None},
        )
        jobs = client.get("/api/v1/jobs").json()["jobs"]
    finally:
        client.close()

    assert queued.status_code == 200
    assert queued.json()["liveness"]["status"] == "live"
    assert reordered.status_code == 200
    assert [job["dedupe_key"] for job in session.json()["jobs"]] == ["demo:2", "demo:1"]
    assert session.json()["documents"] == []
    assert applied.status_code == 200
    assert applied.json()["snapshot"]["title"] == "Cloud Platform Engineering Intern"
    changed = next(job for job in jobs if job["dedupe_key"] == "demo:2")
    assert changed["status"] == "applied"
    assert changed["applied_at"] is not None


def test_board_order_saves_a_column_and_status_changes_reset_it(dashboard):
    base, store = dashboard
    keys = ["demo:1", "demo:2"]
    for key in keys:
        store.save(key, "interviewing", "")
    client, csrf = authenticated_client(base)
    try:
        saved = client.put(
            "/api/v1/tracker/board-order",
            headers={"X-CSRF-Token": csrf},
            json={"status": "interviewing", "dedupe_keys": ["demo:2", "demo:1"]},
        )
        positions = {
            job["dedupe_key"]: job["board_position"]
            for job in client.get("/api/v1/tracker").json()["applications"]
        }
        moved = client.patch(
            "/api/v1/jobs/demo%3A1",
            headers={"X-CSRF-Token": csrf},
            json={"status": "offer", "notes": ""},
        )
        after = {
            job["dedupe_key"]: job["board_position"]
            for job in client.get("/api/v1/tracker").json()["applications"]
        }
    finally:
        client.close()
    assert saved.status_code == 200
    assert saved.json() == {"status": "interviewing", "dedupe_keys": ["demo:2", "demo:1"]}
    assert [positions[key] for key in keys] == [1, 0]
    assert moved.status_code == 200
    assert after["demo:1"] is None  # lands after the ordered cards
    assert after["demo:2"] == 0


def test_board_order_rejects_bad_columns_and_keys(dashboard):
    base, store = dashboard
    store.save("demo:1", "offer", "")
    store.save("demo:2", "applied", "")
    client, csrf = authenticated_client(base)
    put = lambda body: client.put("/api/v1/tracker/board-order", headers={"X-CSRF-Token": csrf}, json=body)
    try:
        wrong_column = put({"status": "offer", "dedupe_keys": ["demo:1", "demo:2"]})
        duplicate = put({"status": "offer", "dedupe_keys": ["demo:1", "demo:1"]})
        not_board = put({"status": "queued", "dedupe_keys": []})
        bad_keys = put({"status": "offer", "dedupe_keys": "demo:1"})
        missing_status = put({"dedupe_keys": ["demo:1"]})
        no_csrf = client.put("/api/v1/tracker/board-order", json={"status": "offer", "dedupe_keys": ["demo:1"]})
    finally:
        client.close()
    assert [r.status_code for r in (wrong_column, duplicate, not_board, bad_keys, missing_status)] == [400] * 5
    assert "only contain jobs in that column" in wrong_column.json()["error"]
    assert "duplicates" in duplicate.json()["error"]
    assert no_csrf.status_code == 403


def test_board_order_keeps_position_when_status_is_unchanged(dashboard):
    _, store = dashboard
    store.save("demo:1", "applied", "")
    store.save("demo:2", "applied", "")
    store.reorder_board("applied", ["demo:2", "demo:1"])
    store.save("demo:1", "applied", "new notes")
    store.mark_applied("demo:2", None)
    rows = {row["dedupe_key"]: row for row in store.rows}
    assert rows["demo:1"]["board_position"] == 1
    assert rows["demo:2"]["board_position"] == 0


def test_queue_writes_require_csrf(dashboard):
    base, _ = dashboard
    reorder = httpx.put(
        f"{base}/api/v1/queue/order",
        headers=AUTH,
        json={"dedupe_keys": ["demo:1"]},
        timeout=2,
    )
    session = httpx.post(f"{base}/api/v1/queue/session", headers=AUTH, timeout=2)
    check = httpx.post(f"{base}/api/v1/queue/check", headers=AUTH,
                       json={"dedupe_key": "demo:1"}, timeout=2)
    applied = httpx.post(
        f"{base}/api/v1/applications/demo%3A1/applied",
        headers=AUTH,
        json={"document_id": None},
        timeout=2,
    )
    assert reorder.status_code == 403
    assert session.status_code == 403
    assert check.status_code == 403
    assert applied.status_code == 403


def test_session_starts_without_checking_every_job_then_checks_current(dashboard):
    base, store = dashboard
    client, csrf = authenticated_client(base)
    try:
        client.patch("/api/v1/jobs/demo%3A1", headers={"X-CSRF-Token": csrf},
                     json={"status": "queued", "notes": ""})
        queued = next(job for job in store.rows if job["dedupe_key"] == "demo:1")
        queued["liveness_checked_at"] = None
        session = client.post("/api/v1/queue/session", headers={"X-CSRF-Token": csrf})
        assert session.status_code == 201
        assert queued["liveness_checked_at"] is None
        checked = client.post("/api/v1/queue/check", headers={"X-CSRF-Token": csrf},
                              json={"dedupe_key": "demo:1"})
        assert checked.status_code == 200
        assert checked.json()["status"] == "live"
        assert queued["liveness_checked_at"] is not None
    finally:
        client.close()


def test_session_check_archives_closed_current_job(dashboard):
    base, store = dashboard
    client, csrf = authenticated_client(base)
    try:
        client.patch("/api/v1/jobs/demo%3A1", headers={"X-CSRF-Token": csrf},
                     json={"status": "queued", "notes": ""})
        queued = next(job for job in store.rows if job["dedupe_key"] == "demo:1")
        queued["url"] = ""
        checked = client.post("/api/v1/queue/check", headers={"X-CSRF-Token": csrf},
                              json={"dedupe_key": "demo:1"})
        assert checked.status_code == 200
        assert checked.json()["status"] == "closed"
        assert queued["status"] == "archived"
    finally:
        client.close()


def test_closed_posting_is_archived_instead_of_entering_queue(dashboard):
    base, store = dashboard
    closed = next(job for job in store.rows if job["dedupe_key"] == "demo:2")
    closed["url"] = ""
    client, csrf = authenticated_client(base)
    try:
        response = client.patch(
            "/api/v1/jobs/demo%3A2",
            headers={"X-CSRF-Token": csrf},
            json={"status": "queued", "notes": ""},
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert response.json()["liveness"]["status"] == "closed"
    assert closed["status"] == "archived"


def test_tracker_followup_interview_calendar_and_next_step(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        applied = client.post(
            "/api/v1/applications/demo%3A2/applied",
            headers={"X-CSRF-Token": csrf},
            json={"document_id": None},
        )
        tracker = client.get("/api/v1/tracker")
        next_step = client.put(
            "/api/v1/applications/demo%3A2/next-step",
            headers={"X-CSRF-Token": csrf},
            json={"next_step": "Send portfolio link"},
        )
        interview = client.post(
            "/api/v1/interviews",
            headers={"X-CSRF-Token": csrf},
            json={
                "dedupe_key": "demo:2",
                "starts_at": "2026-10-10T14:00:00Z",
                "ends_at": "2026-10-10T15:00:00Z",
                "location": "Video call",
                "notes": "System design",
            },
        )
        after = client.get("/api/v1/tracker")
        calendar = client.get("/api/v1/interviews.ics")
    finally:
        client.close()

    assert applied.status_code == 200
    assert tracker.json()["reminders"][0]["kind"] == "follow_up"
    assert next_step.status_code == 200
    assert interview.status_code == 201
    assert after.json()["applications"][0]["status"] == "interviewing"
    assert after.json()["reminders"] == []
    assert "BEGIN:VCALENDAR" in calendar.text
    assert "Video call" in calendar.text


def test_interviews_are_edited_cancelled_deleted_and_restored(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    interview = client.post("/api/v1/interviews", headers=headers, json={
        "dedupe_key": "demo:2", "starts_at": "2026-11-10T14:00:00Z",
        "ends_at": "2026-11-10T15:00:00Z", "location": "Video call", "notes": "System design",
    }).json()["interview"]
    path = f"/api/v1/interviews/{interview['id']}"
    assert httpx.patch(f"{base}{path}", json={"notes": "x"}).status_code == 401
    assert client.patch(path, json={"notes": "x"}).status_code == 403
    assert client.delete(path).status_code == 403
    for body, error in [
        ({"starts_at": "soon"}, "interview start must be a valid date and time"),
        ({"ends_at": "2026-11-10T13:00:00Z"}, "interview end must be after its start"),
        ({"cancelled": "yes"}, "cancelled must be true or false"),
        ({"kind": "phone"}, "kind can't be edited"),
        ({}, "Change the time, location or notes, then save"),
    ]:
        response = client.patch(path, json=body, headers=headers)
        assert (response.status_code, response.json()["error"]) == (400, error)
    assert client.patch("/api/v1/interviews/99", json={"notes": "x"}, headers=headers).status_code == 404
    assert client.patch("/api/v1/interviews/abc", json={"notes": "x"}, headers=headers).status_code == 400

    edited = client.patch(path, headers=headers, json={"location": "Onsite", "starts_at": "2026-11-10T14:30:00Z"}).json()
    assert (edited["interview"]["location"], edited["previous"]["location"]) == ("Onsite", "Video call")
    previous = edited["previous"]
    undone = client.patch(path, headers=headers, json={
        key: previous[key] for key in ("starts_at", "ends_at", "location", "notes")
    }).json()["interview"]
    assert (undone["location"], undone["starts_at"]) == ("Video call", "2026-11-10T14:00:00+00:00")

    cancelled = client.patch(path, headers=headers, json={"cancelled": True}).json()["interview"]
    assert cancelled["cancelled_at"]
    assert client.get("/api/v1/tracker").json()["interviews"][0]["cancelled_at"] == cancelled["cancelled_at"]
    assert "STATUS:CANCELLED" in client.get("/api/v1/interviews.ics").text
    assert client.patch(path, headers=headers, json={"cancelled": False}).json()["interview"]["cancelled_at"] is None

    assert client.delete(path, headers=headers).json() == {"ok": True}
    assert client.get("/api/v1/tracker").json()["interviews"] == []
    assert client.delete(path, headers=headers).status_code == 404
    assert client.patch(path, json={"notes": "x"}, headers=headers).status_code == 404
    restored = client.post(f"{path}/restore", headers=headers)
    assert restored.json()["interview"]["location"] == "Video call"
    assert client.post(f"{path}/restore", headers=headers).status_code == 404
    assert [item["id"] for item in client.get("/api/v1/tracker").json()["interviews"]] == [interview["id"]]


def test_interview_undo_is_refused_after_another_tab_edits(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    other, other_token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    interview = client.post("/api/v1/interviews", headers=headers, json={
        "dedupe_key": "demo:2", "starts_at": "2026-11-10T14:00:00Z", "notes": "A",
    }).json()["interview"]
    path = f"/api/v1/interviews/{interview['id']}"
    assert interview["revision"] == 0
    edited = client.patch(path, headers=headers, json={"notes": "B"}).json()
    assert edited["interview"]["revision"] == 1
    assert client.patch(path, headers=headers, json={"notes": "x", "expected_revision": True}).status_code == 400
    undone = client.patch(path, headers=headers, json={"notes": "A", "expected_revision": 1})
    assert undone.json()["interview"]["revision"] == 2

    redone = client.patch(path, headers=headers, json={"notes": "B"}).json()["interview"]
    other.patch(path, headers={"X-CSRF-Token": other_token}, json={"notes": "C"})
    stale = client.patch(path, headers=headers, json={"notes": "A", "expected_revision": redone["revision"]})
    assert stale.status_code == 409
    assert stale.json()["error"] == "This interview changed in another tab."
    assert (stale.json()["interview"]["notes"], stale.json()["interview"]["revision"]) == ("C", 4)
    assert client.get("/api/v1/tracker").json()["interviews"][0]["notes"] == "C"


def test_company_notes_contacts_and_matched_connections(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        protected = client.get("/api/v1/companies", params={"name": "Datadog"})
        note = client.put(
            "/api/v1/companies/note",
            headers={"X-CSRF-Token": csrf},
            json={"company": "Datadog", "body": "Met the infrastructure team."},
        )
        manual = client.post(
            "/api/v1/contacts",
            headers={"X-CSRF-Token": csrf},
            json={"company": "Datadog", "name": "Grace Example", "title": "Recruiter"},
        )
        imported = client.post(
            "/api/v1/connections/import",
            headers={"X-CSRF-Token": csrf},
            json={"contacts": [{
                "company": "Datadog, Inc.", "name": "Lin Example",
                "title": "Engineer",
                "linkedin_url": "https://www.linkedin.com/in/lin-example",
            }]},
        )
        company = client.get("/api/v1/companies", params={"name": "Datadog"})
        jobs = client.get("/api/v1/jobs")
    finally:
        client.close()

    assert protected.json()["company"]["account_protected"] is True
    assert protected.json()["company"]["account"] is None
    assert note.status_code == 200
    assert manual.status_code == 201
    assert imported.json()["imported"] == 1
    assert len(company.json()["company"]["contacts"]) == 2
    assert company.json()["company"]["note"]["body"] == "Met the infrastructure team."
    datadog = next(job for job in jobs.json()["jobs"] if job["company"] == "Datadog")
    assert datadog["connections_count"] == 1


def test_contacts_are_edited_deleted_and_restored(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    imported = client.post("/api/v1/connections/import", headers=headers, json={"contacts": [{
        "company": "Datadog", "name": "Lin Example", "title": "Engineer",
        "linkedin_url": "https://www.linkedin.com/in/lin-example",
    }]})
    assert imported.status_code == 201
    contacts = lambda: client.get("/api/v1/companies", params={"name": "Datadog"}).json()["company"]["contacts"]
    connections = lambda: next(job for job in client.get("/api/v1/jobs").json()["jobs"] if job["company"] == "Datadog")["connections_count"]
    [contact] = contacts()
    path = f"/api/v1/contacts/{contact['id']}"
    assert httpx.patch(f"{base}{path}", json={"name": "x"}).status_code == 401
    assert client.patch(path, json={"name": "x"}).status_code == 403
    assert client.delete(path).status_code == 403
    assert client.post(f"{path}/restore").status_code == 403
    for body, error in [
        ({"name": " "}, "A contact needs a name. Add one and save again."),
        ({"linkedin_url": "http://www.linkedin.com/in/lin"}, "LinkedIn URL must use HTTPS"),
        ({"company": "Cloudflare"}, "company can't be edited"),
        ({}, "Change the name, role, email or LinkedIn URL, then save"),
    ]:
        response = client.patch(path, json=body, headers=headers)
        assert (response.status_code, response.json()["error"]) == (400, error)
    assert client.patch("/api/v1/contacts/99", json={"name": "x"}, headers=headers).status_code == 404
    assert client.delete("/api/v1/contacts/abc", headers=headers).status_code == 400

    edited = client.patch(path, headers=headers, json={"title": "Staff Engineer", "email": "lin@example.com"}).json()
    assert (edited["contact"]["title"], edited["previous"]["title"]) == ("Staff Engineer", "Engineer")
    previous = edited["previous"]
    client.patch(path, headers=headers, json={key: previous[key] for key in ("name", "title", "email", "linkedin_url")})
    assert (contacts()[0]["title"], contacts()[0]["email"]) == ("Engineer", "")

    assert client.delete(path, headers=headers).json() == {"ok": True}
    assert contacts() == [] and connections() == 0
    assert client.delete(path, headers=headers).status_code == 404
    assert client.post(f"{path}/restore", headers=headers).json()["contact"]["name"] == "Lin Example"
    assert client.post(f"{path}/restore", headers=headers).status_code == 404
    assert len(contacts()) == 1 and connections() == 1


def test_contact_undo_is_refused_after_another_tab_edits(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    other, other_token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    contact = client.post("/api/v1/contacts", headers=headers, json={"company": "Datadog", "name": "Grace", "title": "A"}).json()["contact"]
    path = f"/api/v1/contacts/{contact['id']}"
    edited = client.patch(path, headers=headers, json={"title": "B"}).json()["contact"]
    assert client.patch(path, headers=headers, json={"title": "A", "expected_revision": edited["revision"]}).status_code == 200
    redone = client.patch(path, headers=headers, json={"title": "B"}).json()["contact"]
    other.patch(path, headers={"X-CSRF-Token": other_token}, json={"title": "C"})
    stale = client.patch(path, headers=headers, json={"title": "A", "expected_revision": redone["revision"]})
    assert stale.status_code == 409
    assert (stale.json()["error"], stale.json()["contact"]["title"]) == ("This contact changed in another tab.", "C")
    assert client.patch(path, headers=headers, json={"title": "x", "expected_revision": "2"}).status_code == 400


def test_company_writes_reject_unknown_companies(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        contact = client.post(
            "/api/v1/contacts",
            headers={"X-CSRF-Token": csrf},
            json={"company": "Unknown Example", "name": "Ada Example"},
        )
        note = client.put(
            "/api/v1/companies/note",
            headers={"X-CSRF-Token": csrf},
            json={"company": "Unknown Example", "body": "Should not be stored."},
        )
    finally:
        client.close()

    assert contact.status_code == 400
    assert contact.json()["error"] == "company not found"
    assert note.status_code == 400
    assert note.json()["error"] == "company not found"


def _company(client, name):
    return client.get("/api/v1/companies", params={"name": name}).json()["company"]


def _index(client):
    return {item["key"]: item for item in client.get("/api/v1/companies").json()["companies"]}


def _capture(client, headers, company, slug):
    return client.post("/api/v1/capture", headers=headers, json={
        "url": f"https://example.org/jobs/{slug}", "company": company, "title": f"{slug} intern",
    })


def test_company_writes_need_auth_and_csrf(dashboard):
    base, _ = dashboard
    for method, path in [
        ("POST", "/api/v1/companies"), ("PATCH", "/api/v1/companies/datadog"),
        ("POST", "/api/v1/companies/datadog/hide"), ("POST", "/api/v1/companies/datadog/restore"),
        ("POST", "/api/v1/companies/datadog/merge"), ("POST", "/api/v1/company-merges/1/undo"),
    ]:
        assert httpx.request(method, f"{base}{path}", json={"name": "x"}, timeout=2).status_code == 401
        assert httpx.request(method, f"{base}{path}", json={"name": "x"}, headers=AUTH, timeout=2).status_code == 403
    client, token = authenticated_client(base)
    assert client.post("/api/v1/companies/datadog/archive", headers={"X-CSRF-Token": token}).status_code == 404


def test_companies_are_created_by_hand_before_any_job(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    for body, error in [
        ({"name": "  "}, "A company needs a name. Add one and save again."),
        ({}, "A company needs a name. Add one and save again."),
        ({"name": "x" * 501}, "Company name must be under 500 characters"),
        ({"name": "Acme", "notes": ""}, "notes can't be edited"),
    ]:
        response = client.post("/api/v1/companies", headers=headers, json=body)
        assert (response.status_code, response.json()["error"]) == (400, error)

    created = client.post("/api/v1/companies", headers=headers, json={"name": " Anthropic "})
    assert created.status_code == 201
    assert created.json()["created"] is True
    assert created.json()["company"]["name"] == "Anthropic"
    listed = _index(client)["anthropic"]
    assert (listed["name"], listed["manual"], listed["postings"]) == ("Anthropic", True, [])
    contact = client.post("/api/v1/contacts", headers=headers, json={"company": "Anthropic, Inc.", "name": "Ada"})
    assert contact.status_code == 201
    note = client.put("/api/v1/companies/note", headers=headers, json={"company": "Anthropic", "body": "Met at a meetup."})
    assert note.status_code == 200
    detail = _company(client, "Anthropic")
    assert [item["name"] for item in detail["contacts"]] == ["Ada"]
    assert detail["note"]["body"] == "Met at a meetup."

    again = client.post("/api/v1/companies", headers=headers, json={"name": "Datadog, Inc."})
    assert again.status_code == 200
    assert (again.json()["created"], again.json()["company"]["key"]) == (False, "datadog")

    # a later scout posting joins it
    assert _capture(client, headers, "Anthropic PBC", "research").status_code == 201
    assert _capture(client, headers, "Anthropic", "platform").status_code == 201
    company = _company(client, "Anthropic")
    assert sorted(job["title"] for job in company["postings"]) == ["platform intern"]
    assert company["contacts"][0]["name"] == "Ada"

    client.post("/api/v1/companies/anthropic/hide", headers=headers)
    hidden = client.post("/api/v1/companies", headers=headers, json={"name": "anthropic"})
    assert (hidden.status_code, hidden.json()["error"]) == (400, "Anthropic is hidden. Show hidden companies to restore it.")


def test_a_rename_survives_later_scout_results_and_undoes_by_revision(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    other, other_token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    path = "/api/v1/companies/datadog"
    for body, status, error in [
        ({"name": ""}, 400, "A company needs a name. Add one and save again."),
        ({"name": "Datadog", "expected_revision": "1"}, 400, "expected_revision must be a whole number"),
        ({"name": "Cloudflare, Inc."}, 400, "Cloudflare is already a company. Merge them instead."),
    ]:
        response = client.patch(path, headers=headers, json=body)
        assert (response.status_code, response.json()["error"]) == (status, error)
    assert client.patch("/api/v1/companies/nobody", headers=headers, json={"name": "x"}).status_code == 404

    renamed = client.patch(path, headers=headers, json={"name": "Datadog Labs"}).json()
    assert (renamed["company"]["name"], renamed["previous"]["name"]) == ("Datadog Labs", "Datadog")
    job = next(job for job in client.get("/api/v1/jobs").json()["jobs"] if job["dedupe_key"] == "demo:2")
    assert (job["company"], job["posting_company"], job["company_key"]) == ("Datadog Labs", "Datadog", "datadog")
    assert _company(client, "Datadog Labs")["key"] == _company(client, "Datadog")["key"] == "datadog"

    # old and new names both land
    _capture(client, headers, "Datadog, Inc.", "logs")
    _capture(client, headers, "Datadog Labs", "metrics")
    company = _company(client, "Datadog")
    assert company["name"] == "Datadog Labs"
    assert sorted(job["title"] for job in company["postings"]) == [
        "Cloud Platform Engineering Intern", "logs intern", "metrics intern",
    ]
    assert "datadog labs" not in _index(client)

    undo = client.patch(path, headers=headers, json={"name": "Datadog", "expected_revision": renamed["company"]["revision"]})
    assert undo.json()["company"]["name"] == "Datadog"
    redo = client.patch(path, headers=headers, json={"name": "Datadog Labs"}).json()
    other.patch(path, headers={"X-CSRF-Token": other_token}, json={"name": "Datadog Cloud"})
    stale = client.patch(path, headers=headers, json={"name": "Datadog", "expected_revision": redo["company"]["revision"]})
    assert stale.status_code == 409
    assert (stale.json()["error"], stale.json()["company"]["name"]) == ("This company changed in another tab.", "Datadog Cloud")


def test_hiding_a_company_keeps_its_jobs(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    hidden = client.post("/api/v1/companies/datadog/hide", headers=headers)
    assert hidden.json()["company"]["hidden"] is True
    assert _index(client)["datadog"]["hidden"] is True
    assert "demo:2" in [job["dedupe_key"] for job in client.get("/api/v1/jobs").json()["jobs"]]
    assert client.post("/api/v1/companies/datadog/hide", headers=headers).status_code == 404
    assert client.post("/api/v1/companies/datadog/restore", headers=headers).json()["company"]["hidden"] is False
    assert client.post("/api/v1/companies/datadog/restore", headers=headers).status_code == 404
    assert client.post("/api/v1/companies/nobody/hide", headers=headers).status_code == 404
    assert _index(client)["datadog"]["hidden"] is False


def test_a_merge_moves_everything_and_undoes_exactly(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    client.put("/api/v1/companies/note", headers=headers, json={"company": "Cloudflare", "body": "Target notes."})
    client.put("/api/v1/companies/note", headers=headers, json={"company": "Datadog", "body": "Source notes."})
    contact = client.post("/api/v1/contacts", headers=headers, json={"company": "Datadog", "name": "Grace"}).json()["contact"]
    client.post("/api/v1/connections/import", headers=headers, json={"contacts": [{
        "company": "Datadog", "name": "Lin", "linkedin_url": "https://www.linkedin.com/in/lin",
    }]})
    before = _index(client)
    assert (before["datadog"]["contacts_count"], before["datadog"]["has_note"]) == (2, True)
    merge = "/api/v1/companies/datadog/merge"
    for body, status in [({}, 400), ({"target": "datadog"}, 400), ({"target": "nobody"}, 404)]:
        assert client.post(merge, headers=headers, json=body).status_code == status

    merged = client.post(merge, headers=headers, json={"target": "cloudflare"}).json()
    assert merged["moved"] == {"contacts": 2, "links": 0}
    assert merged["company"]["name"] == "Cloudflare"
    assert (merged["source"]["key"], merged["source"]["revision"]) == ("datadog", 0)
    index = _index(client)
    assert "datadog" not in index
    assert index["cloudflare"]["aliases"] == ["Datadog"]
    assert "datadog" in index["cloudflare"]["match_keys"]
    target = _company(client, "Cloudflare")
    assert sorted(job["dedupe_key"] for job in target["postings"]) == ["demo:1", "demo:2"]
    assert sorted(item["name"] for item in target["contacts"]) == ["Grace", "Lin"]
    assert target["note"]["body"] == "Target notes.\n\nFrom Datadog:\nSource notes."
    assert target["connections_count"] == 1
    assert _company(client, "Datadog")["key"] == "cloudflare"
    jobs = {job["dedupe_key"]: job for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert (jobs["demo:2"]["company"], jobs["demo:2"]["connections_count"]) == ("Cloudflare", 1)
    # later scout results follow it
    _capture(client, headers, "Datadog", "apm")
    assert "apm intern" in [job["title"] for job in _company(client, "Cloudflare")["postings"]]
    added = client.post("/api/v1/contacts", headers=headers, json={"company": "Datadog", "name": "Late"}).json()["contact"]
    assert added["company_key"] == "cloudflare"
    assert client.post(merge, headers=headers, json={"target": "cloudflare"}).status_code == 404

    undo = f"/api/v1/company-merges/{merged['merge_id']}/undo"
    undone = client.post(undo, headers=headers, json={"expected_revision": merged["company"]["revision"]})
    assert undone.status_code == 200
    assert undone.json()["company"]["name"] == "Datadog"
    source, target = _company(client, "Datadog"), _company(client, "Cloudflare")
    assert source["note"]["body"] == "Source notes." and target["note"]["body"] == "Target notes."
    assert sorted(item["name"] for item in source["contacts"]) == ["Grace", "Lin"]
    assert [item["name"] for item in target["contacts"]] == ["Late"]
    assert sorted(job["dedupe_key"] for job in target["postings"]) == ["demo:1"]
    assert contact["id"] in [item["id"] for item in source["contacts"]]
    assert client.post(undo, headers=headers, json={}).status_code == 409
    assert client.post("/api/v1/company-merges/99/undo", headers=headers, json={}).status_code == 404
    assert client.post("/api/v1/company-merges/x/undo", headers=headers, json={}).status_code == 400


def test_hidden_companies_cannot_be_merged(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    client.post("/api/v1/companies/datadog/hide", headers=headers)
    for path, target in [("/api/v1/companies/cloudflare/merge", "datadog"), ("/api/v1/companies/datadog/merge", "cloudflare")]:
        response = client.post(path, headers=headers, json={"target": target})
        assert (response.status_code, response.json()["error"]) == (400, "Datadog is hidden. Show it in Companies before merging.")
    assert set(_index(client)) >= {"datadog", "cloudflare"}


def test_a_renamed_source_stays_merged_and_notes_keep_their_heading(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    client.put("/api/v1/companies/note", headers=headers, json={"company": "Datadog", "body": "Source notes."})
    source_note = _company(client, "Datadog")["note"]
    client.patch("/api/v1/companies/datadog", headers=headers, json={"name": "Datadog Labs"})
    merged = client.post("/api/v1/companies/datadog/merge", headers=headers, json={"target": "cloudflare"}).json()
    assert _company(client, "Cloudflare")["note"]["body"] == "From Datadog Labs:\nSource notes."
    _capture(client, headers, "Datadog Labs", "metrics")
    assert set(_index(client)) == {"cloudflare"}
    assert "metrics intern" in [job["title"] for job in _company(client, "Datadog Labs")["postings"]]

    client.post(f"/api/v1/company-merges/{merged['merge_id']}/undo", headers=headers, json={})
    source = _company(client, "Datadog Labs")
    assert source["key"] == "datadog" and source["note"] == source_note
    assert sorted(job["title"] for job in source["postings"]) == ["Cloud Platform Engineering Intern", "metrics intern"]
    assert _company(client, "Cloudflare")["note"]["body"] == ""


def test_a_stale_merge_undo_cannot_clobber_later_changes(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    other, other_token = authenticated_client(base)
    headers, other_headers = {"X-CSRF-Token": token}, {"X-CSRF-Token": other_token}
    merge = lambda: client.post("/api/v1/companies/datadog/merge", headers=headers, json={"target": "cloudflare"}).json()
    undo = lambda merged, **body: client.post(f"/api/v1/company-merges/{merged['merge_id']}/undo", headers=headers, json=body)

    merged = merge()
    other.put("/api/v1/companies/note", headers=other_headers, json={"company": "Cloudflare", "body": "Typed later."})
    stale = undo(merged)
    assert (stale.status_code, stale.json()["error"]) == (409, "Its notes changed after the merge, so it can't be undone.")
    assert _company(client, "Datadog")["key"] == "cloudflare"

    client.post("/api/v1/companies", headers=headers, json={"name": "Acme"})
    merged = client.post("/api/v1/companies/acme/merge", headers=headers, json={"target": "cloudflare"}).json()
    other.patch("/api/v1/companies/cloudflare", headers=other_headers, json={"name": "Cloudflare Labs"})
    stale = undo(merged, expected_revision=merged["company"]["revision"])
    assert (stale.status_code, stale.json()["error"]) == (409, "This company changed after the merge, so it can't be undone.")
    assert undo(merged, expected_revision="x").status_code == 400


def test_company_links_are_added_edited_removed_and_restored(dashboard):
    base, _ = dashboard
    client, token = authenticated_client(base)
    other, other_token = authenticated_client(base)
    headers = {"X-CSRF-Token": token}
    add = "/api/v1/companies/datadog/links"
    assert httpx.post(f"{base}{add}", json={"url": "https://x.example"}, timeout=2).status_code == 401
    assert client.post(add, json={"url": "https://x.example"}).status_code == 403
    for body, error in [
        ({}, "A link needs a URL. Add one and save again."),
        ({"url": "http://careers.example"}, "Link URL must be an https:// address under 2,000 characters"),
        ({"url": "javascript:alert(1)"}, "Link URL must be an https:// address under 2,000 characters"),
        ({"url": "https://"}, "Link URL must be an https:// address under 2,000 characters"),
        ({"url": "https://x.example", "label": "x" * 201}, "Link label must be text under 200 characters"),
        ({"url": "https://x.example", "kind": "blog"}, "kind can't be edited"),
    ]:
        response = client.post(add, headers=headers, json=body)
        assert (response.status_code, response.json()["error"]) == (400, error)
    assert client.post("/api/v1/companies/nobody/links", headers=headers, json={"url": "https://x.example"}).status_code == 404

    added = client.post(add, headers=headers, json={"label": " Careers ", "url": " https://careers.datadoghq.example "})
    assert added.status_code == 201
    link = added.json()["link"]
    assert (link["label"], link["url"]) == ("Careers", "https://careers.datadoghq.example")
    assert _company(client, "Datadog")["saved_links"] == [link]
    assert _index(client)["datadog"]["saved_links_count"] == 1

    path = f"/api/v1/company-links/{link['id']}"
    assert client.patch(path, json={"label": "x"}).status_code == 403
    assert client.patch(path, headers=headers, json={}).json()["error"] == "Change the label or URL, then save"
    assert client.patch(path, headers=headers, json={"url": "ftp://x"}).status_code == 400
    assert client.patch("/api/v1/company-links/99", headers=headers, json={"label": "x"}).status_code == 404
    assert client.delete("/api/v1/company-links/abc", headers=headers).status_code == 400
    edited = client.patch(path, headers=headers, json={"label": "Jobs"}).json()
    assert (edited["link"]["label"], edited["previous"]["label"]) == ("Jobs", "Careers")
    other.patch(path, headers={"X-CSRF-Token": other_token}, json={"label": "Other tab"})
    stale = client.patch(path, headers=headers, json={"label": "Careers", "expected_revision": edited["link"]["revision"]})
    assert stale.status_code == 409
    assert (stale.json()["error"], stale.json()["link"]["label"]) == ("This link changed in another tab.", "Other tab")

    assert client.delete(path, headers=headers).json() == {"ok": True}
    assert _company(client, "Datadog")["saved_links"] == []
    assert client.delete(path, headers=headers).status_code == 404
    assert client.post(f"{path}/restore", headers=headers).json()["link"]["label"] == "Other tab"
    assert client.post(f"{path}/restore", headers=headers).status_code == 404

    # links follow a merge and its undo
    merged = client.post("/api/v1/companies/datadog/merge", headers=headers, json={"target": "cloudflare"}).json()
    assert merged["moved"]["links"] == 1
    assert [item["id"] for item in _company(client, "Cloudflare")["saved_links"]] == [link["id"]]
    client.post(f"/api/v1/company-merges/{merged['merge_id']}/undo", headers=headers, json={})
    assert [item["id"] for item in _company(client, "Datadog")["saved_links"]] == [link["id"]]
    assert _company(client, "Cloudflare")["saved_links"] == []


def test_inbox_rules_archive_tag_boost_and_undo(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        archived_rule = client.post(
            "/api/v1/rules", headers={"X-CSRF-Token": csrf},
            json={"name": "Hide platform roles", "kind": "archive_title", "pattern": "Cloud Platform"},
        )
        tag_rule = client.post(
            "/api/v1/rules", headers={"X-CSRF-Token": csrf},
            json={"name": "Systems roles", "kind": "tag_title", "pattern": "Platform", "value": "Systems"},
        )
        boost_rule = client.post(
            "/api/v1/rules", headers={"X-CSRF-Token": csrf},
            json={"name": "Prefer Datadog", "kind": "boost_company", "pattern": "Datadog", "value": "25"},
        )
        jobs = client.get("/api/v1/jobs").json()["jobs"]
        datadog = next(job for job in jobs if job["company"] == "Datadog")
        rules = client.get("/api/v1/rules")
        undone = client.post(
            f"/api/v1/rule-actions/{datadog['rule_action_id']}/undo",
            headers={"X-CSRF-Token": csrf}, json={},
        )
        restored = next(
            job for job in client.get("/api/v1/jobs").json()["jobs"] if job["company"] == "Datadog"
        )
        paused = client.put(
            f"/api/v1/rules/{tag_rule.json()['rule']['id']}",
            headers={"X-CSRF-Token": csrf}, json={"enabled": False},
        )
        archive_id = archived_rule.json()["rule"]["id"]
        client.put(
            f"/api/v1/rules/{archive_id}", headers={"X-CSRF-Token": csrf}, json={"enabled": False},
        )
        client.put(
            f"/api/v1/rules/{archive_id}", headers={"X-CSRF-Token": csrf}, json={"enabled": True},
        )
        after_reenable = next(
            job for job in client.get("/api/v1/jobs").json()["jobs"] if job["company"] == "Datadog"
        )
    finally:
        client.close()

    assert archived_rule.status_code == 201
    assert boost_rule.status_code == 201
    assert datadog["status"] == "archived"
    assert datadog["archived_by_rule"] == "Hide platform roles"
    assert datadog["tags"] == ["Systems"]
    assert datadog["rule_boost"] == 25
    assert len(rules.json()["rules"]) == 3
    assert undone.status_code == 200
    assert restored["status"] == "new"
    assert paused.json()["rule"]["enabled"] is False
    assert after_reenable["status"] == "new"


def _rule(client, csrf, **fields):
    response = client.post("/api/v1/rules", headers={"X-CSRF-Token": csrf}, json=fields)
    assert response.status_code == 201
    return response.json()["rule"]


def test_rules_are_edited_deleted_restored_and_reordered(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    try:
        tag = _rule(client, csrf, name="Systems roles", kind="tag_title", pattern="Platform", value="Systems")
        boost = _rule(client, csrf, name="Prefer Datadog", kind="boost_company", pattern="Datadog", value="25")
        edited = client.patch(f"/api/v1/rules/{tag['id']}", headers=headers,
                              json={"value": "Infra", "expected_revision": tag["revision"]})
        jobs = client.get("/api/v1/jobs").json()["jobs"]
        undone = client.patch(f"/api/v1/rules/{tag['id']}", headers=headers,
                              json={"value": "Systems", "expected_revision": edited.json()["rule"]["revision"]})
        archive = client.patch(f"/api/v1/rules/{tag['id']}", headers=headers,
                               json={"kind": "archive_title", "pattern": "Cloud Platform"})
        archived = next(job for job in client.get("/api/v1/jobs").json()["jobs"] if job["company"] == "Datadog")
        reordered = client.put("/api/v1/rules/order", headers=headers, json={"ids": [boost["id"], tag["id"]], "expected_revision": 0})
        listed = client.get("/api/v1/rules").json()
        ordered = [rule["id"] for rule in listed["rules"]]
        deleted = client.delete(f"/api/v1/rules/{boost['id']}", headers=headers)
        after_delete = client.get("/api/v1/rules").json()["rules"]
        boosted = next(job for job in client.get("/api/v1/jobs").json()["jobs"] if job["company"] == "Datadog")
        again = client.delete(f"/api/v1/rules/{boost['id']}", headers=headers)
        restored = client.post(f"/api/v1/rules/{boost['id']}/restore", headers=headers)
        restored_again = client.post(f"/api/v1/rules/{boost['id']}/restore", headers=headers)
        final = client.get("/api/v1/rules").json()["rules"]
    finally:
        client.close()

    assert edited.status_code == 200
    assert edited.json()["previous"]["value"] == "Systems"
    assert next(job for job in jobs if job["company"] == "Datadog")["tags"] == ["Infra"]
    assert undone.json()["rule"]["value"] == "Systems"
    assert archive.json()["rule"]["value"] == ""
    assert archived["status"] == "archived"
    assert archived["archived_by_rule"] == "Systems roles"
    assert reordered.json() == {"ids": [boost["id"], tag["id"]], "order_revision": 1}
    assert ordered == [boost["id"], tag["id"]]
    assert listed["order_revision"] == 1
    assert deleted.status_code == 200
    assert [rule["id"] for rule in after_delete] == [tag["id"]]
    assert boosted["rule_boost"] == 0
    assert again.status_code == 404
    assert restored.status_code == 200
    assert restored_again.status_code == 404
    assert [rule["id"] for rule in final] == [boost["id"], tag["id"]]


def test_rule_runs_skip_hidden_jobs(dashboard):
    base, store = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    try:
        client.post("/api/v1/hidden", headers=headers, json={"dedupe_keys": ["demo:2"]})
        rule = _rule(client, csrf, name="Platform", kind="archive_title", pattern="Nothing")
        client.patch(f"/api/v1/rules/{rule['id']}", headers=headers, json={"pattern": "Platform"})
    finally:
        client.close()
    assert next(row for row in store.rows if row["dedupe_key"] == "demo:2")["status"] == "new"


def test_rule_writes_check_auth_csrf_input_and_staleness(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    try:
        rule = _rule(client, csrf, name="Systems roles", kind="tag_title", pattern="Platform", value="Systems")
        path = f"/api/v1/rules/{rule['id']}"
        no_auth = httpx.patch(f"{base}{path}", json={"name": "x"}, timeout=2)
        no_csrf = client.patch(path, json={"name": "x"})
        no_csrf_delete = client.delete(path)
        no_csrf_order = client.put("/api/v1/rules/order", json={"ids": [rule["id"]], "expected_revision": 0})
        unknown = client.patch(path, headers=headers, json={"enabled": False})
        blank = client.patch(path, headers=headers, json={"pattern": " "})
        empty = client.patch(path, headers=headers, json={})
        not_text = client.patch(path, headers=headers, json={"name": 5})
        bad_value = client.patch(path, headers=headers, json={"kind": "boost_company", "value": "lots"})
        missing = client.patch("/api/v1/rules/999", headers=headers, json={"name": "x"})
        bad_id = client.delete("/api/v1/rules/nope", headers=headers)
        moved = client.patch(path, headers=headers, json={"name": "Infra roles"})
        stale = client.patch(path, headers=headers, json={"name": "Systems roles", "expected_revision": rule["revision"]})
        partial = client.put("/api/v1/rules/order", headers=headers, json={"ids": [], "expected_revision": 0})
        repeated = client.put("/api/v1/rules/order", headers=headers, json={"ids": [rule["id"], rule["id"]], "expected_revision": 0})
        not_ids = client.put("/api/v1/rules/order", headers=headers, json={"ids": ["1"], "expected_revision": 0})
        no_revision = client.put("/api/v1/rules/order", headers=headers, json={"ids": [rule["id"]]})
    finally:
        client.close()

    assert no_auth.status_code == 401
    assert no_csrf.status_code == 403
    assert no_csrf_delete.status_code == 403
    assert no_csrf_order.status_code == 403
    assert unknown.status_code == 400
    assert blank.status_code == 400
    assert empty.status_code == 400
    assert not_text.status_code == 400
    assert bad_value.status_code == 400
    assert missing.status_code == 404
    assert bad_id.status_code == 400
    assert moved.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["rule"]["name"] == "Infra roles"
    assert partial.status_code == 409
    assert repeated.status_code == 400
    assert not_ids.status_code == 400
    assert no_revision.status_code == 400


def test_a_stale_tab_cannot_reorder_or_undo_over_a_newer_rule_order(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    try:
        first = _rule(client, csrf, name="First", kind="tag_title", pattern="Intern", value="One")
        second = _rule(client, csrf, name="Second", kind="tag_title", pattern="Cloud", value="Two")
        ids = [first["id"], second["id"]]
        seen = client.get("/api/v1/rules").json()["order_revision"]
        # tab a moves, tab b moves back
        moved = client.put("/api/v1/rules/order", headers=headers, json={"ids": ids[::-1], "expected_revision": seen})
        other = client.put("/api/v1/rules/order", headers=headers, json={"ids": ids, "expected_revision": moved.json()["order_revision"]})
        stale_undo = client.put("/api/v1/rules/order", headers=headers, json={"ids": ids, "expected_revision": moved.json()["order_revision"]})
        stale_move = client.put("/api/v1/rules/order", headers=headers, json={"ids": ids[::-1], "expected_revision": seen})
        final = client.get("/api/v1/rules").json()
    finally:
        client.close()

    assert moved.status_code == 200
    assert other.status_code == 200
    assert stale_undo.status_code == 409
    assert stale_move.status_code == 409
    assert [rule["id"] for rule in final["rules"]] == ids
    assert final["order_revision"] == other.json()["order_revision"]


def _datadog(client):
    return next(job for job in client.get("/api/v1/jobs").json()["jobs"] if job["company"] == "Datadog")


def test_manual_tags_sit_beside_rule_tags_and_survive_reruns(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    try:
        rule = _rule(client, csrf, name="Systems roles", kind="tag_title", pattern="Platform", value="Systems")
        added = client.post("/api/v1/job-tags", headers=headers,
                            json={"dedupe_keys": ["demo:1", "demo:2"], "tag": "  Dream   job "})
        again = client.post("/api/v1/job-tags", headers=headers, json={"dedupe_keys": ["demo:1", "demo:2"], "tag": "dream JOB"})
        same_as_rule = client.post("/api/v1/job-tags", headers=headers, json={"dedupe_keys": ["demo:2"], "tag": "systems"})
        tagged = _datadog(client)
        client.put(f"/api/v1/rules/{rule['id']}", headers=headers, json={"enabled": False})
        client.put(f"/api/v1/rules/{rule['id']}", headers=headers, json={"enabled": True})
        client.patch(f"/api/v1/rules/{rule['id']}", headers=headers, json={"value": "Infra"})
        rerun = _datadog(client)
        removed = client.post("/api/v1/job-tags", headers=headers,
                              json={"dedupe_keys": ["demo:1", "demo:2"], "tag": "Dream job", "remove": True})
        rule_only = client.post("/api/v1/job-tags", headers=headers,
                                json={"dedupe_keys": ["demo:2"], "tag": "Infra", "remove": True})
        after = _datadog(client)
        restored = client.post("/api/v1/job-tags/undo", headers=headers, json={"ids": removed.json()["ids"], "remove": False})
        final = _datadog(client)
    finally:
        client.close()

    assert added.status_code == 200
    assert added.json() == {"tag": "Dream job", "remove": False, "changed": ["demo:1", "demo:2"], "ids": [1, 2]}
    assert again.json()["changed"] == []
    assert same_as_rule.json()["changed"] == ["demo:2"]
    assert tagged["rule_tags"] == ["Systems"]
    assert tagged["manual_tags"] == ["Dream job", "systems"]
    assert tagged["tags"] == ["Systems", "Dream job"]
    assert rerun["manual_tags"] == ["Dream job", "systems"]
    assert rerun["tags"] == ["Infra", "Dream job", "systems"]
    assert removed.json()["changed"] == ["demo:1", "demo:2"]
    assert rule_only.json()["changed"] == []
    assert after["tags"] == ["Infra", "systems"]
    assert restored.json() == {"changed": ["demo:1", "demo:2"], "ids": [1, 2]}
    assert final["manual_tags"] == ["Dream job", "systems"]


def test_a_stale_tag_undo_leaves_a_newer_hand_tag_alone(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    tag = {"dedupe_keys": ["demo:2"], "tag": "Dream"}
    try:
        added = client.post("/api/v1/job-tags", headers=headers, json=tag).json()
        # another tab removes it and adds it back
        removed = client.post("/api/v1/job-tags", headers=headers, json={**tag, "remove": True}).json()
        readded = client.post("/api/v1/job-tags", headers=headers, json=tag).json()
        stale_undo_add = client.post("/api/v1/job-tags/undo", headers=headers, json={"ids": added["ids"], "remove": True})
        stale_undo_remove = client.post("/api/v1/job-tags/undo", headers=headers, json={"ids": removed["ids"], "remove": False})
        kept = _datadog(client)["manual_tags"]
        undo_readd = client.post("/api/v1/job-tags/undo", headers=headers, json={"ids": readded["ids"], "remove": True})
        gone = _datadog(client)["manual_tags"]
    finally:
        client.close()

    assert readded["ids"] != added["ids"]
    assert stale_undo_add.status_code == 409
    assert stale_undo_remove.status_code == 409
    assert kept == ["Dream"]
    assert undo_readd.json() == {"changed": ["demo:2"], "ids": readded["ids"]}
    assert gone == []


def test_tag_writes_check_auth_csrf_input_and_hidden_jobs(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    body = {"dedupe_keys": ["demo:1"], "tag": "Later"}
    try:
        no_auth = httpx.post(f"{base}/api/v1/job-tags", json=body, timeout=2)
        no_csrf = client.post("/api/v1/job-tags", json=body)
        blank = client.post("/api/v1/job-tags", headers=headers, json={**body, "tag": "  "})
        long = client.post("/api/v1/job-tags", headers=headers, json={**body, "tag": "x" * 41})
        no_keys = client.post("/api/v1/job-tags", headers=headers, json={"dedupe_keys": [], "tag": "Later"})
        bad_remove = client.post("/api/v1/job-tags", headers=headers, json={**body, "remove": "yes"})
        missing = client.post("/api/v1/job-tags", headers=headers, json={"dedupe_keys": ["demo:1", "demo:404"], "tag": "Later"})
        client.post("/api/v1/hidden", headers=headers, json={"dedupe_keys": ["demo:2"]})
        hidden = client.post("/api/v1/job-tags", headers=headers, json={"dedupe_keys": ["demo:1", "demo:2"], "tag": "Later"})
        untouched = client.get("/api/v1/jobs").json()["jobs"][0]
        bad_ids = client.post("/api/v1/job-tags/undo", headers=headers, json={"ids": ["1"], "remove": True})
        bad_undo_remove = client.post("/api/v1/job-tags/undo", headers=headers, json={"ids": [1], "remove": "yes"})
        no_csrf_undo = client.post("/api/v1/job-tags/undo", json={"ids": [1], "remove": True})
        unknown = client.post("/api/v1/job-tags/undo", headers=headers, json={"ids": [999], "remove": True})
    finally:
        client.close()
    client, csrf = authenticated_client(base)
    try:
        client.post("/api/v1/hidden/undo", headers={"X-CSRF-Token": csrf}, json={"ids": [1]})
        tagged = client.post("/api/v1/job-tags", headers={"X-CSRF-Token": csrf}, json={"dedupe_keys": ["demo:2"], "tag": "Later"}).json()
        client.post("/api/v1/hidden", headers={"X-CSRF-Token": csrf}, json={"dedupe_keys": ["demo:2"]})
        hidden_undo = client.post("/api/v1/job-tags/undo", headers={"X-CSRF-Token": csrf}, json={"ids": tagged["ids"], "remove": True})
    finally:
        client.close()

    assert no_auth.status_code == 401
    assert no_csrf.status_code == 403
    assert blank.status_code == 400
    assert long.status_code == 400
    assert no_keys.status_code == 400
    assert bad_remove.status_code == 400
    assert missing.status_code == 404
    assert hidden.status_code == 410
    assert untouched["manual_tags"] == []
    assert bad_ids.status_code == 400
    assert bad_undo_remove.status_code == 400
    assert no_csrf_undo.status_code == 403
    assert unknown.status_code == 404
    assert hidden_undo.status_code == 410


def test_tracker_and_company_writes_require_csrf(dashboard):
    base, _ = dashboard
    interview = httpx.post(
        f"{base}/api/v1/interviews", headers=AUTH,
        json={"dedupe_key": "demo:1", "starts_at": "2026-10-10T14:00:00Z"}, timeout=2,
    )
    contact = httpx.post(
        f"{base}/api/v1/contacts", headers=AUTH,
        json={"company": "Cloudflare", "name": "Example"}, timeout=2,
    )
    connections = httpx.post(
        f"{base}/api/v1/connections/import", headers=AUTH, json={"contacts": []}, timeout=2,
    )
    note = httpx.put(
        f"{base}/api/v1/companies/note", headers=AUTH,
        json={"company": "Cloudflare", "body": "test"}, timeout=2,
    )
    next_step = httpx.put(
        f"{base}/api/v1/applications/demo%3A1/next-step", headers=AUTH,
        json={"next_step": "test"}, timeout=2,
    )
    rule = httpx.post(
        f"{base}/api/v1/rules", headers=AUTH,
        json={"name": "Nope", "kind": "archive_title", "pattern": "Senior"}, timeout=2,
    )
    assert {interview.status_code, contact.status_code, connections.status_code,
            note.status_code, next_step.status_code, rule.status_code} == {403}


def test_saved_views_round_trip_on_server_with_csrf(dashboard):
    base, _ = dashboard
    client, csrf = authenticated_client(base)
    try:
        created = client.post(
            "/api/v1/saved-views",
            headers={"X-CSRF-Token": csrf},
            json={
                "name": "Remote new",
                "filters": {"status": "new", "remote": True},
                "sort": "newest",
                "pinned": True,
            },
        )
        views = client.get("/api/v1/saved-views")
        view_id = created.json()["saved_view"]["id"]
        deleted = client.delete(
            f"/api/v1/saved-views/{view_id}",
            headers={"X-CSRF-Token": csrf},
        )
    finally:
        client.close()
    assert created.status_code == 201
    assert views.json()["saved_views"][0]["name"] == "Remote new"
    assert deleted.status_code == 200


def test_profile_save_from_a_stale_revision_is_a_conflict(quick_fill_dashboard):
    base, _ = quick_fill_dashboard
    client, csrf = authenticated_client(base)
    first_tab = client.get("/api/v1/profile").json()["profile"]
    second_tab = client.get("/api/v1/profile").json()["profile"]
    second_tab["fields"][0]["value"] = "Second tab"
    landed = client.put("/api/v1/profile", headers={"X-CSRF-Token": csrf}, json={"profile": second_tab})
    first_tab["fields"][0]["value"] = "First tab"
    stale = client.put("/api/v1/profile", headers={"X-CSRF-Token": csrf}, json={"profile": first_tab})
    unversioned = client.put("/api/v1/profile", headers={"X-CSRF-Token": csrf}, json={"profile": {"fields": []}})

    assert landed.status_code == 200
    assert landed.json()["profile"]["revision"] == first_tab["revision"] + 1
    assert stale.status_code == 409
    assert stale.json()["profile"]["fields"][0]["value"] == "Second tab"
    assert stale.json()["profile"]["revision"] == landed.json()["profile"]["revision"]
    assert unversioned.status_code == 400
    assert client.get("/api/v1/profile").json()["profile"]["fields"][0]["value"] == "Second tab"


def test_profile_lists_the_fields_eligibility_reads(quick_fill_dashboard):
    base, _ = quick_fill_dashboard
    client, csrf = authenticated_client(base)
    profile = client.get("/api/v1/profile").json()["profile"]
    profile["fields"].reverse()
    saved = client.put("/api/v1/profile", headers={"X-CSRF-Token": csrf}, json={"profile": profile}).json()["profile"]

    assert profile["eligibility_checks"]["work_authorization"] == ["sponsorship"]
    assert set(profile["eligibility_checks"]) >= {"clearance", "degree", "graduation"}
    assert saved["eligibility_checks"] == profile["eligibility_checks"]
    assert [row["key"] for row in saved["fields"]] == [row["key"] for row in profile["fields"]]


def test_template_rename_and_restore_keep_job_overrides(quick_fill_dashboard):
    base, _ = quick_fill_dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    job = {"job": "demo:2", "company": "Datadog"}
    profile = client.get("/api/v1/profile").json()["profile"]
    name = profile["answer_templates"][0]["name"]
    client.put("/api/v1/profile/context", headers=headers, json={"dedupe_key": "demo:2", "company": "Datadog", "answer_overrides": {name: "Datadog answer"}, "company_account": {}})

    profile["answer_templates"][0]["name"] = "Why Datadog"
    renamed = client.put("/api/v1/profile", headers=headers, json={"profile": profile}).json()["profile"]
    after_rename = client.get("/api/v1/profile", params=job).json()["profile"]["answer_overrides"]
    template = renamed["answer_templates"].pop()
    deleted = client.put("/api/v1/profile", headers=headers, json={"profile": renamed}).json()["profile"]
    deleted["answer_templates"].append({"name": template["name"], "body": template["body"]})
    client.put("/api/v1/profile", headers=headers, json={"profile": deleted})
    after_restore = client.get("/api/v1/profile", params=job).json()["profile"]

    assert after_rename == {"Why Datadog": "Datadog answer"}
    assert [row["name"] for row in after_restore["answer_templates"]] == ["Why Datadog"]
    assert after_restore["answer_overrides"] == {"Why Datadog": "Datadog answer"}


def test_apply_session_offers_the_default_document(quick_fill_dashboard):
    base, store = quick_fill_dashboard
    client, csrf = authenticated_client(base)
    headers = {"X-CSRF-Token": csrf}
    profile = client.get("/api/v1/profile").json()["profile"]
    profile["documents"].append({"name": "Platform resume", "default": True})
    saved = client.put("/api/v1/profile", headers=headers, json={"profile": profile}).json()["profile"]
    session = client.post("/api/v1/queue/session", headers=headers).json()

    added = saved["documents"][-1]
    assert added["id"] and added["default"] is True
    assert [row["name"] for row in session["documents"] if row["default"]] == ["Platform resume"]
    assert sum(row["default"] for row in saved["documents"]) == 1
