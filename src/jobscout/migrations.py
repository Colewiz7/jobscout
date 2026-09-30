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
    Migration(
        3,
        "saved_views",
        """
        create table if not exists saved_views (
            id         bigserial primary key,
            name       text not null unique,
            filters    jsonb not null default '{}'::jsonb,
            sort       text not null default 'score',
            pinned     boolean not null default true,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        );
        create index if not exists postings_company_lower_idx
            on postings (lower(company), dedupe_key);
        create index if not exists application_history_applied_idx
            on application_status_history (dedupe_key, changed_at desc)
            where to_status = 'applied';
        """,
    ),
    Migration(
        4,
        "quick_fill",
        """
        create table if not exists profile_fields (
            key        text primary key,
            group_name text not null,
            label      text not null,
            value      text not null default '',
            pinned     boolean not null default false,
            sort_order integer not null default 0,
            updated_at timestamptz not null default now()
        );
        create table if not exists answer_templates (
            id         bigserial primary key,
            name       text not null unique,
            body       text not null default '',
            sort_order integer not null default 0,
            updated_at timestamptz not null default now()
        );
        create table if not exists documents (
            id         bigserial primary key,
            name       text not null,
            document_date date,
            url        text not null default '',
            created_at timestamptz not null default now()
        );
        """,
    ),
    Migration(
        5,
        "quick_fill_story_bank",
        """
        create table if not exists story_bank (
            key          text primary key,
            title        text not null,
            situation    text not null default '',
            task         text not null default '',
            action       text not null default '',
            result       text not null default '',
            reflection   text not null default '',
            competencies text[] not null default '{}',
            sort_order   integer not null default 0,
            updated_at   timestamptz not null default now()
        );
        """,
    ),
    Migration(
        6,
        "quick_fill_job_context",
        """
        create table if not exists quick_fill_copy_state (
            dedupe_key text not null,
            target_key text not null,
            copied_at timestamptz not null default now(),
            primary key (dedupe_key, target_key)
        );
        create table if not exists answer_template_overrides (
            dedupe_key    text not null,
            template_name text not null,
            body          text not null,
            updated_at    timestamptz not null default now(),
            primary key (dedupe_key, template_name)
        );
        create table if not exists company_accounts (
            company_key          text primary key,
            company_name         text not null,
            account_exists       boolean,
            sign_in_email        text not null default '',
            password_manager_url text not null default '',
            updated_at           timestamptz not null default now()
        );
        """,
    ),
    Migration(
        7,
        "apply_queue_and_snapshots",
        """
        alter table postings add column if not exists liveness_status text
            check (liveness_status in ('live', 'closed', 'unknown'));
        alter table postings add column if not exists liveness_checked_at timestamptz;
        alter table postings add column if not exists liveness_evidence text;

        alter table application_states add column if not exists queue_position integer;
        alter table application_states add column if not exists applied_at timestamptz;
        alter table application_states add column if not exists resume_document_id bigint
            references documents(id) on delete set null;
        alter table application_states add column if not exists resume_name text;

        create table if not exists application_snapshots (
            id                   bigserial primary key,
            dedupe_key           text not null unique,
            captured_at          timestamptz not null default now(),
            company              text not null,
            title                text not null,
            location             text not null default '',
            terms                text not null default '',
            url                  text not null default '',
            sources              text not null default '',
            deadline             date,
            description_text     text,
            description_sections jsonb not null default '[]'::jsonb,
            resume_document_id   bigint references documents(id) on delete set null,
            resume_name          text
        );
        create index if not exists application_states_queue_idx
            on application_states (queue_position, updated_at)
            where status = 'queued';
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
