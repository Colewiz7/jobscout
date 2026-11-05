"""The on-demand scout run: Job spec, refusals and the HTTP boundary."""
import datetime as dt
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from jobscout import db as database
from jobscout import scan_runs
from jobscout.dashboard import DashboardServer, DemoStore
from jobscout.scan_runs import FakeLauncher, KubernetesLauncher, ScanBusy, ScanTooSoon, ScanUnavailable

from test_dashboard import AUTH, authenticated_client
from test_db import conn  # noqa: F401 - schema reset fixture

NOW = dt.datetime(2026, 11, 5, 15, 0, tzinfo=dt.timezone.utc)

CRONJOB = {
    "metadata": {"name": "scout", "uid": "uid-1"},
    "spec": {
        "schedule": "17 */6 * * *",
        "timeZone": "America/New_York",
        "jobTemplate": {
            "metadata": {"labels": {"team": "x"}},
            "spec": {
                "backoffLimit": 2,
                "template": {"spec": {"containers": [{
                    "name": "scout",
                    "image": "img",
                    "args": ["run"],
                    "env": [
                        {"name": "DATABASE_URL", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}},
                        {"name": "JOBSCOUT_RUN_TRIGGER", "value": "scheduled"},
                    ],
                }]}},
            },
        },
    },
}


def job(name, *, finished=False, manual=False, age_minutes=60, owner="scout"):
    created = NOW - dt.timedelta(minutes=age_minutes)
    meta = {
        "name": name,
        "creationTimestamp": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ownerReferences": [{"kind": "CronJob", "name": owner}],
    }
    if manual:
        meta["annotations"] = {scan_runs.INSTANTIATE: "manual"}
    status = {"conditions": [{"type": "Complete", "status": "True"}]} if finished else {"active": 1}
    return {"metadata": meta, "status": status}


