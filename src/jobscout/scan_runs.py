"""Start a scout run on demand without giving the dashboard any new power.

The dashboard never takes a field from the client. It copies the scheduled
CronJob's own template into a Job, so a manual run is exactly a scheduled one.
"""
from __future__ import annotations

import copy
import datetime as dt
import logging
import os
import pathlib
import secrets
import ssl
import zoneinfo
from typing import Protocol

import httpx

from .db import SKIPPED_NOTE

log = logging.getLogger(__name__)

COOLDOWN = dt.timedelta(minutes=15)
# pod names need room after this
MAX_NAME = 52
NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
DEADLINE_SECONDS = 3600
TRIGGER_ENV = "JOBSCOUT_RUN_TRIGGER"
JOB_NAME_ENV = "JOBSCOUT_JOB_NAME"
MANUAL_LABEL = "app.kubernetes.io/part-of"
MANUAL_LABEL_VALUE = "jobscout-manual"
INSTANTIATE = "cronjob.kubernetes.io/instantiate"

SERVICE_DIR = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount")


class ScanError(Exception):
    """Base for refusals the route turns into plain JSON."""


class ScanBusy(ScanError):
    """A scout Job is already running."""


class ScanTooSoon(ScanError):
    def __init__(self, retry_after: int):
        super().__init__("too soon")
        self.retry_after = max(1, int(retry_after))


class ScanUnavailable(ScanError):
    """The cluster could not be reached or refused us."""


class ScanUncertain(ScanUnavailable):
    """The Job may exist, so keep the reservation."""


class Launcher(Protocol):
    def status(self) -> dict:
        """Return active, last_manual_started and next_scheduled_at."""

    def new_name(self) -> str:
        """A fresh, unique Job name."""

    def job_state(self, name: str | None) -> str:
        """missing, active, finished or unknown."""

    def start(self, name: str | None = None) -> dict:
        """Start one manual run, or raise a ScanError."""


def _base36(number: int) -> str:
    digits = ""
    while number:
        number, rest = divmod(number, 36)
        digits = NAME_CHARS[rest] + digits
    return digits or "0"


def manual_job_name(cronjob: str, now: dt.datetime) -> str:
    """Unique per start; the active-Job check is what refuses doubles."""
    suffix = "".join(secrets.choice(NAME_CHARS) for _ in range(4))
    tail = f"-manual-{_base36(int(now.timestamp()))}{suffix}"
    head = cronjob.lower().replace(".", "-").replace("_", "-")[: MAX_NAME - len(tail)].rstrip("-")
    return head + tail


def build_job(cronjob: dict, name: str) -> dict:
    """Turn a CronJob into a one-off Job; nothing here comes from a client."""
    meta = cronjob["metadata"]
    template = copy.deepcopy(cronjob["spec"]["jobTemplate"])
    template_meta = template.get("metadata") or {}
    spec = template["spec"]
    spec["backoffLimit"] = 0
    spec["activeDeadlineSeconds"] = DEADLINE_SECONDS
    for container in spec["template"]["spec"]["containers"]:
        env = [
            item for item in container.get("env") or []
            if item.get("name") not in {TRIGGER_ENV, JOB_NAME_ENV}
        ]
        env.append({"name": TRIGGER_ENV, "value": "manual"})
        env.append({
            "name": JOB_NAME_ENV,
            "valueFrom": {"fieldRef": {"fieldPath": "metadata.labels['job-name']"}},
        })
        container["env"] = env
    labels = dict(template_meta.get("labels") or {})
    labels[MANUAL_LABEL] = MANUAL_LABEL_VALUE
    annotations = dict(template_meta.get("annotations") or {})
    annotations[INSTANTIATE] = "manual"
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "labels": labels,
            "annotations": annotations,
            # counted by the CronJob's concurrency policy
            "ownerReferences": [{
                "apiVersion": "batch/v1",
                "kind": "CronJob",
                "name": meta["name"],
                "uid": meta["uid"],
                "controller": True,
            }],
        },
        "spec": spec,
    }


def _parse_time(value) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _owned_by(job: dict, cronjob: str) -> bool:
    owners = (job.get("metadata") or {}).get("ownerReferences") or []
    return any(o.get("kind") == "CronJob" and o.get("name") == cronjob for o in owners)


def _finished(job: dict) -> bool:
    conditions = (job.get("status") or {}).get("conditions") or []
    return any(c.get("type") in {"Complete", "Failed"} and c.get("status") == "True" for c in conditions)


def summarize_jobs(jobs: list[dict], cronjob: str) -> tuple[bool, dt.datetime | None]:
    """Return whether a scout Job is running and when the last manual one began."""
    active, last_manual = False, None
    for job in jobs:
        if not _owned_by(job, cronjob):
            continue
        if not _finished(job):
            active = True
        meta = job.get("metadata") or {}
        if (meta.get("annotations") or {}).get(INSTANTIATE) == "manual":
            started = _parse_time(meta.get("creationTimestamp"))
            if started and (last_manual is None or started > last_manual):
                last_manual = started
    return active, last_manual


