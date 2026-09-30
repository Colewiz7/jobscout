"""The dashboard is a small HTTP boundary, so exercise it as one."""
import json
import pathlib
import threading

import httpx
import pytest

from jobscout.dashboard import DashboardServer, DemoStore, load_profile

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


def test_dashboard_serves_shell_and_history_routes(dashboard):
    base, _ = dashboard
    shell = httpx.get(f"{base}/inbox", timeout=2)
    nested = httpx.get(f"{base}/queue/session/example", timeout=2)

    assert shell.status_code == 200
    assert "Search or run a command" in shell.text
    assert "default-src 'self'" in shell.headers["content-security-policy"]
    assert nested.status_code == 200
    assert nested.text == shell.text


def test_v1_api_requires_authentik(dashboard):
    base, _ = dashboard
    response = httpx.get(f"{base}/api/v1/jobs", timeout=2)
    assert response.status_code == 401
    assert "Authentik" in response.json()["error"]


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
    assert response.status_code == 404
    assert write.status_code == 404
    assert context.status_code == 404
    assert copied.status_code == 404


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


def test_queue_writes_require_csrf(dashboard):
    base, _ = dashboard
    reorder = httpx.put(
        f"{base}/api/v1/queue/order",
        headers=AUTH,
        json={"dedupe_keys": ["demo:1"]},
        timeout=2,
    )
    session = httpx.post(f"{base}/api/v1/queue/session", headers=AUTH, timeout=2)
    applied = httpx.post(
        f"{base}/api/v1/applications/demo%3A1/applied",
        headers=AUTH,
        json={"document_id": None},
        timeout=2,
    )
    assert reorder.status_code == 403
    assert session.status_code == 403
    assert applied.status_code == 403


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