class FakeKube:
    """A bare Kubernetes API: one CronJob, a job list, recorded creates."""

    def __init__(self):
        self.jobs = []
        self.cronjob = CRONJOB
        self.created = []
        self.create_status = 201
        self.list_status = 200
        self.lookup_status = None
        self.drop_create_reply = False
        self.record_rejected = False
        self.create_statuses = []
        self.list_delay = 0.0
        self.after_list = None
        self.seen_auth = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.seen_auth.append(self.headers.get("Authorization"))
                if self.path.endswith("/cronjobs/scout"):
                    self._send(200, outer.cronjob)
                elif self.path.endswith("/jobs"):
                    time.sleep(outer.list_delay)
                    items = list(outer.jobs)
                    if outer.after_list:
                        outer.after_list()
                    self._send(outer.list_status, {"items": items, "secret": "internal detail"})
                elif "/jobs/" in self.path:
                    wanted = self.path.rsplit("/", 1)[1]
                    known = outer.jobs + [body for _, body in outer.created]
                    found = next((j for j in known if j["metadata"]["name"] == wanted), None)
                    if outer.lookup_status:
                        self._send(outer.lookup_status, {})
                    elif found is None:
                        self._send(404, {})
                    else:
                        self._send(200, found)
                else:
                    self._send(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                status = outer.create_statuses.pop(0) if outer.create_statuses else outer.create_status
                if status < 400 or outer.record_rejected:
                    outer.created.append((self.path, body))
                if outer.drop_create_reply:
                    # created, but the reply is lost
                    self.close_connection = True
                    return
                self._send(status, {"message": "internal detail from the api"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def kube():
    try:
        fake = FakeKube()
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    yield fake
    fake.close()


@pytest.fixture
def launcher(kube):
    made = KubernetesLauncher(
        base_url=kube.url, token="tok", namespace="ns", cronjob="scout", verify=False, clock=lambda: NOW,
    )
    yield made


# --- job spec ---

def test_the_job_copies_the_template_and_is_owned_by_the_cronjob():
    built = scan_runs.build_job(CRONJOB, "scout-manual-x")
    owner = built["metadata"]["ownerReferences"][0]
    assert owner == {"apiVersion": "batch/v1", "kind": "CronJob", "name": "scout", "uid": "uid-1", "controller": True}
    assert built["metadata"]["annotations"]["cronjob.kubernetes.io/instantiate"] == "manual"
    assert built["metadata"]["labels"] == {"team": "x", "app.kubernetes.io/part-of": "jobscout-manual"}
    assert built["spec"]["backoffLimit"] == 0
    assert built["spec"]["activeDeadlineSeconds"] == 3600
    container = built["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "img" and container["args"] == ["run"]
    env = {item["name"]: item for item in container["env"]}
    assert env["DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "s"
    assert env["JOBSCOUT_RUN_TRIGGER"]["value"] == "manual"
    assert env["JOBSCOUT_JOB_NAME"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.labels['job-name']"
    assert [i["name"] for i in container["env"]].count("JOBSCOUT_RUN_TRIGGER") == 1


def test_building_a_job_leaves_the_cronjob_untouched():
    scan_runs.build_job(CRONJOB, "scout-manual-x")
    assert CRONJOB["spec"]["jobTemplate"]["spec"]["backoffLimit"] == 2


def test_names_are_unique_valid_and_short():
    import re

    names = {scan_runs.manual_job_name("scout", NOW) for _ in range(200)}
    assert len(names) == 200
    assert all(re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name) and len(name) <= 52 for name in names)
    assert all(name.startswith("scout-manual-") for name in names)


def test_a_long_cronjob_name_is_trimmed_to_fit():
    name = scan_runs.manual_job_name("a-very-long-cronjob-name-" * 3, NOW)
    assert len(name) <= 52 and "manual" in name and not name.startswith("-")


# --- launcher against the fake API ---

def test_start_creates_one_job_with_the_service_token(launcher, kube):
    started = launcher.start()
    assert started["trigger"] == "manual"
    (path, body), = kube.created
    assert path == "/apis/batch/v1/namespaces/ns/jobs"
    assert body["spec"]["backoffLimit"] == 0
    assert set(kube.seen_auth) == {"Bearer tok"}


def test_an_active_scheduled_job_refuses_the_start(launcher, kube):
    kube.jobs = [job("scout-1", finished=False)]
    with pytest.raises(ScanBusy):
        launcher.start()
    assert kube.created == []


def test_an_active_manual_job_refuses_the_start(launcher, kube):
    kube.jobs = [job("scout-manual-1", manual=True, age_minutes=3)]
    with pytest.raises(ScanBusy):
        launcher.start()


def test_other_cronjobs_active_jobs_do_not_block(launcher, kube):
    kube.jobs = [job("other-1", owner="other")]
    launcher.start()
    assert len(kube.created) == 1


def test_a_recent_manual_job_hits_the_cooldown(launcher, kube):
    kube.jobs = [job("scout-manual-1", manual=True, finished=True, age_minutes=5)]
    with pytest.raises(ScanTooSoon) as caught:
        launcher.start()
    assert 9 * 60 <= caught.value.retry_after <= 10 * 60
    assert kube.created == []


def test_an_old_manual_job_does_not(launcher, kube):
    kube.jobs = [job("scout-manual-1", manual=True, finished=True, age_minutes=16)]
    launcher.start()
    assert len(kube.created) == 1


def test_a_finished_manual_job_in_the_same_half_hour_does_not_block_a_start(launcher, kube):
    kube.jobs = [job("scout-manual-old", manual=True, finished=True, age_minutes=16)]
    launcher.start()
    (_, body), = kube.created
    assert body["metadata"]["name"] != "scout-manual-old"


def test_two_starts_get_different_job_names(launcher, kube):
    launcher.start()
    launcher.start()
    assert kube.created[0][1]["metadata"]["name"] != kube.created[1][1]["metadata"]["name"]


def test_a_create_conflict_reports_busy(launcher, kube):
    kube.create_status = 409
    with pytest.raises(ScanBusy):
        launcher.start()


def test_an_api_error_is_unavailable_without_its_body(launcher, kube):
    kube.create_status = 403
    with pytest.raises(ScanUnavailable) as caught:
        launcher.start()
    assert "internal detail" not in str(caught.value)


def test_an_unreachable_api_fails_closed(kube):
    dead = KubernetesLauncher(
        base_url="http://127.0.0.1:1", token="t", namespace="ns", cronjob="scout", verify=False, timeout=0.5,
    )
    with pytest.raises(ScanUnavailable):
        dead.start()
    with pytest.raises(ScanUnavailable):
        dead.status()


def test_status_reports_activity_and_the_next_scheduled_run(launcher, kube):
    kube.jobs = [job("scout-1")]
    status = launcher.status()
    assert status["active"] is True
    # 10:00 in New York, so the next 12:17 slot is 17:17 UTC
    assert status["next_scheduled_at"] == dt.datetime(2026, 11, 5, 17, 17, tzinfo=dt.timezone.utc)


def test_cron_next_handles_lists_ranges_and_bad_input():
    start = dt.datetime(2026, 11, 5, 4, 0, tzinfo=dt.timezone.utc)
    assert scan_runs.next_scheduled("30 9 * * *", "UTC", start) == dt.datetime(2026, 11, 5, 9, 30, tzinfo=dt.timezone.utc)
    assert scan_runs.next_scheduled("0 8-10/2 * * *", "UTC", start) == dt.datetime(2026, 11, 5, 8, 0, tzinfo=dt.timezone.utc)
    assert scan_runs.next_scheduled("nonsense", "UTC", start) is None
    assert scan_runs.next_scheduled("0 0 * * *", "Nowhere/Land", start) is None


def test_from_env_needs_all_the_in_cluster_files(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    assert KubernetesLauncher.from_env(tmp_path) is None
    (tmp_path / "token").write_text("t\n")
    (tmp_path / "namespace").write_text("ns\n")
    assert KubernetesLauncher.from_env(tmp_path) is None


# --- HTTP boundary ---

class StubLauncher:
    def __init__(self, error=None):
        self.error = error
        self.starts = 0
        self.state = {"active": False, "last_manual_started": None, "next_scheduled_at": None}

    def status(self):
        if isinstance(self.error, ScanUnavailable):
            raise self.error
        return self.state

    def new_name(self):
        return "stub-manual-1"

    def job_state(self, name):
        return "missing"

    def start(self, name=None):
        self.starts += 1
        if self.error:
            raise self.error
        return {"trigger": "manual", "state": "running", "started_at": NOW}


@pytest.fixture
def scan_dashboard():
    made = []

    def build(launcher, store=None):
        store = store or DemoStore()
        try:
            server = DashboardServer(("127.0.0.1", 0), store, require_auth=True,
                                     authentik_user="cole", scan_launcher=launcher)
        except PermissionError:
            pytest.skip("the test sandbox does not permit a loopback listener")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        made.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}", store

    yield build
    for server, thread in made:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_the_session_reports_the_feature_only_when_a_launcher_exists(scan_dashboard):
    on, _ = scan_dashboard(StubLauncher())
    off, _ = scan_dashboard(None)
    assert authenticated_client(on)[0].get("/api/v1/session").json()["features"]["scan_run"] is True
    assert authenticated_client(off)[0].get("/api/v1/session").json()["features"]["scan_run"] is False


def test_a_start_returns_202_and_the_run(scan_dashboard):
    stub = StubLauncher()
    base, _ = scan_dashboard(stub)
    client, csrf = authenticated_client(base)
    response = client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202
    assert response.json()["run"]["trigger"] == "manual"
    assert stub.starts == 1


def test_a_start_with_a_body_is_refused_and_starts_nothing(scan_dashboard):
    stub = StubLauncher()
    base, _ = scan_dashboard(stub)
    client, csrf = authenticated_client(base)
    response = client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf},
                           json={"image": "evil", "args": ["rm"], "name": "x"})
    assert response.status_code == 400
    assert stub.starts == 0


def test_a_start_needs_csrf_same_origin_and_auth(scan_dashboard):
    stub = StubLauncher()
    base, _ = scan_dashboard(stub)
    client, csrf = authenticated_client(base)
    assert client.post("/api/v1/scan-runs").status_code == 403
    assert client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"}).status_code == 403
    assert httpx.post(base + "/api/v1/scan-runs").status_code in {401, 403}
    assert httpx.post(base + "/api/v1/scan-runs", headers={"X-CSRF-Token": csrf}).status_code in {401, 403}
    assert stub.starts == 0


def test_a_busy_start_is_409(scan_dashboard):
    base, _ = scan_dashboard(StubLauncher(ScanBusy("x")))
    client, csrf = authenticated_client(base)
    response = client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409
    assert "error" in response.json()


def test_a_cooldown_start_is_429_with_retry_after(scan_dashboard):
    base, _ = scan_dashboard(StubLauncher(ScanTooSoon(420)))
    client, csrf = authenticated_client(base)
    response = client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "420"
    assert response.json()["retry_after"] == 420


def test_a_cluster_failure_is_a_generic_503(scan_dashboard):
    base, _ = scan_dashboard(StubLauncher(ScanUnavailable("10.1.2.3 refused: token=abc")))
    client, csrf = authenticated_client(base)
    response = client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503
    assert "10.1.2.3" not in response.text and "abc" not in response.text


def test_the_routes_are_absent_when_the_feature_is_off(scan_dashboard):
    base, _ = scan_dashboard(None)
    client, csrf = authenticated_client(base)
    assert client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf}).status_code == 404
    assert client.get("/api/v1/scan-runs").status_code == 404


def test_the_overview_lists_runs_with_safe_fields_only(scan_dashboard):
    stub = StubLauncher()
    base, store = scan_dashboard(stub)
    store._scout_runs.append({
        "id": 1, "trigger": "scheduled", "started_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
        "finished_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=40),
        "exit_code": 0, "new_rows": 4, "notified": 2, "job_name": "pod-secret-name",
    })
    client, _ = authenticated_client(base)
    body = client.get("/api/v1/scan-runs").json()
    assert body["running"] is False and body["status_known"] is True
    (run,) = body["runs"]
    assert run["state"] == "ok" and run["new_rows"] == 4
    assert "job_name" not in run and "pod-secret-name" not in json.dumps(body)


def test_the_overview_survives_an_unreachable_cluster(scan_dashboard):
    stub = StubLauncher(ScanUnavailable("down"))
    base, store = scan_dashboard(stub)
    store.claim_manual_scan("manual-x")
    client, _ = authenticated_client(base)
    body = client.get("/api/v1/scan-runs").json()
    assert body["status_known"] is False
    assert body["running"] is True


def test_an_old_unfinished_row_is_not_running():
    old = {"id": 1, "trigger": "scheduled", "started_at": NOW - dt.timedelta(hours=5), "finished_at": None}
    assert scan_runs.public_run(old, NOW)["state"] == "failed"
    assert scan_runs.overview([old], None, NOW)["running"] is False


def test_a_failed_exit_code_reads_as_failed():
    row = {"id": 1, "trigger": "manual", "started_at": NOW, "finished_at": NOW, "exit_code": 5}
    assert scan_runs.public_run(row, NOW)["state"] == "failed"


def test_the_cooldown_comes_from_the_newest_manual_row(scan_dashboard):
    now = dt.datetime.now(dt.timezone.utc)
    row = {"id": 1, "trigger": "manual", "started_at": now - dt.timedelta(minutes=3), "finished_at": now,
           "exit_code": 0, "job_created_at": now - dt.timedelta(minutes=3)}
    result = scan_runs.overview([row], {"active": False, "last_manual_started": None, "next_scheduled_at": None}, now)
    assert result["cooldown_until"] == row["started_at"] + scan_runs.COOLDOWN


# --- fake launcher and demo store ---

def test_the_fake_launcher_runs_then_finishes_and_records_in_the_demo_store():
    clock = [NOW]
    store = DemoStore()
    fake = FakeLauncher(store, duration=60, clock=lambda: clock[0])
    assert "id" in store.claim_manual_scan(fake.new_name())
    fake.start()
    assert fake.status()["active"] is True
    assert store.scout_runs()[0]["finished_at"] is None
    with pytest.raises(ScanBusy):
        fake.start()
    clock[0] += dt.timedelta(seconds=61)
    assert fake.status()["active"] is False
    assert store.scout_runs()[0]["exit_code"] == 0
    with pytest.raises(ScanTooSoon):
        fake.start()
    clock[0] += dt.timedelta(minutes=16)
    fake.start()


# --- claims, cooldown and races ---

@pytest.fixture
def kube_dashboard(scan_dashboard, kube):
    made = KubernetesLauncher(base_url=kube.url, token="tok", namespace="ns", cronjob="scout", verify=False)
    base, store = scan_dashboard(made)
    client, csrf = authenticated_client(base)
    return client, {"X-CSRF-Token": csrf}, store, kube


def test_pruned_jobs_cannot_reset_the_cooldown(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    assert kube.jobs == []
    recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    store._scout_runs.append({"id": 1, "trigger": "manual", "started_at": recent, "finished_at": recent,
                              "exit_code": 0, "new_rows": 0, "notified": 0, "job_created_at": recent})
    response = client.post("/api/v1/scan-runs", headers=headers)
    assert response.status_code == 429
    assert 9 * 60 <= int(response.headers["Retry-After"]) <= 10 * 60 + 2
    assert kube.created == []


def test_an_old_manual_row_does_not_block(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    store._scout_runs.append({"id": 1, "trigger": "manual", "started_at": old, "finished_at": old,
                              "exit_code": 0, "new_rows": 0, "notified": 0, "job_created_at": old})
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 202


def test_two_simultaneous_starts_make_one_job(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    base = str(client.base_url)
    results, barrier = [], threading.Barrier(2)

    def click():
        other = httpx.Client(base_url=base, headers={**AUTH, **headers}, timeout=5)
        other.cookies.update(client.cookies)
        barrier.wait()
        results.append(other.post("/api/v1/scan-runs").status_code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [202, 409]
    assert len(kube.created) == 1


def test_a_failed_create_releases_the_claim(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    kube.create_status = 403
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 503
    assert store.scout_runs() == []
    kube.create_status = 201
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 202


def _open_claim(store, name, minutes=30):
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    store._scout_runs.append({"id": 1, "trigger": "manual", "started_at": old, "finished_at": None,
                              "exit_code": None, "new_rows": None, "notified": None, "job_name": name,
                              "job_created_at": None, "note": None})


def test_a_claim_with_no_job_behind_it_is_cleared_and_not_counted(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    _open_claim(store, "scout-manual-gone")
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 202
    assert store._scout_runs[0]["exit_code"] == 1
    assert store._scout_runs[0]["job_created_at"] is None


def test_a_claim_with_a_live_job_stays_busy(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    _open_claim(store, "scout-manual-live")
    kube.jobs = [job("scout-manual-live", manual=True, age_minutes=30)]
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 409
    assert kube.created == []


def test_another_jobs_activity_does_not_keep_a_dead_claim_open(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    _open_claim(store, "scout-manual-gone")
    kube.jobs = [job("scout-9", manual=False, age_minutes=2)]
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 409
    kube.jobs = []
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 202


def test_a_claim_whose_job_finished_is_closed_and_still_counts(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    _open_claim(store, "scout-manual-done", minutes=20)
    kube.jobs = [job("scout-manual-done", manual=True, finished=True, age_minutes=20)]
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 202
    assert store._scout_runs[0]["finished_at"] is not None
    assert store._scout_runs[0]["job_created_at"] is not None


@pytest.fixture
def quick_kube(scan_dashboard, kube):
    made = KubernetesLauncher(base_url=kube.url, token="tok", namespace="ns", cronjob="scout", verify=False, timeout=2)
    base, store = scan_dashboard(made)
    client, csrf = authenticated_client(base)
    return client, {"X-CSRF-Token": csrf}, store, kube


def test_a_lost_create_reply_for_a_job_that_exists_keeps_the_claim_and_answers_202(quick_kube):
    client, headers, store, kube = quick_kube
    kube.drop_create_reply = True
    response = client.post("/api/v1/scan-runs", headers=headers)
    assert response.status_code == 202 and response.json()["run"]["id"] == 1
    (row,) = store._scout_runs
    assert row["finished_at"] is None and row["job_created_at"] is not None


def test_a_lost_reply_and_a_failed_lookup_keep_the_claim(quick_kube):
    client, headers, store, kube = quick_kube
    kube.drop_create_reply = True
    kube.lookup_status = 500
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 503
    (row,) = store._scout_runs
    assert row["finished_at"] is None and row["job_created_at"] is None


def test_a_server_error_reply_keeps_the_claim_when_the_job_is_unknown(quick_kube):
    client, headers, store, kube = quick_kube
    kube.create_status = 500
    kube.lookup_status = 503
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 503
    assert len(store._scout_runs) == 1


def test_a_definite_rejection_with_no_job_releases_the_claim(quick_kube):
    client, headers, store, kube = quick_kube
    kube.create_status = 422
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 503
    assert store._scout_runs == []


def test_a_rejection_that_still_made_a_job_is_treated_as_started(quick_kube):
    client, headers, store, kube = quick_kube
    kube.create_status = 400
    kube.record_rejected = True
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 202
    assert store._scout_runs[0]["job_created_at"] is not None


def test_a_slow_list_does_not_let_two_starts_through(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    kube.list_delay = 0.4
    base = str(client.base_url)
    results, barrier = [], threading.Barrier(2)

    def click():
        other = httpx.Client(base_url=base, headers={**AUTH, **headers}, timeout=10)
        other.cookies.update(client.cookies)
        barrier.wait()
        results.append(other.post("/api/v1/scan-runs").status_code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [202, 409]
    assert len(kube.created) == 1


def test_a_scheduled_tick_during_the_create_is_the_known_gap(kube_dashboard):
    # Forbid and the scout lock cover this window
    client, headers, store, kube = kube_dashboard
    kube.after_list = lambda: kube.jobs.append(job("scout-9", manual=False, age_minutes=0))
    assert client.post("/api/v1/scan-runs", headers=headers).status_code == 202
    kube.after_list = None
    assert client.get("/api/v1/scan-runs").json()["running"] is True


DSN = os.environ.get("JOBSCOUT_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")
def test_a_start_before_the_migration_fails_closed(scan_dashboard, conn):
    from jobscout.dashboard import PostgresStore

    with conn.cursor() as cur:
        cur.execute("delete from schema_migrations where version = 31")
    conn.commit()
    stub = StubLauncher()
    base, _ = scan_dashboard(stub, store=PostgresStore(DSN))
    client, csrf = authenticated_client(base)
    response = client.post("/api/v1/scan-runs", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503
    assert stub.starts == 0
    assert "migrat" not in response.text.lower()


def test_a_skipped_manual_run_is_not_reported_as_a_success():
    row = {"id": 1, "trigger": "manual", "started_at": NOW, "finished_at": NOW, "exit_code": 0,
           "note": scan_runs.SKIPPED_NOTE}
    assert scan_runs.public_run(row, NOW)["state"] == "skipped"
    assert scan_runs.public_run(row, NOW)["note"] == scan_runs.SKIPPED_NOTE
    assert scan_runs.public_run({**row, "note": None}, NOW)["state"] == "ok"
    other = scan_runs.public_run({**row, "note": "something else <b>"}, NOW)
    assert other["state"] == "ok" and "note" not in other


@pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")
def test_the_postgres_guard_holds_until_released(conn):
    import psycopg

    from jobscout.dashboard import PostgresStore

    store = PostgresStore(DSN)
    with store.scan_guard():
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with store.scan_guard(timeout=0.3):
                pass
    with store.scan_guard(timeout=1):
        pass


def test_the_demo_guard_holds_until_released():
    store = DemoStore()
    with store.scan_guard():
        with pytest.raises(TimeoutError):
            with store.scan_guard(timeout=0.2):
                pass
    with store.scan_guard(timeout=0.2):
        pass


def test_a_second_start_waits_for_the_first_to_finish_its_create(kube_dashboard):
    client, headers, store, kube = kube_dashboard
    kube.list_delay = 0.4
    kube.create_statuses = [403]
    base = str(client.base_url)
    results, barrier = [], threading.Barrier(2)

    def click():
        other = httpx.Client(base_url=base, headers={**AUTH, **headers}, timeout=10)
        other.cookies.update(client.cookies)
        barrier.wait()
        results.append(other.post("/api/v1/scan-runs").status_code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # the loser of the first create frees the way, so the other one goes through
    assert sorted(results) == [202, 503]


def test_a_reservation_without_a_job_shows_no_cooldown_in_the_overview():
    now = dt.datetime.now(dt.timezone.utc)
    row = {"id": 1, "trigger": "manual", "started_at": now - dt.timedelta(minutes=2), "finished_at": now,
           "exit_code": 1, "job_created_at": None}
    idle = {"active": False, "last_manual_started": None, "next_scheduled_at": None}
    assert scan_runs.overview([row], idle, now)["cooldown_until"] is None
    counted = {**row, "job_created_at": row["started_at"]}
    assert scan_runs.overview([counted], idle, now)["cooldown_until"] == row["started_at"] + scan_runs.COOLDOWN


def test_the_guard_waits_only_a_few_seconds_by_default():
    import inspect

    from jobscout.dashboard import PostgresStore

    for store in (PostgresStore, DemoStore):
        assert inspect.signature(store.scan_guard).parameters["timeout"].default <= 5


@pytest.mark.skipif(not DSN, reason="JOBSCOUT_TEST_DSN not set")
def test_the_guard_keeps_its_lock_without_an_open_transaction(conn):
    from jobscout.dashboard import PostgresStore

    store = PostgresStore(DSN)
    with store.scan_guard():
        with conn.cursor() as cur:
            cur.execute("select pg_try_advisory_lock(%s) as got", (database.MANUAL_GUARD_LOCK_ID,))
            assert cur.fetchone()["got"] is False
            cur.execute("select count(*) as n from pg_stat_activity where state = 'idle in transaction' and pid <> pg_backend_pid()")
            assert cur.fetchone()["n"] == 0
        conn.commit()
