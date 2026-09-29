"""The dashboard is a small HTTP boundary, so exercise it as one."""
import threading

import httpx
import pytest

from jobscout.dashboard import DashboardServer, DemoStore, load_profile


@pytest.fixture
def dashboard():
    store = DemoStore()
    try:
        server = DashboardServer(
            ("127.0.0.1", 0), store,
            {"name": "Cole", "email": "cole@example.com", "highlights": []},
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


def test_default_profile_is_valid_and_keeps_phone_empty():
    profile = load_profile()
    assert profile["name"] == "Cole Wisniewski"
    assert profile["phone"] == ""
    assert profile["highlights"]


def test_dashboard_serves_shell_jobs_and_security_headers(dashboard):
    base, _ = dashboard
    shell = httpx.get(base, timeout=2)
    jobs = httpx.get(f"{base}/api/jobs", timeout=2)

    assert shell.status_code == 200
    assert "application desk" in shell.text
    assert "default-src 'self'" in shell.headers["content-security-policy"]
    assert jobs.status_code == 200
    assert len(jobs.json()["jobs"]) == 5


def test_application_state_round_trips(dashboard):
    base, _ = dashboard
    response = httpx.patch(
        f"{base}/api/jobs/demo%3A2",
        json={"status": "applied", "notes": "Submitted on Friday"},
        timeout=2,
    )
    jobs = httpx.get(f"{base}/api/jobs", timeout=2).json()["jobs"]
    changed = next(job for job in jobs if job["dedupe_key"] == "demo:2")

    assert response.status_code == 200
    assert changed["status"] == "applied"
    assert changed["notes"] == "Submitted on Friday"


def test_application_state_rejects_unknown_status(dashboard):
    base, _ = dashboard
    response = httpx.patch(
        f"{base}/api/jobs/demo%3A2",
        json={"status": "maybe", "notes": ""},
        timeout=2,
    )
    assert response.status_code == 400


def test_application_state_rejects_cross_origin_write(dashboard):
    base, _ = dashboard
    response = httpx.patch(
        f"{base}/api/jobs/demo%3A2",
        headers={"Origin": "https://attacker.example"},
        json={"status": "saved", "notes": ""},
        timeout=2,
    )
    assert response.status_code == 403
