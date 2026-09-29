"""The dashboard is a small HTTP boundary, so exercise it as one."""
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
    assert response.status_code == 404


def test_authenticated_jobs_round_trip(dashboard):
    base, _ = dashboard
    with httpx.Client(base_url=base, headers=AUTH, timeout=2) as client:
        response = client.get("/api/v1/jobs")
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 2


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
