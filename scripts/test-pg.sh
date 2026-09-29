#!/usr/bin/env bash
set -euo pipefail

runtime="${CONTAINER_RUNTIME:-}"
if [[ -n "$runtime" ]]; then
  if ! command -v "$runtime" >/dev/null 2>&1; then
    echo "CONTAINER_RUNTIME=$runtime is not installed" >&2
    exit 2
  fi
  if ! "$runtime" info >/dev/null 2>&1; then
    echo "CONTAINER_RUNTIME=$runtime is installed but not running" >&2
    exit 2
  fi
elif command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
  runtime=podman
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  runtime=docker
else
  echo "test-pg needs a running Podman or Docker service" >&2
  exit 2
fi

python_bin="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  echo "$python_bin is not executable; create the virtualenv or set PYTHON" >&2
  exit 2
fi

image="${POSTGRES_TEST_IMAGE:-postgres:16-alpine}"
container="jobscout-test-pg-$$"

cleanup() {
  "$runtime" rm --force "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "Starting disposable PostgreSQL with $runtime ($image)"
"$runtime" run --detach --name "$container" \
  --env POSTGRES_USER=jobscout \
  --env POSTGRES_PASSWORD=jobscout-test \
  --env POSTGRES_DB=jobscout \
  --publish 127.0.0.1::5432 \
  "$image" >/dev/null

ready=false
for _ in {1..60}; do
  if "$runtime" exec "$container" pg_isready \
    --username jobscout --dbname jobscout >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 0.5
done

if [[ "$ready" != true ]]; then
  echo "PostgreSQL did not become ready" >&2
  "$runtime" logs "$container" >&2 || true
  exit 1
fi

published_port=$("$runtime" port "$container" 5432/tcp | head -n 1)
host_port="${published_port##*:}"
if [[ ! "$host_port" =~ ^[0-9]+$ ]]; then
  echo "Could not determine the published PostgreSQL port: $published_port" >&2
  exit 1
fi

echo "Running the full suite with PostgreSQL on port $host_port"
JOBSCOUT_TEST_DSN="postgresql://jobscout:jobscout-test@127.0.0.1:${host_port}/jobscout" \
  "$python_bin" -m pytest -q