def _cron_values(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, _, raw = part.partition("/")
            step = int(raw)
            if step < 1:
                raise ValueError("bad step")
        if part in {"*", ""}:
            start, end = low, high
        elif "-" in part:
            first, _, last = part.partition("-")
            start, end = int(first), int(last)
        else:
            start = int(part)
            end = high if step > 1 else start
        if start < low or end > high or start > end:
            raise ValueError("out of range")
        values.update(range(start, end + 1, step))
    return values


def next_scheduled(schedule: str, zone: str | None, now: dt.datetime) -> dt.datetime | None:
    """The next fire time of a plain five-field schedule, or None if unreadable."""
    try:
        fields = schedule.split()
        if len(fields) != 5:
            return None
        minutes = _cron_values(fields[0], 0, 59)
        hours = _cron_values(fields[1], 0, 23)
        days = _cron_values(fields[2], 1, 31)
        months = _cron_values(fields[3], 1, 12)
        weekdays = {d % 7 for d in _cron_values(fields[4], 0, 7)}
        tz = zoneinfo.ZoneInfo(zone) if zone else dt.timezone.utc
    except (ValueError, zoneinfo.ZoneInfoNotFoundError):
        return None
    day_any, week_any = fields[2] == "*", fields[4] == "*"
    moment = now.astimezone(tz).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    for _ in range(60 * 24 * 8):
        weekday = (moment.weekday() + 1) % 7
        if day_any or week_any:
            day_ok = moment.day in days and weekday in weekdays
        else:
            day_ok = moment.day in days or weekday in weekdays
        if (moment.month in months and day_ok and moment.hour in hours
                and moment.minute in minutes):
            return moment.astimezone(dt.timezone.utc)
        moment += dt.timedelta(minutes=1)
    return None


class KubernetesLauncher:
    """In-cluster launcher using the pod's own service account."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        namespace: str,
        cronjob: str = "jobscout",
        verify: str | bool = True,
        clock=None,
        timeout: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.namespace = namespace
        self.cronjob = cronjob
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            verify=verify,
            timeout=timeout,
        )

    @classmethod
    def from_env(cls, directory: pathlib.Path = SERVICE_DIR) -> "KubernetesLauncher | None":
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "").strip()
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443").strip()
        try:
            token = (directory / "token").read_text().strip()
            namespace = (directory / "namespace").read_text().strip()
        except OSError:
            return None
        ca = directory / "ca.crt"
        if not host or not token or not namespace or not ca.is_file():
            return None
        if ":" in host:
            host = f"[{host}]"
        return cls(
            base_url=f"https://{host}:{port}",
            token=token,
            namespace=namespace,
            cronjob=os.environ.get("JOBSCOUT_SCOUT_CRONJOB", "jobscout").strip() or "jobscout",
            verify=ssl.create_default_context(cafile=str(ca)),
        )

    def _path(self, resource: str, name: str = "") -> str:
        base = f"/apis/batch/v1/namespaces/{self.namespace}/{resource}"
        return f"{self.base_url}{base}/{name}" if name else f"{self.base_url}{base}"

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            # the cause names internal hosts
            log.warning("cluster request failed: %s", type(error).__name__)
            raise ScanUnavailable("cluster unreachable") from None

    def _cronjob(self) -> dict:
        response = self._request("GET", self._path("cronjobs", self.cronjob))
        if response.status_code != 200:
            log.warning("cronjob read returned %s", response.status_code)
            raise ScanUnavailable("cronjob unavailable")
        try:
            return response.json()
        except ValueError:
            raise ScanUnavailable("bad cronjob reply") from None

    def job_state(self, name: str | None) -> str:
        if not name:
            return "unknown"
        try:
            response = self._client.request("GET", self._path("jobs", name))
            if response.status_code == 404:
                return "missing"
            if response.status_code != 200:
                return "unknown"
            return "finished" if _finished(response.json()) else "active"
        except (httpx.HTTPError, ValueError):
            return "unknown"

    def _jobs(self) -> list[dict]:
        response = self._request("GET", self._path("jobs"))
        if response.status_code != 200:
            log.warning("job list returned %s", response.status_code)
            raise ScanUnavailable("jobs unavailable")
        try:
            return list(response.json().get("items") or [])
        except (ValueError, AttributeError):
            raise ScanUnavailable("bad job reply") from None

    def status(self) -> dict:
        active, last_manual = summarize_jobs(self._jobs(), self.cronjob)
        upcoming = None
        try:
            spec = self._cronjob().get("spec") or {}
            if not spec.get("suspend"):
                upcoming = next_scheduled(
                    str(spec.get("schedule") or ""), spec.get("timeZone"), self._clock()
                )
        except ScanUnavailable:
            pass
        return {"active": active, "last_manual_started": last_manual, "next_scheduled_at": upcoming}

    def new_name(self) -> str:
        return manual_job_name(self.cronjob, self._clock())

    def start(self, name: str | None = None) -> dict:
        now = self._clock()
        active, last_manual = summarize_jobs(self._jobs(), self.cronjob)
        if active:
            raise ScanBusy("a scan is running")
        if last_manual is not None and now - last_manual < COOLDOWN:
            raise ScanTooSoon((COOLDOWN - (now - last_manual)).total_seconds())
        job = build_job(self._cronjob(), name or self.new_name())
        try:
            response = self._client.request("POST", self._path("jobs"), json=job)
        except httpx.HTTPError as error:
            log.warning("job create failed: %s", type(error).__name__)
            response = None
        if response is not None and response.status_code == 409:
            raise ScanBusy("a scan was just started")
        started = {"trigger": "manual", "state": "running", "started_at": now}
        if response is not None and response.status_code in {200, 201, 202}:
            return started
        if response is not None:
            log.warning("job create returned %s", response.status_code)
        # the reply may be lost, so ask
        state = self.job_state(job["metadata"]["name"])
        if state in {"active", "finished"}:
            return started
        if state == "missing" and response is not None and 400 <= response.status_code < 500:
            raise ScanUnavailable("could not start the scan")
        raise ScanUncertain("could not confirm the scan")


class FakeLauncher:
    """In-memory launcher for demo and local development."""

    def __init__(self, store=None, *, duration: float = 30.0, clock=None):
        self._store = store
        self._duration = dt.timedelta(seconds=duration)
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._started: dt.datetime | None = None
        self._last_manual: dt.datetime | None = None
        self._name: str | None = None

    def _settle(self) -> None:
        if self._started and self._clock() - self._started >= self._duration:
            if self._store is not None:
                self._store.finish_demo_scout_run(0, 0, 0)
            self._started = None

    def status(self) -> dict:
        self._settle()
        return {"active": self._started is not None,
                "last_manual_started": self._last_manual, "next_scheduled_at": None}

    def new_name(self) -> str:
        return manual_job_name("jobscout", self._clock())

    def job_state(self, name: str | None) -> str:
        self._settle()
        return "active" if name and name == self._name and self._started else "missing"

    def start(self, name: str | None = None) -> dict:
        self._settle()
        now = self._clock()
        if self._started is not None:
            raise ScanBusy("a scan is running")
        if self._last_manual and now - self._last_manual < COOLDOWN:
            raise ScanTooSoon((COOLDOWN - (now - self._last_manual)).total_seconds())
        self._started = self._last_manual = now
        self._name = name
        return {"trigger": "manual", "state": "running", "started_at": now}


# an unfinished row older than this is a crashed run, not a live one
STALE_AFTER = dt.timedelta(hours=2)


def public_run(row: dict, now: dt.datetime) -> dict:
    """Safe fields only; job names and pod details stay server side."""
    finished = row.get("finished_at")
    started = row.get("started_at")
    if finished is None:
        stale = started is not None and now - started > STALE_AFTER
        state = "failed" if stale else "running"
    elif row.get("exit_code") == 0:
        state = "skipped" if row.get("note") == SKIPPED_NOTE else "ok"
    else:
        state = "failed"
    public = {
        "id": row.get("id"),
        "trigger": row.get("trigger"),
        "state": state,
        "started_at": started,
        "finished_at": finished,
        "exit_code": row.get("exit_code"),
        "new_rows": row.get("new_rows"),
        "notified": row.get("notified"),
    }
    if state == "skipped":
        public["note"] = SKIPPED_NOTE
    return public


def overview(rows: list[dict], status: dict | None, now: dt.datetime) -> dict:
    """The GET payload: recent runs plus what the button may do."""
    runs = [public_run(row, now) for row in rows]
    known = status is not None
    if known:
        running = bool(status["active"])
    else:
        # cluster unreachable: trust the newest row
        running = bool(runs) and runs[0]["state"] == "running"
    last_manual = status.get("last_manual_started") if known else None
    for row in rows:
        # same rule as the start check
        if row.get("trigger") == "manual" and row.get("started_at") and row.get("job_created_at"):
            if last_manual is None or row["started_at"] > last_manual:
                last_manual = row["started_at"]
            break
    cooldown_until = last_manual + COOLDOWN if last_manual else None
    if cooldown_until is not None and cooldown_until <= now:
        cooldown_until = None
    return {
        "runs": runs,
        "running": running,
        "status_known": known,
        "cooldown_until": cooldown_until,
        "next_scheduled_at": status.get("next_scheduled_at") if known else None,
    }
