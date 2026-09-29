# Database migrations

Schema changes run in one place: `python -m jobscout migrate`. The scout and
dashboard only verify the recorded schema version and fail closed when the
PreSync migration Job has not completed. The runner holds a PostgreSQL session
advisory lock for its full transaction, so an Argo retry cannot race another
migration process.

The production manifest lives in the homelab GitOps repository at
`apps/jobscout/migration-job.yaml`. It is a PreSync hook at sync wave `-10`, so
a failed migration blocks rollout of the wave `0` scout and dashboard images.

## Before the first migration

Create an on-demand CNPG backup and wait for it to complete before the first
hook is enabled:

```bash
kubectl -n jobscout apply -f - <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: jobscout-pre-migrations
spec:
  method: barmanObjectStore
  cluster:
    name: jobscout-db
EOF
kubectl -n jobscout wait --for=condition=Completed \
  backup/jobscout-pre-migrations --timeout=20m
```

Do not start the first migration if the Backup is not `Completed`.

## Test against real data

Take a custom-format dump from the live database, keep it outside Git, and run
the disposable restore test before deploying a migration that changes data:

```bash
pg_dump "$DATABASE_URL" --format=custom --file=/private/jobscout-real.dump
scripts/test-migration-dump.sh /private/jobscout-real.dump jobscout:migration-test
```

The script restores into a temporary Postgres container, records row counts,
runs the migration twice to prove idempotence, and verifies that application
data counts did not change. It deletes the disposable container and network;
it never modifies the supplied dump.
