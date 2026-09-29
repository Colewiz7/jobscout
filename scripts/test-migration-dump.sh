#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 /path/to/jobscout.dump [image]" >&2
  exit 2
fi

dump_path=$(realpath "$1")
image=${2:-jobscout:migration-test}
container="jobscout-migration-test-$RANDOM"
network="$container"

cleanup() {
  docker rm --force "$container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$network" >/dev/null
docker run --detach --name "$container" --network "$network" \
  --env POSTGRES_PASSWORD=test --env POSTGRES_DB=jobscout \
  postgres:16-alpine >/dev/null

for _ in {1..30}; do
  if docker exec "$container" pg_isready --username postgres --dbname jobscout >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$container" pg_isready --username postgres --dbname jobscout >/dev/null

docker exec -i "$container" pg_restore --username postgres --dbname jobscout \
  --no-owner --no-privileges < "$dump_path"

table_count() {
  local table=$1
  local exists
  exists=$(docker exec "$container" psql --username postgres --dbname jobscout \
    --tuples-only --no-align --command "select to_regclass('public.$table') is not null;")
  if [[ "$exists" == "t" ]]; then
    docker exec "$container" psql --username postgres --dbname jobscout \
      --tuples-only --no-align --command "select count(*) from $table;"
  else
    echo 0
  fi
}

before="postings=$(table_count postings),applications=$(table_count application_states)"

database_url="postgresql://postgres:test@$container:5432/jobscout"
docker run --rm --network "$network" --env DATABASE_URL="$database_url" "$image" migrate
docker run --rm --network "$network" --env DATABASE_URL="$database_url" "$image" migrate

after="postings=$(table_count postings),applications=$(table_count application_states)"
if [[ "$before" != "$after" ]]; then
  echo "row counts changed: before=$before after=$after" >&2
  exit 1
fi

echo "migration test passed: $after"
