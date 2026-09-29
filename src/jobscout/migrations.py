"""Versioned PostgreSQL migrations run only by the deployment hook."""
from __future__ import annotations

import dataclasses
import logging

import psycopg

from . import db

log = logging.getLogger(__name__)

# Stable, repository-specific signed bigint. A session-level lock serializes
# migrations even when Argo retries a hook while another process is finishing.
MIGRATION_LOCK_ID = 0x4A4F4253434F5554


@dataclasses.dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS = (
    Migration(1, "baseline", db.SCHEMA),
    Migration(
        2,
        "inbox_descriptions_and_status_history",
        """
        alter table postings add column if not exists description_html text;
        alter table postings add column if not exists description_text text;
        alter table postings add column if not exists description_sections jsonb;
        alter table postings add column if not exists description_fetched_at timestamptz;
        alter table postings add column if not exists description_error text;
        alter table postings add column if not exists deadline date;
        alter table postings add column if not exists deadline_source text
            check (deadline_source in ('description', 'provider', 'manual'));

        alter table application_states
            drop constraint if exists application_states_status_check;
        update application_states
           set status = case status
               when 'preparing' then 'queued'
               when 'interview' then 'interviewing'
               when 'skipped' then 'archived'
               else status
           end;
        alter table application_states
            add constraint application_states_status_check
            check (status in (
                'new', 'saved', 'queued', 'applying', 'applied',
                'interviewing', 'offer', 'rejected', 'archived'
            ));

        create table if not exists application_status_history (
            id          bigserial primary key,
            dedupe_key  text not null,
            from_status text not null,
            to_status   text not null,
            changed_at  timestamptz not null default now()
        );
        create index if not exists application_status_history_job_idx
            on application_status_history (dedupe_key, changed_at desc);
        """,
    ),
)


def run(conn: psycopg.Connection) -> list[Migration]:
    """Apply pending migrations while holding the JobScout advisory lock."""
    applied: list[Migration] = []
    with conn.cursor() as cur:
        cur.execute("select pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists schema_migrations (
                    version    integer primary key,
                    name       text not null,
                    applied_at timestamptz not null default now()
                )
                """
            )
            cur.execute("select version from schema_migrations")
            completed = {row["version"] for row in cur.fetchall()}
            for migration in MIGRATIONS:
                if migration.version in completed:
                    continue
                log.info("applying migration %04d %s", migration.version, migration.name)
                cur.execute(migration.sql)
                cur.execute(
                    "insert into schema_migrations (version, name) values (%s, %s)",
                    (migration.version, migration.name),
                )
                applied.append(migration)
        conn.commit()
        return applied
    except Exception:
        conn.rollback()
        raise
    finally:
        with conn.cursor() as cur:
            cur.execute("select pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
        conn.commit()


def require_current(conn: psycopg.Connection) -> None:
    """Fail closed when a runtime starts before its PreSync migration Job."""
    expected = MIGRATIONS[-1].version
    try:
        with conn.cursor() as cur:
            cur.execute("select coalesce(max(version), 0) as version from schema_migrations")
            actual = cur.fetchone()["version"]
    except psycopg.errors.UndefinedTable as error:
        conn.rollback()
        raise RuntimeError("database is not migrated; run `jobscout migrate`") from error
    if actual != expected:
        raise RuntimeError(
            f"database migration version is {actual}; expected {expected}; run `jobscout migrate`"
        )
