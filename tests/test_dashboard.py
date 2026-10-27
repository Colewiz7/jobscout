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
    client.post("/api/v1/hidden", json={"dedupe_keys": ["demo:1"]}, headers=headers)
    writes = [
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
    ]
    for method, path, body in writes:
        response = client.request(method, path, json=body, headers=headers)
        assert (path, response.status_code) == (path, 410)
        assert response.json()["error"] == "This job is hidden, so it can't change."
    row = store.rows[0]
    assert (row["status"], row["notes"], row.get("next_step", "")) == ("applied", "Mention the homelab incident response story.", "")
    assert client.patch("/api/v1/jobs/demo%3A2", json={"status": "saved"}, headers=headers).status_code == 200


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
