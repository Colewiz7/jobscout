"""Postgres persistence and the dedupe/close rules.

Schema is created by the job itself, idempotently, so there is no separate
migration step to forget on a fresh CNPG cluster.
"""
from __future__ import annotations

import dataclasses
import logging

import psycopg
from psycopg.rows import dict_row

from .models import Posting, merge_locations

log = logging.getLogger(__name__)

# A board posting has to be absent from this many consecutive successful
# fetches of its own board before we call it closed. One miss is usually the
# board API being briefly inconsistent.
MISSES_BEFORE_CLOSED = 2

SCHEMA = """
create table if not exists postings (
    id          bigserial primary key,
    source      text        not null,
    source_id   text        not null,
    dedupe_key  text        not null,
    fallback_key text       not null default '',
    board       text,
    company     text        not null,
    title       text        not null,
    location    text        not null,
    terms       text        not null default '',
    url         text        not null,
    remote      boolean     not null default false,
    first_seen  timestamptz not null default now(),
    last_seen   timestamptz not null default now(),
    notified_at timestamptz,
    closed      boolean     not null default false,
    missed_runs integer     not null default 0,
    age_days    integer,
    unique (source, source_id)
);
-- Additive migrations for databases created by an earlier version. `create
-- table if not exists` is a no-op on an existing table, so a new column has to
-- be added explicitly or an upgrade fails at insert time.
alter table postings add column if not exists fallback_key text not null default '';
alter table postings add column if not exists age_days integer;

create index if not exists postings_dedupe_key_idx on postings (dedupe_key);
create index if not exists postings_fallback_key_idx on postings (fallback_key);
create index if not exists postings_pending_idx on postings (notified_at)
    where notified_at is null and not closed;
"""


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def upsert_open(conn: psycopg.Connection, postings: list[Posting]) -> int:
    """Insert or refresh every open posting. Returns the number of new rows."""
    if not postings:
        return 0
    with conn.cursor() as cur:
        cur.execute("select count(*) as n from postings")
        before = cur.fetchone()["n"]
    # Collapse duplicates inside this batch first. Three rows for the same job
    # in three cities must arrive as one row carrying all three locations; left
    # to ON CONFLICT they would overwrite each other and the last one would win.
    merged: dict[tuple[str, str], Posting] = {}
    for posting in postings:
        key = (posting.source, posting.source_id)
        existing = merged.get(key)
        if existing is None:
            merged[key] = posting
        else:
            merged[key] = dataclasses.replace(
                existing,
                location=merge_locations(existing.location, posting.location),
                remote=existing.remote or posting.remote,
            )
    rows = [
        (
            p.source, p.source_id, p.dedupe_key, p.fallback_key, p.board, p.company,
            p.title, p.location, p.terms, p.url, p.remote, p.age_days,
        )
        for p in merged.values()
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into postings
                (source, source_id, dedupe_key, fallback_key, board, company, title,
                 location, terms, url, remote, age_days)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (source, source_id) do update set
                last_seen   = now(),
                closed      = false,
                missed_runs = 0,
                title       = excluded.title,
                age_days    = excluded.age_days,
                -- Union rather than replace, so a location seen on an earlier
                -- run is not dropped when this run reports a different one.
                location    = (
                    select coalesce(
                        string_agg(distinct btrim(part), '; ' order by btrim(part)), '')
                      from unnest(string_to_array(
                               postings.location || '; ' || excluded.location, ';')) as part
                     where btrim(part) <> ''
                ),
                terms       = excluded.terms,
                url         = excluded.url,
                remote      = excluded.remote
            """,
            rows,
        )
        cur.execute("select count(*) as n from postings")
        after = cur.fetchone()["n"]
    conn.commit()
    return after - before


def mark_closed(conn: psycopg.Connection, source: str, dedupe_keys: list[str]) -> int:
    """Close rows we already know about. Never inserts.

    A locked Simplify row is a job we may already be tracking, so it flips the
    existing row closed. A locked row we have never seen is not worth a
    database row at all.

    Matches on fallback_key as well as dedupe_key: a locked row has no apply
    URL, so it cannot regenerate the provider id the open row was stored under.
    """
    if not dedupe_keys:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "update postings set closed = true, last_seen = now() "
            "where source = %s and not closed "
            "and (dedupe_key = any(%s) or fallback_key = any(%s))",
            (source, list(dedupe_keys), list(dedupe_keys)),
        )
        changed = cur.rowcount
    conn.commit()
    return changed


def close_missing_boards(
    conn: psycopg.Connection,
    fetched: set[tuple[str, str]],
    seen_ids: dict[tuple[str, str], set[str]],
) -> int:
    """Advance the missed-run counter for boards that answered this run.

    Scoped to (source, board) pairs in `fetched`, so a board that 404'd cannot
    close everything it has ever listed.
    """
    closed = 0
    with conn.cursor() as cur:
        for source, board in sorted(fetched):
            present = list(seen_ids.get((source, board), set()))
            cur.execute(
                "update postings set missed_runs = missed_runs + 1 "
                "where source = %s and board = %s and not closed "
                "and not (source_id = any(%s))",
                (source, board, present),
            )
            cur.execute(
                "update postings set closed = true "
                "where source = %s and board = %s and not closed "
                "and missed_runs >= %s",
                (source, board, MISSES_BEFORE_CLOSED),
            )
            closed += cur.rowcount
    conn.commit()
    return closed


_OPEN_SQL = """
with candidates as (
    select * from postings p
     where not p.closed
       {unnotified}
),
grouped as (
    select dedupe_key,
           min(company)   as company,
           min(title)     as title,
           min(terms)     as terms,
           min(age_days)  as age_days,
           min(url) filter (where url <> '') as url,
           string_agg(location, ';')         as locations_raw
      from candidates
     group by dedupe_key
)
select dedupe_key, company, title, terms, age_days, coalesce(url, '') as url,
       (select coalesce(string_agg(distinct btrim(part), '; ' order by btrim(part)), '')
          from unnest(string_to_array(locations_raw, ';')) as part
         where btrim(part) <> '') as location
  from grouped
"""

_UNNOTIFIED = """
       and p.notified_at is null
       and not exists (
             select 1 from postings q
              where q.dedupe_key = p.dedupe_key
                and q.notified_at is not null)
"""


def open_postings(conn: psycopg.Connection, only_unnotified: bool = True) -> list[dict]:
    """Open postings, one row per dedupe_key, with locations unioned.

    Grouping by dedupe_key is the cross-source dedupe: the same job found in
    both the Simplify README and the company's own Greenhouse board is one
    entry, carrying every location either copy mentioned.
    """
    sql = _OPEN_SQL.format(unnotified=_UNNOTIFIED if only_unnotified else "")
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    rows.sort(key=lambda r: (r["company"].lower(), r["title"].lower()))
    return rows


def pending(conn: psycopg.Connection, limit: int | None = None) -> list[dict]:
    """Open postings we have never notified about."""
    rows = open_postings(conn, only_unnotified=True)
    return rows[:limit] if limit else rows


def mark_notified(conn: psycopg.Connection, dedupe_keys: list[str]) -> int:
    """Mark every row sharing these keys, so a twin row cannot re-fire."""
    if not dedupe_keys:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "update postings set notified_at = now() "
            "where dedupe_key = any(%s) and notified_at is null",
            (list(dedupe_keys),),
        )
        changed = cur.rowcount
    conn.commit()
    return changed


def seed(conn: psycopg.Connection) -> int:
    """Mark everything currently known as already notified, pushing nothing."""
    with conn.cursor() as cur:
        cur.execute(
            "update postings set notified_at = now() where notified_at is null"
        )
        changed = cur.rowcount
    conn.commit()
    return changed
