"""Postgres persistence and the dedupe/close rules.

Schema changes belong to the locked one-shot migration runner. Runtime scout
and dashboard processes only verify that the recorded version is current.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import logging
import urllib.parse

import psycopg
from psycopg.types.json import Json
from psycopg.rows import dict_row

from .models import Posting, merge_locations, normalise, workday_req_from_url

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
alter table postings add column if not exists score integer;
alter table postings add column if not exists score_detail jsonb;

create table if not exists board_notices (
    key        text primary key,
    first_seen timestamptz not null default now()
);

create table if not exists application_states (
    dedupe_key text primary key,
    status     text not null default 'new'
               check (status in (
                   'new', 'saved', 'preparing', 'applied', 'interview',
                   'offer', 'rejected', 'skipped'
               )),
    notes      text not null default '',
    updated_at timestamptz not null default now()
);

create index if not exists postings_dedupe_key_idx on postings (dedupe_key);
create index if not exists postings_fallback_key_idx on postings (fallback_key);
create index if not exists postings_pending_idx on postings (notified_at)
    where notified_at is null and not closed;
"""


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)


def ensure_schema(conn: psycopg.Connection) -> None:
    """Test/bootstrap compatibility wrapper around the migration runner.

    Production scout and dashboard processes call ``require_current`` instead;
    only the one-shot migration Job is allowed to mutate schema.
    """
    from . import migrations

    migrations.run(conn)


def require_schema(conn: psycopg.Connection) -> None:
    """Verify that the deployment migration hook has brought the schema current."""
    from . import migrations

    migrations.require_current(conn)


def upsert_open(conn: psycopg.Connection, postings: list[Posting]) -> int:
    """Insert or refresh every open posting. Returns the number of new rows."""
    if not postings:
        return 0
    with conn.cursor() as cur:
        # before any postings row lock
        lock_companies(cur)
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
        _ensure_companies(cur, {
            company_key(posting.company): posting.company.strip()
            for posting in merged.values() if company_key(posting.company)
        })
        _apply_rules_cursor(cur)
        _apply_hidden_cursor(cur)
        cur.execute("select count(*) as n from postings")
        after = cur.fetchone()["n"]
    conn.commit()
    return after - before


def title_key(company: str, title: str) -> str:
    # the dashboard collapses on this
    company, title = company_key(company), normalise(title)
    return f"{company}\t{title}" if company and title else ""


HIDE_UNDO_SECONDS = 60


# undo outlived its snackbar
class HideUndoRefused(Exception):
    def __init__(self, message: str, *, unknown: bool = False):
        super().__init__(message)
        self.unknown = unknown


def hide_jobs(
    conn: psycopg.Connection, dedupe_keys: list[str], undo_hash: str | None = None,
    undo_seconds: int = HIDE_UNDO_SECONDS,
) -> list[dict]:
    created = []
    with conn.cursor() as cur:
        for key in dict.fromkeys(dedupe_keys):
            cur.execute(
                "select fallback_key, company, title, url, hidden_by from postings where dedupe_key = %s",
                (key,),
            )
            rows = cur.fetchall()
            # repeat hides have nothing to undo
            if not rows or any(row["hidden_by"] is not None for row in rows):
                continue
            keys = sorted({key} | {row["fallback_key"] for row in rows if row["fallback_key"]})
            cur.execute("select company, title from posting_overrides where dedupe_key = %s", (key,))
            override = cur.fetchone()
            named = [*rows, *([{
                "company": override["company"] or rows[0]["company"],
                "title": override["title"] or rows[0]["title"],
            }] if override else [])]
            titles = sorted({title for row in named if (title := title_key(row["company"], row["title"]))})
            reqs = sorted({req for row in rows if (req := workday_req_from_url(row["url"]))})
            cur.execute(
                """
                insert into hidden_jobs (dedupe_key, keys, titles, reqs) values (%s, %s, %s, %s)
                on conflict (dedupe_key) do nothing
                returning id
                """,
                (key, keys, titles, reqs),
            )
            entry = cur.fetchone()
            if entry:
                created.append({"id": entry["id"], "dedupe_key": key})
        _apply_hidden_cursor(cur)
        if created and undo_hash:
            cur.execute("delete from hide_undo_tokens where expires_at < now() - interval '1 day'")
            cur.execute(
                """
                insert into hide_undo_tokens (token_hash, hidden_ids, expires_at)
                values (%s, %s, now() + make_interval(secs => %s))
                """,
                (undo_hash, [entry["id"] for entry in created], undo_seconds),
            )
    conn.commit()
    return created


def undo_hide(conn: psycopg.Connection, undo_hash: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            select hidden_ids, used_at, expires_at < now() as expired
              from hide_undo_tokens where token_hash = %s for update
            """,
            (undo_hash,),
        )
        token = cur.fetchone()
        if token is None or token["used_at"] is not None or token["expired"]:
            conn.rollback()
            if token is None:
                raise HideUndoRefused("This hide can't be undone from here.", unknown=True)
            raise HideUndoRefused("This hide was already undone." if token["used_at"] else "The time to undo this hide has passed.")
        cur.execute("update hide_undo_tokens set used_at = now() where token_hash = %s", (undo_hash,))
        cur.execute("delete from hidden_jobs where id = any(%s)", (token["hidden_ids"],))
        removed = cur.rowcount
        # other entries may still match
        _apply_hidden_cursor(cur)
    conn.commit()
    return removed


def hidden_keys(conn: psycopg.Connection, dedupe_keys: list[str]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "select distinct dedupe_key from postings where dedupe_key = any(%s) and hidden_by is not null",
            (list(dedupe_keys),),
        )
        return {row["dedupe_key"] for row in cur.fetchall()}




def _apply_hidden_cursor(cur) -> None:
    cur.execute("select id, titles, reqs from hidden_jobs")
    entries = cur.fetchall()
    if not entries:
        return
    cur.execute(
        """
        update postings p set hidden_by = h.id
          from hidden_jobs h
         where p.hidden_by is null
           and (p.dedupe_key = any(h.keys) or (p.fallback_key <> '' and p.fallback_key = any(h.keys)))
        """
    )
    titles = {title: entry["id"] for entry in entries for title in entry["titles"]}
    reqs = {req: entry["id"] for entry in entries for req in entry["reqs"]}
    cur.execute("select id, company, title, url from postings where hidden_by is null")
    matched = []
    for row in cur.fetchall():
        title, req = title_key(row["company"], row["title"]), workday_req_from_url(row["url"])
        entry = titles.get(title) or (reqs.get(req) if req else None)
        if entry:
            matched.append((entry, row["id"]))
    if matched:
        cur.executemany("update postings set hidden_by = %s where id = %s", matched)
    # a hidden row hides its twins
    cur.execute(
        """
        update postings p set hidden_by = q.hidden_by
          from postings q
         where p.hidden_by is null and q.hidden_by is not null
           and p.dedupe_key = q.dedupe_key
        """
    )


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
       and p.hidden_by is null
       and p.board is distinct from 'testnisc'
       {unnotified}
),
grouped as (
    select dedupe_key,
           min(company)   as company,
           min(title)     as title,
           min(terms)     as terms,
           min(age_days)  as age_days,
           min(first_seen) as first_seen,
           min(url) filter (where url <> '') as url,
           string_agg(location, ';')         as locations_raw
      from candidates
     group by dedupe_key
)
select dedupe_key, company, title, terms, age_days, first_seen,
       coalesce(url, '') as url,
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


def record_scores(conn: psycopg.Connection, scored: list[tuple[str, int, dict]]) -> int:
    """Store the score and its breakdown against every row of each key.

    Written for every candidate, not only the ones that push, so a posting
    that never arrived can still be explained after the fact.
    """
    if not scored:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "update postings set score = %s, score_detail = %s where dedupe_key = %s",
            [(total, Json(detail), key) for key, total, detail in scored],
        )
    conn.commit()
    return len(scored)


def notice_once(conn: psycopg.Connection, key: str) -> bool:
    """True the first time this notice is raised, false forever after.

    A misconfigured board is wrong every run. Saying so every six hours trains
    the reader to ignore it, so it is said once.
    """
    with conn.cursor() as cur:
        cur.execute(
            "insert into board_notices (key) values (%s) on conflict do nothing", (key,)
        )
        fresh = cur.rowcount == 1
    conn.commit()
    return fresh


APPLICATION_STATUSES = frozenset(
    {
        "new", "saved", "queued", "applying", "applied", "interviewing",
        "offer", "rejected", "archived",
    }
)
BOARD_STATUSES = frozenset({"applying", "applied", "interviewing", "offer", "rejected"})


def company_key(value: str) -> str:
    """Stable, deliberately conservative key for company/contact matching."""
    parts = normalise(value).split()
    if parts and parts[0] == "the":
        parts = parts[1:]
    suffixes = {"co", "company", "corp", "corporation", "inc", "incorporated", "llc", "ltd", "limited"}
    while len(parts) > 1 and parts[-1] in suffixes:
        parts.pop()
    return " ".join(parts)


COMPANY_COLUMNS = "key, name, display_name, manual, hidden_at, revision"


def company_name(row: dict) -> str:
    return row.get("display_name") or row["name"]


def resolve_company(companies: dict[str, dict], name: str) -> str | None:
    """The company a scout or typed name means, after renames and merges."""
    key = company_key(name)
    if not key:
        return None
    if key in companies:
        return canonical_key(companies, key)
    owner = renamed_owners(companies).get(key)
    return owner and canonical_key(companies, owner)


def renamed_owners(companies: dict[str, dict]) -> dict[str, str]:
    """Display-name keys to the row that took that name."""
    owners: dict[str, str] = {}
    # live rows win over merged ones
    for row in sorted(companies.values(), key=lambda row: bool(row.get("merged_into"))):
        if row.get("display_name"):
            owners.setdefault(company_key(row["display_name"]), row["key"])
    return owners


def canonical_key(companies: dict[str, dict], key: str) -> str:
    seen = set()
    # merges chain, so walk them
    while key in companies and companies[key].get("merged_into") and key not in seen:
        seen.add(key)
        key = companies[key]["merged_into"]
    return key


def company_rows(cur) -> dict[str, dict]:
    cur.execute(f"select {COMPANY_COLUMNS}, merged_into from companies")
    return {row["key"]: row for row in cur.fetchall()}


def companies(conn: psycopg.Connection) -> dict[str, dict]:
    with conn.cursor() as cur:
        return company_rows(cur)


def lock_companies(cur) -> None:
    """Every companies writer takes this first."""
    # one order means no deadlocks
    cur.execute("lock table companies in share row exclusive mode")


def _read_companies(cur) -> dict[str, dict]:
    # waits out merges and renames
    cur.execute("lock table companies in share mode")
    return company_rows(cur)


def _ensure_companies(cur, names: dict[str, str], *, refresh: bool = True) -> None:
    # renames must not slip between
    lock_companies(cur)
    # postings may use new names
    renamed = renamed_owners(company_rows(cur))
    cur.executemany(
        f"""
        insert into companies (key, name, merged_into) values (%s, %s, %s)
        on conflict (key) do {'update set name = excluded.name, updated_at = now()' if refresh else 'nothing'}
        """,
        [(key, name, renamed[key] if renamed.get(key, key) != key else None) for key, name in names.items()],
    )


def company_icon_keys(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("select company_key from company_assets where logo_data is not null")
        keys = [row["company_key"] for row in cur.fetchall()]
        cur.execute("select display_name from companies where display_name is not null and key = any(%s)", (keys,))
        # renamed rows show the new name
        renamed = [company_key(row["display_name"]) for row in cur.fetchall()]
    return sorted(set(keys) | set(renamed))


def company_icon(conn: psycopg.Connection, name: str) -> dict | None:
    with conn.cursor() as cur:
        key = resolve_company(company_rows(cur), name) or company_key(name)
        if not key:
            return None
        cur.execute(
            "select logo_data, media_type from company_assets where company_key = %s and logo_data is not null",
            (key,),
        )
        return cur.fetchone()


def enqueue_overviews(conn: psycopg.Connection, keys: list[str], *, priority: bool = False) -> int:
    if not keys or len(keys) > 5_000:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """insert into overview_jobs (dedupe_key, priority)
               select distinct dedupe_key, %s from postings where dedupe_key = any(%s)
               on conflict (dedupe_key) do update set
                 status = 'queued', priority = overview_jobs.priority or excluded.priority,
                 attempts = 0, error = null, available_at = now(),
                 requested_at = now(), updated_at = now()
               where overview_jobs.status <> 'ready'
               returning dedupe_key""",
            (priority, keys),
        )
        count = len(cur.fetchall())
    conn.commit()
    return count


def overview_job(conn: psycopg.Connection, key: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """select status, priority, items, source, source_hash, attempts,
                      error, requested_at, generated_at, dismissed_at, revision,
                      previous is not null as has_previous
                 from overview_jobs where dedupe_key = %s""",
            (key,),
        )
        return cur.fetchone()


def claim_overview(conn: psycopg.Connection, *, priority_only: bool = False) -> tuple[str, int] | None:
    """The claim id must come back with the result."""
    with conn.cursor() as cur:
        cur.execute(
            """select dedupe_key from overview_jobs
                where ((status = 'queued' and available_at <= now())
                   or (status = 'running' and started_at < now() - interval '10 minutes'))
                  and (not %s or priority)
                order by priority desc, requested_at
                for update skip locked limit 1""",
            (priority_only,),
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None
        key = row["dedupe_key"]
        cur.execute(
            """update overview_jobs set status = 'running', attempts = attempts + 1,
                      claim_id = claim_id + 1, started_at = now(), updated_at = now()
                 where dedupe_key = %s returning claim_id""",
            (key,),
        )
        claim = cur.fetchone()["claim_id"]
    conn.commit()
    return key, claim


# a later claim owns the row
def finish_overview(
    conn: psycopg.Connection, key: str, items: list[dict], source: str, source_hash: str, *, claim: int
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """update overview_jobs set status = 'ready', priority = false,
                      items = %s, source = %s, source_hash = %s, error = null,
                      generated_at = now(), updated_at = now()
                 where dedupe_key = %s and claim_id = %s and status <> 'ready'""",
            (Json(items), source, source_hash, key, claim),
        )
    conn.commit()


def fail_overview(conn: psycopg.Connection, key: str, reason: str, *, claim: int, terminal: bool = False) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """update overview_jobs
                  set status = case when %s or attempts >= 3 then 'failed' else 'queued' end,
                      error = %s, available_at = now() + interval '10 minutes',
                      updated_at = now()
                where dedupe_key = %s and claim_id = %s and status <> 'ready'""",
            (terminal, reason[:500], key, claim),
        )
    conn.commit()


def dismiss_overview(conn: psycopg.Connection, key: str, dismissed: bool) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """update overview_jobs set dismissed_at = case when %s then now() end, updated_at = now()
                where dedupe_key = %s and (dismissed_at is null) = %s returning dedupe_key""",
            (dismissed, key, dismissed),
        )
        changed = cur.fetchone() is not None
    conn.commit()
    row = overview_job(conn, key)
    # another tab got there first
    if row is not None and not changed:
        raise EditConflict("The overview changed in another tab.")
    return row


def regenerate_overview(conn: psycopg.Connection, key: str) -> dict | None:
    """Keeps the last result for undo."""
    with conn.cursor() as cur:
        cur.execute(
            """update overview_jobs set
                      previous = case when status = 'ready' then jsonb_build_object(
                          'items', items, 'source', source, 'source_hash', source_hash,
                          'generated_at', generated_at) else previous end,
                      status = 'queued', priority = true, attempts = 0, error = null,
                      dismissed_at = null, revision = revision + 1, claim_id = claim_id + 1,
                      available_at = now(), requested_at = now(), updated_at = now()
                where dedupe_key = %s and status in ('ready', 'failed') returning dedupe_key""",
            (key,),
        )
        changed = cur.fetchone() is not None
    conn.commit()
    row = overview_job(conn, key)
    if row is not None and not changed:
        raise EditConflict("This overview is already being generated.")
    return row


def undo_regenerate_overview(conn: psycopg.Connection, key: str, expected: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("select revision, previous from overview_jobs where dedupe_key = %s for update", (key,))
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        if row["revision"] != expected or row["previous"] is None:
            conn.rollback()
            raise EditConflict("This overview changed after it was regenerated, so it can't be undone.")
        previous = row["previous"]
        cur.execute(
            """update overview_jobs set status = 'ready', priority = false, error = null,
                      items = %s, source = %s, source_hash = %s, generated_at = %s,
                      previous = null, revision = revision + 1, claim_id = claim_id + 1, updated_at = now()
                where dedupe_key = %s""",
            (Json(previous["items"]), previous["source"], previous["source_hash"], previous["generated_at"], key),
        )
    conn.commit()
    return overview_job(conn, key)


def posting_taglines(conn: psycopg.Connection) -> dict[str, str]:
    """Generated one-line taglines, keyed by deduplicated job."""
    with conn.cursor() as cur:
        cur.execute("select dedupe_key, tagline from posting_taglines where tagline is not null")
        return {row["dedupe_key"]: row["tagline"] for row in cur.fetchall()}


def pending_taglines(conn: psycopg.Connection, keys: list[str], prompt_hash: str, limit: int) -> list[str]:
    """Keys that still need a tagline, in the order given.

    A changed prompt regenerates everything, so tuning the prompt is an edit and
    a rerun. Failures back off for ten minutes and stop after three attempts.
    Postings whose description has not been fetched yet are left for later,
    unless the description was corrected by hand.
    """
    if not keys:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """select k.key
                 from unnest(%s::text[]) with ordinality as k(key, position)
                 left join posting_taglines t on t.dedupe_key = k.key
                where (exists (select 1 from postings p
                                where p.dedupe_key = k.key and p.description_fetched_at is not null)
                       or exists (select 1 from posting_overrides o
                                   where o.dedupe_key = k.key and o.description_text is not null))
                  and (t.dedupe_key is null
                       or t.prompt_hash <> %s
                       or (t.tagline is null and t.attempts < 3 and t.available_at <= now()))
                order by k.position
                limit %s""",
            (keys, prompt_hash, limit),
        )
        return [row["key"] for row in cur.fetchall()]


def save_tagline(conn: psycopg.Connection, key: str, tagline: str, prompt_hash: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into posting_taglines (dedupe_key, tagline, prompt_hash, generated_at)
               values (%s, %s, %s, now())
               on conflict (dedupe_key) do update set
                 tagline = excluded.tagline, prompt_hash = excluded.prompt_hash,
                 attempts = 0, error = null, generated_at = now(), updated_at = now()""",
            (key, tagline, prompt_hash),
        )
    conn.commit()


def fail_tagline(conn: psycopg.Connection, key: str, reason: str, prompt_hash: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into posting_taglines (dedupe_key, prompt_hash, attempts, error, available_at)
               values (%s, %s, 1, %s, now() + interval '10 minutes')
               on conflict (dedupe_key) do update set
                 attempts = case when posting_taglines.prompt_hash = excluded.prompt_hash
                                 then posting_taglines.attempts + 1 else 1 end,
                 tagline = case when posting_taglines.prompt_hash = excluded.prompt_hash
                                then posting_taglines.tagline else null end,
                 prompt_hash = excluded.prompt_hash, error = excluded.error,
                 available_at = excluded.available_at, updated_at = now()""",
            (key, prompt_hash, reason[:500]),
        )
    conn.commit()


def collapse_dashboard_duplicates(rows: list[dict]) -> list[dict]:
    """Unify legacy cross-source copies without changing stored application keys.

    A Workday req is stronger than a feed's rewritten title/company. Otherwise
    equal normalized company/title is the fallback. Two independently tracked
    applications are kept apart rather than silently hiding either history.
    """
    groups: list[dict] = []
    req_index: dict[str, int] = {}
    title_index: dict[tuple[str, str], int] = {}
    for original in rows:
        row = dict(original)
        req = workday_req_from_url(str(row.get("url") or ""))
        title = (company_key(str(row.get("company") or "")), normalise(str(row.get("title") or "")))
        index = req_index.get(req) if req else None
        if index is None:
            index = title_index.get(title)
        if index is None:
            index = len(groups)
            groups.append(row)
        else:
            old = groups[index]
            if old.get("status", "new") != "new" and row.get("status", "new") != "new":
                groups.append(row)
                continue
            # Keep the key with application history, otherwise prefer the
            # first-party board URL over Simplify's rewritten title.
            incoming_primary = (row.get("status", "new") != "new" and old.get("status", "new") == "new") or (
                row.get("status", "new") == old.get("status", "new")
                and "simplify" in str(old.get("sources") or "")
                and "simplify" not in str(row.get("sources") or "")
            )
            primary, secondary = (row, old) if incoming_primary else (old, row)
            combined = dict(primary)
            combined["sources"] = ", ".join(sorted(set(
                str(old.get("sources") or "").split(", ") +
                str(row.get("sources") or "").split(", ")
            ) - {""}))
            combined["location"] = merge_locations(str(old.get("location") or ""), str(row.get("location") or ""))
            combined["first_seen"] = min(filter(None, (old.get("first_seen"), row.get("first_seen"))), default=None)
            combined["last_seen"] = max(filter(None, (old.get("last_seen"), row.get("last_seen"))), default=None)
            combined["score"] = max(filter(lambda value: value is not None, (old.get("score"), row.get("score"))), default=None)
            combined["notified"] = bool(old.get("notified") or row.get("notified"))
            combined["closed"] = bool(old.get("closed") and row.get("closed"))
            direct = next((item for item in (old, row)
                           if item.get("url") and "simplify" not in str(item.get("sources") or "")), None)
            combined["url"] = (direct or primary).get("url") or secondary.get("url") or ""
            groups[index] = combined
        if req:
            req_index[req] = index
        title_index[title] = index
    for row in groups:
        url = str(row.get("url") or "")
        lowered = url.lower()
        row["nonpublic_site"] = (
            "only_confidential_executive_recruiting" in lowered
            or "private_posting_no_tmp" in lowered
        )
        row["public_apply_url"] = ""
        if "private_posting_no_tmp" in lowered and "_01866497" in lowered:
            row["public_apply_url"] = url.replace("/fr-CA/Private_Posting_No_TMP/", "/en-US/REC_RTX_Ext_Gateway/")
    return groups


def dashboard_postings(conn: psycopg.Connection, include_closed: bool = False) -> list[dict]:
    """Every posting needed by the application desk, collapsed to one job.

    Notification state and application state are deliberately independent. A
    notification says the scout surfaced a posting; application state records
    what the person did with it afterwards.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            with grouped as (
                select p.dedupe_key,
                       min(p.company) as company,
                       min(p.title) as title,
                       min(p.terms) as terms,
                       min(p.age_days) as age_days,
                       min(p.first_seen) as first_seen,
                       max(p.last_seen) as last_seen,
                       min(p.url) filter (where p.url <> '') as url,
                       string_agg(p.location, ';') as locations_raw,
                       string_agg(distinct p.source, ', ' order by p.source) as sources,
                       bool_and(p.closed) as closed,
                       bool_or(p.notified_at is not null) as notified,
                       max(p.score) as score,
                       min(p.deadline) filter (where p.deadline is not null) as deadline,
                       max(p.liveness_checked_at) as liveness_checked_at,
                       (array_agg(p.liveness_status order by p.liveness_checked_at desc nulls last)
                           filter (where p.liveness_status is not null))[1] as liveness_status,
                       (array_agg(p.liveness_evidence order by p.liveness_checked_at desc nulls last)
                           filter (where p.liveness_evidence is not null))[1] as liveness_evidence,
                       (array_agg(p.score_detail order by p.score desc nulls last)
                           filter (where p.score_detail is not null))[1] as score_detail
                  from postings p
                 where (%s or not p.closed)
                   and p.hidden_by is null
                   and p.board is distinct from 'testnisc'
                 group by p.dedupe_key
            ), application_events as (
                select h.dedupe_key, h.changed_at
                  from application_status_history h
                 where h.to_status = 'applied'
                union all
                select s.dedupe_key, s.updated_at
                  from application_states s
                 where s.status in ('applied', 'interviewing', 'offer', 'rejected')
                   and not exists (
                       select 1 from application_status_history h
                        where h.dedupe_key = s.dedupe_key
                          and h.to_status = 'applied'
                   )
            )
            select g.dedupe_key, coalesce(o.company, g.company) as company,
                   coalesce(o.title, g.title) as title, g.terms, g.age_days,
                   g.first_seen, g.last_seen, coalesce(o.url, g.url, '') as url,
                   g.sources, g.closed, g.notified, g.score, case when o.description_text is not null then o.deadline else g.deadline end as deadline,
                   g.liveness_status, g.liveness_checked_at, g.liveness_evidence,
                   coalesce(g.score_detail, '{}'::jsonb) as score_detail,
                   coalesce(s.status, 'new') as status,
                   coalesce(s.notes, '') as notes,
                   s.updated_at as application_updated_at,
                   s.queue_position, s.board_position, s.applied_at, s.resume_document_id, s.resume_name,
                   coalesce(s.next_step, '') as next_step,
                   rule_action.id as rule_action_id,
                   rule_action.name as archived_by_rule,
                   (
                       select max(e.changed_at)
                         from application_events e
                         join postings other on other.dedupe_key = e.dedupe_key
                        where lower(other.company) = lower(coalesce(o.company, g.company))
                          and other.dedupe_key <> g.dedupe_key
                          and other.hidden_by is null
                          and e.changed_at >= now() - interval '30 days'
                   ) as recent_company_application_at,
                   (select coalesce(
                       string_agg(distinct btrim(part), '; ' order by btrim(part)), '')
                      from unnest(string_to_array(g.locations_raw, ';')) as part
                     where btrim(part) <> '') as location
              from grouped g
              left join posting_overrides o on o.dedupe_key = g.dedupe_key
              left join application_states s on s.dedupe_key = g.dedupe_key
              left join lateral (
                  select a.id, r.name
                    from rule_actions a
                    join rules r on r.id = a.rule_id
                   where a.dedupe_key = g.dedupe_key
                     and a.action = 'archive' and a.undone_at is null
                   order by a.created_at desc, a.id desc
                   limit 1
              ) rule_action on true
             order by g.score desc nulls last, g.age_days asc nulls last,
                      lower(g.company), lower(g.title)
            """,
            (include_closed,),
        )
        return collapse_dashboard_duplicates(cur.fetchall())


def queued_description_texts(conn: psycopg.Connection) -> dict[str, str]:
    """One cached posting text per queued role for compact pay facts."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct on (p.dedupe_key) p.dedupe_key,
                   coalesce(o.description_text, p.description_text) as description_text
              from postings p
              join application_states s on s.dedupe_key = p.dedupe_key
              left join posting_overrides o on o.dedupe_key = p.dedupe_key
             where s.status = 'queued' and coalesce(o.description_text, p.description_text) is not null
             order by p.dedupe_key, (p.description_text is not null) desc, p.description_fetched_at desc nulls last
            """
        )
        return {row["dedupe_key"]: row["description_text"] for row in cur.fetchall()}


_VIEW_COLUMNS = "id, name, filters, sort, pinned, position, revision, created_at, updated_at"
VIEW_SORTS = frozenset({"score", "newest", "company"})
VIEW_FILTERS = ("query", "status", "source", "remote", "tag")


# a live view has the name
class ViewNameTaken(Exception):
    def __init__(self, view: dict):
        super().__init__(f"A view named “{view['name']}” already exists.")
        self.view = view


def normalize_view(payload: dict) -> dict:
    clean: dict = {}
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name or len(name) > 40:
            raise ValueError("view name must be between 1 and 40 characters")
        clean["name"] = name
    if "sort" in payload:
        if payload["sort"] not in VIEW_SORTS:
            raise ValueError("unknown saved-view sort")
        clean["sort"] = payload["sort"]
    if "filters" in payload:
        filters = payload["filters"]
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        clean["filters"] = {key: filters[key] for key in VIEW_FILTERS if key in filters}
    return clean


def saved_views(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        # waits out a reorder in flight
        cur.execute("select revision from saved_view_order for share")
        revision = cur.fetchone()["revision"]
        cur.execute(f"select {_VIEW_COLUMNS} from saved_views where deleted_at is null order by position, id")
        views = cur.fetchall()
    conn.commit()
    return {"saved_views": views, "order_revision": revision}


def _live_view_named(cur, name: str, other: int | None = None) -> dict | None:
    cur.execute(
        f"select {_VIEW_COLUMNS} from saved_views where name = %s and deleted_at is null and id is distinct from %s",
        (name, other),
    )
    return cur.fetchone()


def create_view(conn: psycopg.Connection, payload: dict) -> dict:
    clean = {"filters": {}, "sort": "score", **normalize_view(payload)}
    if "name" not in clean:
        raise ValueError("view name must be between 1 and 40 characters")
    with conn.cursor() as cur:
        # creates queue behind reorders
        cur.execute("select revision from saved_view_order for update")
        taken = _live_view_named(cur, clean["name"])
        if taken is not None:
            conn.rollback()
            raise ViewNameTaken(taken)
        cur.execute(
            f"""
            insert into saved_views (name, filters, sort, pinned, position)
            values (%s, %s, %s, true, (select coalesce(max(position) + 1, 0) from saved_views))
            returning {_VIEW_COLUMNS}
            """,
            (clean["name"], Json(clean["filters"]), clean["sort"]),
        )
        view = cur.fetchone()
    conn.commit()
    return view


def update_view(conn: psycopg.Connection, view_id: int, changes: dict, expected: int | None = None) -> dict | None:
    clean = normalize_view(changes)
    if not clean:
        raise ValueError("nothing to change")
    with conn.cursor() as cur:
        cur.execute("select revision from saved_view_order for update")
        cur.execute(
            f"select {_VIEW_COLUMNS} from saved_views where id = %s and deleted_at is null for update",
            (view_id,),
        )
        previous = cur.fetchone()
        if previous is None:
            conn.rollback()
            return None
        # undo must not undo another tab
        if expected is not None and previous["revision"] != expected:
            conn.rollback()
            raise RecordConflict(previous)
        if "name" in clean and _live_view_named(cur, clean["name"], view_id) is not None:
            conn.rollback()
            raise ValueError(f"A view named “{clean['name']}” already exists.")
        merged = {**previous, **clean}
        cur.execute(
            f"""
            update saved_views set name = %s, filters = %s, sort = %s,
                   revision = revision + 1, updated_at = now()
             where id = %s returning {_VIEW_COLUMNS}
            """,
            (merged["name"], Json(merged["filters"]), merged["sort"], view_id),
        )
        view = cur.fetchone()
    conn.commit()
    return {"view": view, "previous": previous}


def delete_saved_view(conn: psycopg.Connection, view_id: int) -> bool:
    # restore puts it back in place
    with conn.cursor() as cur:
        cur.execute(
            "update saved_views set deleted_at = now() where id = %s and deleted_at is null returning id",
            (view_id,),
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


def restore_saved_view(conn: psycopg.Connection, view_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("select revision from saved_view_order for update")
        cur.execute(
            f"select {_VIEW_COLUMNS} from saved_views where id = %s and deleted_at is not null for update",
            (view_id,),
        )
        view = cur.fetchone()
        if view is None:
            conn.rollback()
            return None
        if _live_view_named(cur, view["name"]) is not None:
            conn.rollback()
            raise ValueError(f"Another view is now named “{view['name']}”, so this one can't come back.")
        cur.execute(
            f"update saved_views set deleted_at = null where id = %s returning {_VIEW_COLUMNS}",
            (view_id,),
        )
        view = cur.fetchone()
    conn.commit()
    return view


def save_view_order(conn: psycopg.Connection, view_ids: list[int], expected: int) -> dict:
    if len(view_ids) != len(set(view_ids)) or len(view_ids) > 500:
        raise ValueError("view order contains duplicates or too many views")
    with conn.cursor() as cur:
        cur.execute("select revision from saved_view_order for update")
        revision = cur.fetchone()["revision"]
        cur.execute("select id from saved_views where deleted_at is null for update")
        # a stale tab has another order
        if revision != expected or {row["id"] for row in cur.fetchall()} != set(view_ids):
            conn.rollback()
            raise EditConflict("The saved views changed in another tab. Reload and retry.")
        cur.executemany(
            "update saved_views set position = %s where id = %s",
            list(enumerate(view_ids)),
        )
        cur.execute("update saved_view_order set revision = revision + 1 returning revision")
        revision = cur.fetchone()["revision"]
    conn.commit()
    return {"ids": view_ids, "order_revision": revision}


class ProfileConflict(Exception):
    """The profile changed since the client read it."""


def bump_profile_revision(cur, expected: int | None = None) -> None:
    cur.execute("select revision from profile_meta where id for update")
    current = cur.fetchone()["revision"]
    if expected is not None and expected != current:
        raise ProfileConflict("profile is at revision %d, not %d" % (current, expected))
    cur.execute("update profile_meta set revision = revision + 1 where id")


def profile_data(
    conn: psycopg.Connection,
    *,
    dedupe_key: str | None = None,
    company: str | None = None,
) -> dict:
    with conn.cursor() as cur:
        cur.execute("select revision from profile_meta where id")
        revision = cur.fetchone()["revision"]
        cur.execute(
            """
            select key, group_name as "group", label, value, pinned, sort_order
              from profile_fields
             order by sort_order, key
            """
        )
        fields = cur.fetchall()
        cur.execute(
            """
            select id, name, body, sort_order
              from answer_templates
             order by sort_order, lower(name), id
            """
        )
        templates = cur.fetchall()
        cur.execute(
            """
            select d.id, d.name, d.document_date as date, d.url, d.is_default as "default", d.sort_order,
                   (select count(*) from (
                        select dedupe_key from application_states where resume_document_id = d.id
                        union
                        select dedupe_key from application_snapshots where resume_document_id = d.id
                   ) refs) as application_count
              from documents d
             order by d.sort_order, d.id
            """
        )
        documents = cur.fetchall()
        cur.execute(
            """
            select key as id, title, situation, task, action, result, reflection,
                   competencies, sort_order
              from story_bank
             order by sort_order, lower(title), key
            """
        )
        stories = cur.fetchall()
        copied_fields = []
        answer_overrides = {}
        if dedupe_key:
            cur.execute(
                "select target_key from quick_fill_copy_state where dedupe_key = %s order by target_key",
                (dedupe_key,),
            )
            copied_fields = [row["target_key"] for row in cur.fetchall()]
            cur.execute(
                "select template_name, body from answer_template_overrides where dedupe_key = %s",
                (dedupe_key,),
            )
            answer_overrides = {row["template_name"]: row["body"] for row in cur.fetchall()}
        company_account = None
        if company:
            key = resolve_company(company_rows(cur), company) or company_key(company)
            cur.execute(
                """
                select company_name, account_exists, sign_in_email, password_manager_url, updated_at
                  from company_accounts
                 where company_key = any(%s)
                 order by (company_key = %s) desc
                 limit 1
                """,
                ([key, company.casefold().strip()], key),
            )
            company_account = cur.fetchone()
    return {
        "revision": revision,
        "fields": fields,
        "answer_templates": templates,
        "documents": documents,
        "stories": stories,
        "copied_fields": copied_fields,
        "answer_overrides": answer_overrides,
        "company_account": company_account,
    }


def mark_quick_fill_copy(conn: psycopg.Connection, dedupe_key: str, target_key: str) -> None:
    if not dedupe_key or len(dedupe_key) > 500 or not target_key or len(target_key) > 200:
        raise ValueError("invalid job or copy target")
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into quick_fill_copy_state (dedupe_key, target_key)
            values (%s, %s)
            on conflict (dedupe_key, target_key) do update set copied_at = now()
            """,
            (dedupe_key, target_key),
        )
    conn.commit()


def clean_context_keys(dedupe_key: str, company: str) -> tuple[str, str]:
    dedupe_key, company = dedupe_key.strip(), company.strip()
    if not dedupe_key or len(dedupe_key) > 500 or not company or len(company) > 240:
        raise ValueError("invalid job or company")
    return dedupe_key, company


def _clean_override(name, body) -> tuple[str, str]:
    clean_name = str(name).strip()
    clean_body = str(body)
    if not clean_name or len(clean_name) > 120 or len(clean_body) > 10_000:
        raise ValueError("answer override name or body is invalid")
    return clean_name, clean_body


def _clean_account(company_account) -> dict:
    if not isinstance(company_account, dict):
        raise ValueError("company_account must be an object")
    exists = company_account.get("account_exists")
    if exists not in {True, False, None}:
        raise ValueError("account_exists must be true, false, or null")
    email = str(company_account.get("sign_in_email", "")).strip()
    manager_url = str(company_account.get("password_manager_url", "")).strip()
    if len(email) > 320 or len(manager_url) > 2_000:
        raise ValueError("company account email or URL is too long")
    if manager_url:
        parsed = urllib.parse.urlsplit(manager_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("password manager link must be a valid HTTPS URL")
    return {"account_exists": exists, "sign_in_email": email, "password_manager_url": manager_url}


ACCOUNT_FIELDS = ("account_exists", "sign_in_email", "password_manager_url")
_ACCOUNT_BLANK = {"account_exists": None, "sign_in_email": "", "password_manager_url": ""}


def normalize_quick_fill_field(field) -> dict:
    # one override or account property
    if not isinstance(field, dict) or field.get("kind") not in {"override", "account"}:
        raise ValueError("field must be an override or an account property")
    if field["kind"] == "override":
        name, value = _clean_override(field.get("name"), field.get("value") or "")
    else:
        name = field.get("name")
        if name not in ACCOUNT_FIELDS:
            raise ValueError("unknown company account property")
        value = _clean_account({name: field.get("value")})[name]
    clean = {"kind": field["kind"], "name": name, "value": value}
    if "expected" in field:
        expected = field["expected"]
        if name == "account_exists" and expected not in {True, False, None}:
            raise ValueError("expected must be true, false, or null")
        if name != "account_exists" and not isinstance(expected, str):
            raise ValueError("expected must be text")
        clean["expected"] = expected
    return clean


def save_quick_fill_field(conn: psycopg.Connection, *, dedupe_key: str, company: str, field: dict) -> dict | None:
    # newer values elsewhere stay put
    clean = normalize_quick_fill_field(field)
    dedupe_key, company = clean_context_keys(dedupe_key, company)
    name = clean["name"]
    with conn.cursor() as cur:
        if clean["kind"] == "override":
            if not _lock_job(cur, dedupe_key):
                conn.rollback()
                return None
            cur.execute(
                "select body from answer_template_overrides where dedupe_key = %s and template_name = %s",
                (dedupe_key, name),
            )
            current = (cur.fetchone() or {"body": ""})["body"]
        else:
            key = resolve_company(_read_companies(cur), company) or company_key(company)
            # no row to lock before the first write
            cur.execute("select pg_advisory_xact_lock(hashtext('company_accounts:' || %s))", (key,))
            cur.execute(
                "select account_exists, sign_in_email, password_manager_url from company_accounts where company_key = %s",
                (key,),
            )
            current = {**_ACCOUNT_BLANK, **(cur.fetchone() or {})}[name]
        if "expected" in clean and current != clean["expected"]:
            conn.rollback()
            raise ValueConflict(current)
        # undo needs what this wrote
        if clean["kind"] == "override" and clean["value"]:
            cur.execute(
                """
                insert into answer_template_overrides (dedupe_key, template_name, body) values (%s, %s, %s)
                on conflict (dedupe_key, template_name) do update set body = excluded.body, updated_at = now()
                returning body as value
                """,
                (dedupe_key, name, clean["value"]),
            )
            written = cur.fetchone()["value"]
        elif clean["kind"] == "override":
            cur.execute("delete from answer_template_overrides where dedupe_key = %s and template_name = %s", (dedupe_key, name))
            written = ""
        else:
            # only the named column, ACCOUNT_FIELDS checked
            cur.execute(
                f"""
                insert into company_accounts (company_key, company_name, {name}) values (%s, %s, %s)
                on conflict (company_key) do update set
                    company_name = excluded.company_name, {name} = excluded.{name}, updated_at = now()
                returning {name} as value
                """,
                (key, company, clean["value"]),
            )
            written = cur.fetchone()["value"]
    conn.commit()
    return {"profile": profile_data(conn, dedupe_key=dedupe_key, company=company), "value": written}


def normalize_profile_data(payload: dict) -> dict:
    """Validate the complete Quick-fill document before replacing any rows."""
    if not isinstance(payload, dict):
        raise ValueError("profile must be an object")

    def objects(name: str, limit: int) -> list[dict]:
        value = payload.get(name, [])
        if not isinstance(value, list) or len(value) > limit or not all(isinstance(row, dict) for row in value):
            raise ValueError(f"{name} must be a list of at most {limit} objects")
        return value

    fields = []
    seen_keys: set[str] = set()
    for index, row in enumerate(objects("fields", 100)):
        key = str(row.get("key", "")).strip()
        label = str(row.get("label", "")).strip()
        group = str(row.get("group", "")).strip()
        value = str(row.get("value", ""))
        if not key or len(key) > 80 or key in seen_keys:
            raise ValueError(f"fields[{index}].key is missing, duplicate, or too long")
        if not label or len(label) > 120 or not group or len(group) > 80 or len(value) > 10_000:
            raise ValueError(f"fields[{index}] has an invalid label, group, or value")
        seen_keys.add(key)
        fields.append({
            "key": key, "label": label, "group": group, "value": value,
            "pinned": bool(row.get("pinned", False)), "sort_order": int(row.get("sort_order", index * 10)),
        })

    templates = []
    seen_names: set[str] = set()
    for index, row in enumerate(objects("answer_templates", 50)):
        name = str(row.get("name", "")).strip()
        body = str(row.get("body", ""))
        if not name or len(name) > 120 or name.casefold() in seen_names or len(body) > 10_000:
            raise ValueError(f"answer_templates[{index}] has an invalid or duplicate name/body")
        seen_names.add(name.casefold())
        templates.append({
            "id": row.get("id"), "name": name, "body": body,
            "sort_order": int(row.get("sort_order", index * 10)),
        })

    documents = []
    for index, row in enumerate(objects("documents", 50)):
        name = str(row.get("name", "")).strip()
        url = str(row.get("url") or "")
        date = str(row.get("date") or "").strip() or None  # null would become "None"
        if not name or len(name) > 160 or len(url) > 2_000:
            raise ValueError(f"documents[{index}] has an invalid name or URL")
        if date:
            try:
                dt.date.fromisoformat(date)
            except ValueError as error:
                raise ValueError(f"documents[{index}].date must be YYYY-MM-DD") from error
        documents.append({
            "id": row.get("id"),
            "name": name, "url": url, "date": date, "default": row.get("default") is True,
            "sort_order": int(row.get("sort_order", index * 10)),
        })
    if sum(row["default"] for row in documents) > 1:
        raise ValueError("only one document can be the default")

    stories = []
    seen_story_keys: set[str] = set()
    for index, row in enumerate(objects("stories", 10)):
        key = str(row.get("id", row.get("key", ""))).strip()
        title = str(row.get("title", "")).strip()
        competencies = row.get("competencies", [])
        if not key or len(key) > 80 or key in seen_story_keys or not title or len(title) > 160:
            raise ValueError(f"stories[{index}] has an invalid or duplicate id/title")
        if not isinstance(competencies, list) or len(competencies) > 12:
            raise ValueError(f"stories[{index}].competencies must be a list")
        clean_competencies = [str(value).strip() for value in competencies if str(value).strip()]
        if any(len(value) > 60 for value in clean_competencies):
            raise ValueError(f"stories[{index}] has a competency tag that is too long")
        story = {
            "id": key, "title": title, "competencies": clean_competencies,
            "sort_order": int(row.get("sort_order", index * 10)),
        }
        for part in ("situation", "task", "action", "result", "reflection"):
            value = str(row.get(part, ""))
            if len(value) > 5_000:
                raise ValueError(f"stories[{index}].{part} is too long")
            story[part] = value
        seen_story_keys.add(key)
        stories.append(story)

    return {"fields": fields, "answer_templates": templates, "documents": documents, "stories": stories}


def _row_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _sync_templates(cur, templates: list[dict]) -> None:
    cur.execute("select id, name from answer_templates")
    existing = {row["id"]: row["name"] for row in cur.fetchall()}
    kept = {_row_id(row["id"]) for row in templates} & existing.keys()
    # orphaned overrides let undo reconnect
    cur.execute("delete from answer_templates where not (id = any(%s))", (list(kept),))
    renames = {_row_id(row["id"]): row["name"] for row in templates if _row_id(row["id"]) in kept and existing[_row_id(row["id"])] != row["name"]}
    # names are unique, park renames first
    for key in renames:
        cur.execute("update answer_templates set name = %s where id = %s", ("\x01%d" % key, key))
        cur.execute("update answer_template_overrides set template_name = %s where template_name = %s", ("\x01%d" % key, existing[key]))
    for key, name in renames.items():
        # the live answer beats orphans
        cur.execute(
            """
            delete from answer_template_overrides o
             where o.template_name = %s
               and exists (select 1 from answer_template_overrides p where p.dedupe_key = o.dedupe_key and p.template_name = %s)
            """,
            (name, "\x01%d" % key),
        )
        cur.execute("update answer_template_overrides set template_name = %s where template_name = %s", (name, "\x01%d" % key))
    for row in templates:
        key = _row_id(row["id"])
        if key in kept:
            cur.execute(
                "update answer_templates set name = %s, body = %s, sort_order = %s, updated_at = now() where id = %s",
                (row["name"], row["body"], row["sort_order"], key),
            )
        else:
            cur.execute(
                "insert into answer_templates (name, body, sort_order) values (%s, %s, %s)",
                (row["name"], row["body"], row["sort_order"]),
            )


def _sync_documents(cur, documents: list[dict]) -> None:
    cur.execute("select id from documents")
    existing = {row["id"] for row in cur.fetchall()}
    kept = {_row_id(row["id"]) for row in documents} & existing
    # a delete nulls resume references
    cur.execute("delete from documents where not (id = any(%s))", (list(kept),))
    default = None
    for row in documents:
        key = _row_id(row["id"])
        if key in kept:
            cur.execute(
                "update documents set name = %s, document_date = %s, url = %s, sort_order = %s where id = %s",
                (row["name"], row["date"], row["url"], row["sort_order"], key),
            )
        else:
            cur.execute(
                "insert into documents (name, document_date, url, sort_order) values (%s, %s, %s, %s) returning id",
                (row["name"], row["date"], row["url"], row["sort_order"]),
            )
            key = cur.fetchone()["id"]
        if row["default"]:
            default = key
    # clear first for the unique index
    cur.execute("update documents set is_default = false where is_default and id is distinct from %s", (default,))
    cur.execute("update documents set is_default = true where id = %s", (default,))


def replace_profile_data(conn: psycopg.Connection, payload: dict, *, revision: int | None = None) -> dict:
    """Atomically replace the feature-gated single-user Quick-fill profile."""
    clean = normalize_profile_data(payload)
    with conn.cursor() as cur:
        try:
            bump_profile_revision(cur, revision)
        except ProfileConflict:
            conn.rollback()
            raise
        cur.execute("delete from profile_fields")
        cur.executemany(
            """
            insert into profile_fields (key, group_name, label, value, pinned, sort_order)
            values (%s, %s, %s, %s, %s, %s)
            """,
            [(row["key"], row["group"], row["label"], row["value"], row["pinned"], row["sort_order"]) for row in clean["fields"]],
        )
        # ids are referenced, keep them stable
        _sync_templates(cur, clean["answer_templates"])
        _sync_documents(cur, clean["documents"])
        cur.execute("delete from story_bank")
        cur.executemany(
            """
            insert into story_bank
                (key, title, situation, task, action, result, reflection, competencies, sort_order)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(
                row["id"], row["title"], row["situation"], row["task"], row["action"],
                row["result"], row["reflection"], row["competencies"], row["sort_order"],
            ) for row in clean["stories"]],
        )
    conn.commit()
    return profile_data(conn)


def description_target(conn: psycopg.Connection, dedupe_key: str) -> dict | None:
    """Best source row to use for an ATS detail request."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, dedupe_key, source, source_id, board, url
              from postings
             where dedupe_key = %s
             order by
                 case when source = split_part(dedupe_key, ':', 1) then 0 else 1 end,
                 case source
                     when 'greenhouse' then 0
                     when 'lever' then 1
                     when 'ashby' then 2
                     when 'workday' then 3
                     else 4
                 end,
                 id
             limit 1
            """,
            (dedupe_key,),
        )
        return cur.fetchone()


def cached_description(conn: psycopg.Connection, dedupe_key: str) -> dict | None:
    """Cached provider detail for a deduplicated job, if it exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select description_html, description_text,
                   coalesce(description_sections, '[]'::jsonb) as sections,
                   description_fetched_at, description_error,
                   deadline, deadline_source
              from postings
             where dedupe_key = %s
               and description_fetched_at is not null
             order by (description_text is not null) desc,
                      description_fetched_at desc
             limit 1
            """,
            (dedupe_key,),
        )
        fetched = cur.fetchone()
        cur.execute(
            """
            select description_text, coalesce(description_sections, '[]'::jsonb) as sections,
                   deadline, deadline_source, updated_at
              from posting_overrides where dedupe_key = %s and description_text is not null
            """,
            (dedupe_key,),
        )
        corrected = cur.fetchone()
    if corrected is None:
        return fetched
    # provider fetches land underneath
    fetched = fetched or {}
    return {
        **fetched, "description_html": None, "description_text": corrected["description_text"],
        "sections": corrected["sections"], "description_fetched_at": corrected["updated_at"],
        "description_error": None,
        "deadline": corrected["deadline"], "deadline_source": corrected["deadline_source"],
    }


def save_description(
    conn: psycopg.Connection,
    posting_id: int,
    *,
    html: str | None,
    text: str | None,
    sections: tuple[dict[str, str], ...] = (),
    deadline=None,
    deadline_source: str | None = None,
    error: str | None = None,
) -> None:
    """Cache one provider response without overwriting a manual deadline."""
    with conn.cursor() as cur:
        cur.execute("select dedupe_key, description_text from postings where id = %s", (posting_id,))
        previous = cur.fetchone()
        cur.execute(
            """
            update postings
               set description_html = %s,
                   description_text = %s,
                   description_sections = %s,
                   description_fetched_at = now(),
                   description_error = %s,
                   deadline = case
                       when deadline_source = 'manual' then deadline
                       else %s
                   end,
                   deadline_source = case
                       when deadline_source = 'manual' then deadline_source
                       else %s
                   end
             where id = %s
            """,
            (html, text, Json(list(sections)), error, deadline, deadline_source, posting_id),
        )
        if previous and previous["description_text"] != text:
            _requeue_overview(cur, previous["dedupe_key"])
    conn.commit()


def _requeue_overview(cur, dedupe_key: str) -> None:
    cur.execute(
        """update overview_jobs set status = 'queued', items = null, source = null,
                  source_hash = null, attempts = 0, error = null, previous = null,
                  revision = revision + 1, dismissed_at = null,
                  available_at = now(), requested_at = now(), updated_at = now()
             where dedupe_key = %s and status = 'ready'""",
        (dedupe_key,),
    )


class EditConflict(Exception):
    """A later change would be lost by this undo."""


POSTING_EDIT_FIELDS = ("company", "title", "url")
OVERRIDE_COLUMNS = (*POSTING_EDIT_FIELDS, "description_text", "description_sections", "deadline", "deadline_source")


def _lock_job(cur, dedupe_key: str) -> bool:
    # first edits have no override row
    cur.execute("select id from postings where dedupe_key = %s order by id for update", (dedupe_key,))
    return bool(cur.fetchall())


def edit_posting(conn: psycopg.Connection, dedupe_key: str, fields: dict[str, str], description=None) -> dict | None:
    """Override a job's identity fields and correct its description.

    Returns ``{"id": None}`` when nothing changed and None for an unknown job.
    """
    with conn.cursor() as cur:
        if "company" in fields:
            lock_companies(cur)
        if not _lock_job(cur, dedupe_key):
            conn.rollback()
            return None
        cur.execute(
            """
            select min(company) as company, min(title) as title,
                   coalesce(min(url) filter (where url <> ''), '') as url
              from postings where dedupe_key = %s
            """,
            (dedupe_key,),
        )
        base = cur.fetchone()
        # scout refreshes rewrite postings rows
        cur.execute(f"select {', '.join(OVERRIDE_COLUMNS)} from posting_overrides where dedupe_key = %s", (dedupe_key,))
        override = cur.fetchone()
        values = dict(override) if override else dict.fromkeys(OVERRIDE_COLUMNS)
        current = {name: values[name] or base[name] for name in POSTING_EDIT_FIELDS}
        changed = {name: value for name, value in fields.items() if value != current[name]}
        values.update(changed)
        described = description is not None and (
            (cached_description(conn, dedupe_key) or {}).get("description_text") != description.text
        )
        if described:
            values.update(
                description_text=description.text, description_sections=list(description.sections),
                deadline=description.deadline, deadline_source=description.deadline_source,
            )
        if not changed and not described:
            conn.rollback()
            return {"id": None}
        _write_override(cur, dedupe_key, values)
        if "company" in changed and company_key(changed["company"]):
            _ensure_companies(cur, {company_key(changed["company"]): changed["company"]}, refresh=False)
        if described:
            _description_changed(cur, dedupe_key)
        before = {"override": {
            name: value.isoformat() if isinstance(value, dt.date) else value for name, value in override.items()
        } if override else None}
        cur.execute(
            "insert into posting_edits (dedupe_key, before) values (%s, %s) returning id",
            (dedupe_key, Json(before)),
        )
        edit = cur.fetchone()
    conn.commit()
    return {"id": edit["id"]}


def _description_changed(cur, dedupe_key: str) -> None:
    _requeue_overview(cur, dedupe_key)
    # a blank hash reads as pending
    cur.execute(
        "update posting_taglines set prompt_hash = '', attempts = 0, available_at = now() where dedupe_key = %s",
        (dedupe_key,),
    )


def _write_override(cur, dedupe_key: str, values: dict | None) -> None:
    if values is None or all(values.get(name) is None for name in OVERRIDE_COLUMNS):
        cur.execute("delete from posting_overrides where dedupe_key = %s", (dedupe_key,))
        return
    sections = values.get("description_sections")
    cur.execute(
        f"""
        insert into posting_overrides (dedupe_key, {', '.join(OVERRIDE_COLUMNS)})
        values (%s, {', '.join(['%s'] * len(OVERRIDE_COLUMNS))})
        on conflict (dedupe_key) do update set
            {', '.join(f"{name} = excluded.{name}" for name in OVERRIDE_COLUMNS)}, updated_at = now()
        """,
        (dedupe_key, *(Json(sections) if name == "description_sections" and sections is not None else values.get(name)
                       for name in OVERRIDE_COLUMNS)),
    )


def undo_posting_edit(conn: psycopg.Connection, edit_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("select dedupe_key from posting_edits where id = %s", (edit_id,))
        found = cur.fetchone()
        if found is None:
            return None
        _lock_job(cur, found["dedupe_key"])
        cur.execute("select id, dedupe_key, before, undone_at from posting_edits where id = %s", (edit_id,))
        edit = cur.fetchone()
        cur.execute(
            "select max(id) as id from posting_edits where dedupe_key = %s and undone_at is null",
            (edit["dedupe_key"],),
        )
        # an older undo would drop newer edits
        if edit["undone_at"] is not None or cur.fetchone()["id"] != edit_id:
            conn.rollback()
            raise EditConflict("This posting changed after that edit, so it can't be undone.")
        cur.execute("select description_text from posting_overrides where dedupe_key = %s", (edit["dedupe_key"],))
        shown = (cur.fetchone() or {}).get("description_text")
        restored = edit["before"]["override"]
        _write_override(cur, edit["dedupe_key"], restored)
        if shown != (restored or {}).get("description_text"):
            _description_changed(cur, edit["dedupe_key"])
        cur.execute("update posting_edits set undone_at = now() where id = %s", (edit_id,))
    conn.commit()
    return {"id": edit_id, "dedupe_key": edit["dedupe_key"]}


# job moved since caller looked
class StatusConflict(Exception):
    def __init__(self, status: str):
        super().__init__("This job changed in another tab.")
        self.status = status


# text changed since caller looked
class ValueConflict(Exception):
    def __init__(self, value: str):
        super().__init__("It changed in another tab.")
        self.value = value


# expected guards undo against newer moves
def save_application_state(
    conn: psycopg.Connection,
    dedupe_key: str,
    status: str | None,
    notes: str | None,
    expected: str | None = None,
    expected_notes: str | None = None,
) -> str | None:
    # none means an unknown job
    if status is not None and status not in APPLICATION_STATUSES:
        raise ValueError(f"unknown application status: {status}")
    with conn.cursor() as cur:
        if not _lock_job(cur, dedupe_key):
            conn.rollback()
            return None
        cur.execute(
            "select status, notes from application_states where dedupe_key = %s",
            (dedupe_key,),
        )
        existing = cur.fetchone()
        previous = existing["status"] if existing else "new"
        previous_notes = existing["notes"] if existing else ""
        if expected is not None and previous != expected:
            conn.rollback()
            raise StatusConflict(previous)
        if expected_notes is not None and previous_notes != expected_notes:
            conn.rollback()
            raise ValueConflict(previous_notes)
        if status is None:
            status = previous
        if notes is None:
            notes = previous_notes
        cur.execute(
            """
            insert into application_states (dedupe_key, status, notes)
            values (%s, %s, %s)
            on conflict (dedupe_key) do update set
                status = excluded.status,
                notes = excluded.notes,
                queue_position = case
                    when excluded.status = 'queued' then application_states.queue_position
                    else null
                end,
                board_position = case
                    when excluded.status = application_states.status then application_states.board_position
                    else null
                end,
                applied_at = case
                    when excluded.status = 'applied'
                        then coalesce(application_states.applied_at, now())
                    else application_states.applied_at
                end,
                updated_at = now()
            """,
            (dedupe_key, status, notes),
        )
        if status == "applied":
            cur.execute(
                """
                update application_states
                   set applied_at = coalesce(applied_at, now())
                 where dedupe_key = %s
                """,
                (dedupe_key,),
            )
        if status != "archived":
            cur.execute(
                """
                update rule_actions set undone_at = now()
                 where dedupe_key = %s and action = 'archive' and undone_at is null
                """,
                (dedupe_key,),
            )
        if previous != status:
            cur.execute(
                """
                insert into application_status_history
                    (dedupe_key, from_status, to_status)
                values (%s, %s, %s)
                """,
                (dedupe_key, previous, status),
            )
        if status == "applied" and previous != "applied":
            cur.execute(
                """
                insert into reminders (dedupe_key, kind, due_at)
                values (%s, 'follow_up', now() + interval '7 days')
                on conflict (dedupe_key, kind) where kind <> 'manual' do update set
                    due_at = excluded.due_at, status = 'pending',
                    snoozed_until = null, completed_at = null
                """,
                (dedupe_key,),
            )
        elif previous == "applied" and (status != "applied" or notes != previous_notes):
            cur.execute(
                """
                update reminders set status = 'done', completed_at = now()
                 where dedupe_key = %s and kind = 'follow_up' and status <> 'done'
                """,
                (dedupe_key,),
            )
    conn.commit()
    return status


def liveness_target(conn: psycopg.Connection, dedupe_key: str) -> dict | None:
    """Return the preferred source plus the latest cached liveness result."""
    target = description_target(conn, dedupe_key)
    if target is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            select liveness_status, liveness_checked_at, liveness_evidence
              from postings
             where dedupe_key = %s and liveness_checked_at is not null
             order by liveness_checked_at desc
             limit 1
            """,
            (dedupe_key,),
        )
        cached = cur.fetchone()
    return {**target, **(cached or {})}


def save_liveness(
    conn: psycopg.Connection, dedupe_key: str, status: str, evidence: str
) -> None:
    if status not in {"live", "closed", "unknown"}:
        raise ValueError("unknown liveness status")
    with conn.cursor() as cur:
        cur.execute(
            """
            update postings
               set liveness_status = %s,
                   liveness_checked_at = now(),
                   liveness_evidence = %s
             where dedupe_key = %s
            """,
            (status, evidence[:500], dedupe_key),
        )
    conn.commit()


def save_queue_order(conn: psycopg.Connection, dedupe_keys: list[str]) -> list[str]:
    if len(dedupe_keys) != len(set(dedupe_keys)) or len(dedupe_keys) > 2_000:
        raise ValueError("queue order contains duplicates or too many jobs")
    with conn.cursor() as cur:
        cur.execute(
            """
            select s.dedupe_key from application_states s
             where s.status = 'queued'
               and not exists (
                   select 1 from postings p
                    where p.dedupe_key = s.dedupe_key and p.hidden_by is not null
               )
            """
        )
        queued = {row["dedupe_key"] for row in cur.fetchall()}
        if queued != set(dedupe_keys):
            raise ValueError("queue order must contain every queued job exactly once")
        cur.executemany(
            "update application_states set queue_position = %s, updated_at = now() where dedupe_key = %s",
            [(position, key) for position, key in enumerate(dedupe_keys)],
        )
    conn.commit()
    return dedupe_keys


def save_board_order(conn: psycopg.Connection, status: str, dedupe_keys: list[str]) -> list[str]:
    # filtered boards send partial columns
    if status not in BOARD_STATUSES:
        raise ValueError(f"not a board column: {status}")
    if len(dedupe_keys) != len(set(dedupe_keys)) or len(dedupe_keys) > 2_000:
        raise ValueError("board order contains duplicates or too many jobs")
    with conn.cursor() as cur:
        cur.execute("select dedupe_key from application_states where status = %s for update", (status,))
        column = {row["dedupe_key"] for row in cur.fetchall()}
        if not set(dedupe_keys) <= column:
            raise ValueError("board order must only contain jobs in that column")
        cur.execute("update application_states set board_position = null where status = %s", (status,))
        cur.executemany(
            "update application_states set board_position = %s where dedupe_key = %s",
            [(position, key) for position, key in enumerate(dedupe_keys)],
        )
    conn.commit()
    return dedupe_keys


def mark_application_applied(
    conn: psycopg.Connection, dedupe_key: str, document_id: int | None = None
) -> dict | None:
    """Atomically mark applied and snapshot the posting and chosen resume."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select min(company) as company, min(title) as title,
                   min(terms) as terms, min(url) filter (where url <> '') as url,
                   string_agg(location, ';') as locations_raw,
                   string_agg(distinct source, ', ' order by source) as sources,
                   min(deadline) filter (where deadline is not null) as deadline,
                   (array_agg(description_text order by description_fetched_at desc nulls last)
                       filter (where description_text is not null))[1] as description_text,
                   (array_agg(description_sections order by description_fetched_at desc nulls last)
                       filter (where description_sections is not null))[1] as description_sections
              from postings
             where dedupe_key = %s
             group by dedupe_key
            """,
            (dedupe_key,),
        )
        posting = cur.fetchone()
        if posting is None:
            return None
        cur.execute(f"select {', '.join(OVERRIDE_COLUMNS)} from posting_overrides where dedupe_key = %s", (dedupe_key,))
        override = cur.fetchone() or {}
        posting.update({name: override[name] for name in POSTING_EDIT_FIELDS if override.get(name) is not None})
        # a correction owns its deadline
        if override.get("description_text") is not None:
            posting.update({name: override[name] for name in ("description_text", "description_sections", "deadline")})
        document = None
        if document_id is not None:
            cur.execute(
                "select id, name, document_date from documents where id = %s",
                (document_id,),
            )
            document = cur.fetchone()
            if document is None:
                raise ValueError("resume version not found")
        cur.execute(
            "select status from application_states where dedupe_key = %s for update",
            (dedupe_key,),
        )
        existing = cur.fetchone()
        previous = existing["status"] if existing else "new"
        if previous == "applied":
            cur.execute(
                "select * from application_snapshots where dedupe_key = %s",
                (dedupe_key,),
            )
            existing_snapshot = cur.fetchone()
            if existing_snapshot is not None:
                return existing_snapshot
        resume_name = document["name"] if document else None
        cur.execute(
            """
            insert into application_states
                (dedupe_key, status, applied_at, resume_document_id, resume_name)
            values (%s, 'applied', now(), %s, %s)
            on conflict (dedupe_key) do update set
                status = 'applied',
                applied_at = coalesce(application_states.applied_at, now()),
                queue_position = null,
                board_position = case
                    when application_states.status = 'applied' then application_states.board_position
                    else null
                end,
                resume_document_id = excluded.resume_document_id,
                resume_name = excluded.resume_name, updated_at = now()
            """,
            (dedupe_key, document_id, resume_name),
        )
        if previous != "applied":
            cur.execute(
                """
                insert into application_status_history (dedupe_key, from_status, to_status)
                values (%s, %s, 'applied')
                """,
                (dedupe_key, previous),
            )
        cur.execute(
            """
            insert into reminders (dedupe_key, kind, due_at)
            values (%s, 'follow_up', now() + interval '7 days')
            on conflict (dedupe_key, kind) where kind <> 'manual' do update set
                due_at = excluded.due_at, status = 'pending',
                snoozed_until = null, completed_at = null
            """,
            (dedupe_key,),
        )
        locations = merge_locations(posting["locations_raw"] or "")
        sections = posting["description_sections"] or []
        cur.execute(
            """
            insert into application_snapshots
                (dedupe_key, company, title, location, terms, url, sources,
                 deadline, description_text, description_sections,
                 resume_document_id, resume_name)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (dedupe_key) do nothing
            returning *
            """,
            (
                dedupe_key, posting["company"], posting["title"], locations,
                posting["terms"] or "", posting["url"] or "", posting["sources"] or "",
                posting["deadline"], posting["description_text"], Json(sections),
                document_id, resume_name,
            ),
        )
        snapshot = cur.fetchone()
        if snapshot is None:
            cur.execute(
                "select * from application_snapshots where dedupe_key = %s",
                (dedupe_key,),
            )
            snapshot = cur.fetchone()
    conn.commit()
    return snapshot


_SESSION_COLUMNS = """
    id, keys, applied, skipped, started_at, ended_at, revision,
    extract(epoch from coalesce(ended_at, now()) - started_at)::integer as elapsed_seconds
"""


def start_apply_session(conn: psycopg.Connection, keys: list[str]) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"insert into apply_sessions (keys) values (%s) returning {_SESSION_COLUMNS}", (keys,))
        session = cur.fetchone()
    conn.commit()
    return session


def apply_session(conn: psycopg.Connection) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(f"select {_SESSION_COLUMNS} from apply_sessions order by id desc limit 1")
        return cur.fetchone()


def check_session_changes(keys: list[str], changes: dict) -> None:
    allowed = set(keys)
    if not set(changes.get("applied", [])) <= allowed:
        raise ValueError("applied jobs must be in the session")
    if not {item["dedupe_key"] for item in changes.get("skipped", [])} <= allowed:
        raise ValueError("skipped jobs must be in the session")


def update_apply_session(conn: psycopg.Connection, session_id: int, changes: dict, expected: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(f"select {_SESSION_COLUMNS} from apply_sessions where id = %s for update", (session_id,))
        current = cur.fetchone()
        if current is None:
            return None
        # another tab moved the session
        if current["revision"] != expected:
            conn.rollback()
            raise RecordConflict(current)
        check_session_changes(current["keys"], changes)
        ended = changes.get("ended")
        cur.execute(
            f"""
            update apply_sessions
               set applied = coalesce(%s, applied), skipped = coalesce(%s, skipped),
                   started_at = case when %s::boolean is false and ended_at is not null
                       then started_at + (now() - ended_at) else started_at end,
                   ended_at = case
                       when %s::boolean is null then ended_at
                       when %s::boolean then coalesce(ended_at, now())
                       else null
                   end,
                   revision = revision + 1
             where id = %s
            returning {_SESSION_COLUMNS}
            """,
            (changes.get("applied"), Json(changes["skipped"]) if "skipped" in changes else None, ended, ended, ended, session_id),
        )
        session = cur.fetchone()
    conn.commit()
    return session


def contact_counts(conn: psycopg.Connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select company_key, count(*)::integer as count
              from contacts
             where source = 'linkedin_csv' and deleted_at is null
             group by company_key
            """
        )
        return {row["company_key"]: row["count"] for row in cur.fetchall()}


_INTERVIEWS = """
    select i.id, i.dedupe_key, i.starts_at, i.ends_at, i.location, i.notes, i.cancelled_at, i.revision,
           coalesce(min(o.company), min(p.company)) as company,
           coalesce(min(o.title), min(p.title)) as title
      from interviews i
      join postings p on p.dedupe_key = i.dedupe_key
      left join posting_overrides o on o.dedupe_key = i.dedupe_key
     where p.hidden_by is null and {where}
     group by i.id
     order by i.starts_at, i.id
"""


def tracker_support(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            select r.id, r.dedupe_key, r.kind, r.due_at, r.status, r.snoozed_until,
                   r.completed_at, r.note, r.revision,
                   coalesce(min(o.company), min(p.company)) as company,
                   coalesce(min(o.title), min(p.title)) as title
              from reminders r
              join postings p on p.dedupe_key = r.dedupe_key
              left join posting_overrides o on o.dedupe_key = r.dedupe_key
             where p.hidden_by is null
               and (r.status <> 'done' or r.completed_at > now() - interval '14 days')
             group by r.id
             order by coalesce(r.snoozed_until, r.due_at), r.id
            """
        )
        rows = cur.fetchall()
        reminders = [row for row in rows if row["status"] != "done"]
        # recent ones can be reopened
        done = sorted((row for row in rows if row["status"] == "done"), key=lambda row: row["completed_at"], reverse=True)[:20]
        cur.execute(_INTERVIEWS.format(where="i.deleted_at is null"))
        interviews = cur.fetchall()
        cur.execute(
            """
            select h.id, h.dedupe_key, h.from_status, h.to_status, h.changed_at
              from application_status_history h
             where h.dismissed_at is null and not exists (
                 select 1 from postings p
                  where p.dedupe_key = h.dedupe_key and p.hidden_by is not null
             )
             order by h.changed_at, h.id
            """
        )
        history = cur.fetchall()
    return {"reminders": reminders, "done_reminders": done, "interviews": interviews, "history": history}


REMINDER_ACTIONS = frozenset({"done", "reopen", "snooze", "unsnooze"})
_REMINDER_COLUMNS = "id, dedupe_key, kind, due_at, status, snoozed_until, completed_at, note, revision"


def reminder_key(conn: psycopg.Connection, reminder_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("select dedupe_key from reminders where id = %s", (reminder_id,))
        row = cur.fetchone()
        return row["dedupe_key"] if row else None


def history_key(conn: psycopg.Connection, entry_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("select dedupe_key from application_status_history where id = %s", (entry_id,))
        row = cur.fetchone()
        return row["dedupe_key"] if row else None


def dismiss_history(conn: psycopg.Connection, entry_id: int, dismissed: bool) -> dict | None:
    """Only hides it; applied dates still count it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            update application_status_history set dismissed_at = case when %s then now() end
             where id = %s and (dismissed_at is null) = %s
            returning id, dedupe_key, from_status, to_status, changed_at, dismissed_at
            """,
            (dismissed, entry_id, dismissed),
        )
        entry = cur.fetchone()
    conn.commit()
    return entry


def save_reminder(
    conn: psycopg.Connection,
    reminder_id: int,
    action: str,
    *,
    until: dt.datetime | None = None,
    expected: int | None = None,
) -> dict | None:
    """Returns the reminder before and after."""
    if action not in REMINDER_ACTIONS:
        raise ValueError("reminder action must be done, reopen, snooze or unsnooze")
    with conn.cursor() as cur:
        # hidden jobs stay as they were
        cur.execute(
            f"""
            select {_REMINDER_COLUMNS} from reminders r
             where id = %s and not exists (
                 select 1 from postings p where p.dedupe_key = r.dedupe_key and p.hidden_by is not null
             )
               for update
            """,
            (reminder_id,),
        )
        previous = cur.fetchone()
        if previous is None:
            return None
        if expected is not None and previous["revision"] != expected:
            conn.rollback()
            raise RecordConflict(previous)
        if action != "done" and action != "reopen" and previous["status"] == "done":
            raise ValueError("Reopen this follow-up before snoozing it")
        changes = {
            # keeps the snooze for reopen
            "done": "status = 'done', completed_at = now()",
            "reopen": "status = case when snoozed_until > now() then 'snoozed' else 'pending' end, completed_at = null",
            "snooze": "status = 'snoozed', snoozed_until = coalesce(%(until)s, now() + interval '3 days'), completed_at = null",
            "unsnooze": "status = 'pending', snoozed_until = null, completed_at = null",
        }[action]
        cur.execute(
            f"update reminders set {changes}, revision = revision + 1 where id = %(id)s returning {_REMINDER_COLUMNS}",
            {"id": reminder_id, "until": until},
        )
        reminder = cur.fetchone()
    conn.commit()
    return {"reminder": reminder, "previous": previous}


def add_reminder(conn: psycopg.Connection, dedupe_key: str, due_at: dt.datetime, note: str = "") -> dict | None:
    note = note.strip()
    if len(note) > 500:
        raise ValueError("A follow-up note must be under 500 characters")
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from postings where dedupe_key = %s group by dedupe_key having bool_and(hidden_by is null)",
            (dedupe_key,),
        )
        if cur.fetchone() is None:
            return None
        cur.execute(
            f"""
            insert into reminders (dedupe_key, kind, due_at, note)
            values (%s, 'manual', %s, %s) returning {_REMINDER_COLUMNS}
            """,
            (dedupe_key, due_at, note),
        )
        reminder = cur.fetchone()
    conn.commit()
    return reminder


def delete_reminder(conn: psycopg.Connection, reminder_id: int) -> bool:
    # only hand-made ones go away
    with conn.cursor() as cur:
        cur.execute(
            """
            delete from reminders r where id = %s and kind = 'manual' and not exists (
                select 1 from postings p where p.dedupe_key = r.dedupe_key and p.hidden_by is not null
            ) returning id
            """,
            (reminder_id,),
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


def add_interview(
    conn: psycopg.Connection,
    *,
    dedupe_key: str,
    starts_at: dt.datetime,
    ends_at: dt.datetime | None,
    location: str,
    notes: str,
) -> dict:
    if ends_at is not None and ends_at <= starts_at:
        raise ValueError("interview end must be after its start")
    with conn.cursor() as cur:
        cur.execute("select 1 from postings where dedupe_key = %s limit 1", (dedupe_key,))
        if cur.fetchone() is None:
            raise ValueError("job not found")
        cur.execute(
            "select status from application_states where dedupe_key = %s for update",
            (dedupe_key,),
        )
        application = cur.fetchone()
        previous = application["status"] if application else "new"
        cur.execute(
            """
            insert into interviews (dedupe_key, starts_at, ends_at, location, notes)
            values (%s, %s, %s, %s, %s) returning *
            """,
            (dedupe_key, starts_at, ends_at, location[:500], notes[:20_000]),
        )
        interview = cur.fetchone()
        if application is None:
            cur.execute(
                """
                insert into application_states (dedupe_key, status, next_step)
                values (%s, 'interviewing', %s)
                """,
                (dedupe_key, f"Interview {starts_at.isoformat()}"),
            )
            next_status = "interviewing"
        else:
            next_status = previous if previous in {"offer", "rejected"} else "interviewing"
            cur.execute(
                """
                update application_states
                   set status = %s, next_step = %s, updated_at = now()
                 where dedupe_key = %s
                """,
                (next_status, f"Interview {starts_at.isoformat()}", dedupe_key),
            )
        if previous != next_status:
            cur.execute(
                """
                insert into application_status_history (dedupe_key, from_status, to_status)
                values (%s, %s, %s)
                """,
                (dedupe_key, previous, next_status),
            )
        cur.execute(
            """
            update reminders set status = 'done', completed_at = now()
             where dedupe_key = %s and kind = 'follow_up' and status <> 'done'
            """,
            (dedupe_key,),
        )
    conn.commit()
    return interview


def interview_key(conn: psycopg.Connection, interview_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("select dedupe_key from interviews where id = %s", (interview_id,))
        row = cur.fetchone()
    return row["dedupe_key"] if row else None


def _interview(cur, interview_id: int) -> dict | None:
    cur.execute(_INTERVIEWS.format(where="i.id = %s and i.deleted_at is null"), (interview_id,))
    return cur.fetchone()


class RecordConflict(Exception):
    """The record changed since the client read it."""

    def __init__(self, record: dict):
        super().__init__("record changed")
        self.record = record


def update_interview(conn: psycopg.Connection, interview_id: int, changes: dict, expected: int | None = None) -> dict | None:
    """Apply validated changes; returns the row before and after."""
    with conn.cursor() as cur:
        cur.execute("select id from interviews where id = %s and deleted_at is null for update", (interview_id,))
        if cur.fetchone() is None:
            return None
        previous = _interview(cur, interview_id)
        if previous is None:
            return None
        # undo must not undo another tab
        if expected is not None and previous["revision"] != expected:
            conn.rollback()
            raise RecordConflict(previous)
        merged = {**previous, **changes}
        if merged["ends_at"] is not None and merged["ends_at"] <= merged["starts_at"]:
            raise ValueError("interview end must be after its start")
        cancelled = changes.get("cancelled")
        cur.execute(
            """
            update interviews
               set starts_at = %s, ends_at = %s, location = %s, notes = %s, revision = revision + 1,
                   cancelled_at = case
                       when %s::boolean is null then cancelled_at
                       when %s::boolean then coalesce(cancelled_at, now())
                       else null
                   end
             where id = %s
            """,
            (merged["starts_at"], merged["ends_at"], merged["location"][:500], merged["notes"][:20_000],
             cancelled, cancelled, interview_id),
        )
        interview = _interview(cur, interview_id)
    conn.commit()
    return {"interview": interview, "previous": previous}


def delete_interview(conn: psycopg.Connection, interview_id: int) -> bool:
    with conn.cursor() as cur:
        # undo only lasts a moment
        cur.execute("delete from interviews where deleted_at < now() - interval '1 day'")
        cur.execute(
            "update interviews set deleted_at = now() where id = %s and deleted_at is null returning id",
            (interview_id,),
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


def restore_interview(conn: psycopg.Connection, interview_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "update interviews set deleted_at = null where id = %s and deleted_at is not null returning id",
            (interview_id,),
        )
        interview = _interview(cur, interview_id) if cur.fetchone() else None
    conn.commit()
    return interview


def save_next_step(
    conn: psycopg.Connection, dedupe_key: str, value: str, expected: str | None = None
) -> str | None:
    # none means no application
    if len(value) > 1_000:
        raise ValueError("next step is too long")
    with conn.cursor() as cur:
        if not _lock_job(cur, dedupe_key):
            conn.rollback()
            return None
        cur.execute(
            "select next_step from application_states where dedupe_key = %s for update",
            (dedupe_key,),
        )
        existing = cur.fetchone()
        if existing is None:
            conn.rollback()
            return None
        # undo must not clobber newer steps
        if expected is not None and (existing["next_step"] or "") != expected:
            conn.rollback()
            raise ValueConflict(existing["next_step"] or "")
        cur.execute(
            """
            update application_states set next_step = %s, updated_at = now()
             where dedupe_key = %s returning next_step
            """,
            (value.strip(), dedupe_key),
        )
        saved = cur.fetchone()["next_step"]
        cur.execute(
            """
            update reminders set status = 'done', completed_at = now()
             where dedupe_key = %s and kind = 'follow_up' and status <> 'done'
            """,
            (dedupe_key,),
        )
    conn.commit()
    return saved


_CONTACT_COLUMNS = "id, company_name, name, title, email, linkedin_url, source, created_at, updated_at, revision"


def company_records(conn: psycopg.Connection, name: str) -> dict:
    with conn.cursor() as cur:
        key = resolve_company(company_rows(cur), name) or company_key(name)
        cur.execute(
            f"""
            select {_CONTACT_COLUMNS}
              from contacts where company_key = %s and deleted_at is null
             order by source desc, lower(name), id
            """,
            (key,),
        )
        contacts = cur.fetchall()
        cur.execute(
            "select body, updated_at from company_notes where company_key = %s",
            (key,),
        )
        note = cur.fetchone()
        cur.execute(
            f"select {_LINK_COLUMNS} from company_links where company_key = %s and deleted_at is null order by id",
            (key,),
        )
        links = cur.fetchall()
    return {"contacts": contacts, "note": note or {"body": "", "updated_at": None}, "saved_links": links}


def company_counts(conn: psycopg.Connection) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select company_key, count(*)::integer as contacts
              from contacts where deleted_at is null group by company_key
            """
        )
        counts: dict[str, dict] = {}
        for row in cur.fetchall():
            counts.setdefault(row["company_key"], {})["contacts"] = row["contacts"]
        cur.execute("select company_key from company_notes where btrim(body) <> ''")
        for row in cur.fetchall():
            counts.setdefault(row["company_key"], {})["note"] = True
        cur.execute(
            "select company_key, count(*)::integer as links from company_links where deleted_at is null group by company_key"
        )
        for row in cur.fetchall():
            counts.setdefault(row["company_key"], {})["links"] = row["links"]
    return counts


def company_account(conn: psycopg.Connection, name: str) -> dict | None:
    with conn.cursor() as cur:
        key = resolve_company(company_rows(cur), name) or company_key(name)
        cur.execute(
            """
            select account_exists, sign_in_email, password_manager_url, updated_at
              from company_accounts
             where company_key = any(%s)
             order by (company_key = %s) desc
             limit 1
            """,
            ([key, name.casefold().strip()], key),
        )
        return cur.fetchone()


def save_company_note(conn: psycopg.Connection, name: str, body: str, expected: str | None = None) -> dict:
    if len(body) > 20_000:
        raise ValueError("company note is too long")
    if not company_key(name):
        raise ValueError("company name is required")
    with conn.cursor() as cur:
        key = resolve_company(_read_companies(cur), name)
        # one note writer per company
        cur.execute("select name, display_name from companies where key = %s for update", (key,))
        company = cur.fetchone()
        if company is None:
            conn.rollback()
            raise ValueError("company not found")
        if expected is not None:
            cur.execute("select body from company_notes where company_key = %s", (key,))
            current = (cur.fetchone() or {"body": ""})["body"]
            if current != expected:
                conn.rollback()
                raise ValueConflict(current)
        cur.execute(
            """
            insert into company_notes (company_key, company_name, body)
            values (%s, %s, %s)
            on conflict (company_key) do update set
                company_name = excluded.company_name, body = excluded.body, updated_at = now()
            returning body, updated_at
            """,
            (key, company_name(company), body),
        )
        note = cur.fetchone()
    conn.commit()
    return note


def _public_company(cur, key: str) -> dict | None:
    cur.execute(f"select {COMPANY_COLUMNS} from companies where key = %s", (key,))
    row = cur.fetchone()
    return row and {**row, "name": company_name(row), "hidden": row["hidden_at"] is not None}


def _taken_name(rows: dict[str, dict], name: str, key: str | None = None) -> None:
    other = resolve_company(rows, name)
    if other is not None and other != key:
        raise ValueError(f"{company_name(rows[other])} is already a company. Merge them instead.")


def create_company(conn: psycopg.Connection, name: str) -> dict:
    """Add a company before the scout finds any of its jobs."""
    name = name.strip()
    key = company_key(name)
    if not key or len(name) > 500:
        raise ValueError("A company needs a name under 500 characters.")
    with conn.cursor() as cur:
        lock_companies(cur)
        rows = company_rows(cur)
        existing = resolve_company(rows, name)
        if existing is not None:
            row = rows[existing]
            if row["hidden_at"] is not None:
                conn.rollback()
                raise ValueError(f"{company_name(row)} is hidden. Show hidden companies to restore it.")
            # keep it listed without jobs
            cur.execute("update companies set manual = true where key = %s", (existing,))
            company = _public_company(cur, existing)
            conn.commit()
            return {"company": company, "created": False}
        cur.execute(
            "insert into companies (key, name, display_name, manual) values (%s, %s, %s, true)",
            (key, name, name),
        )
        company = _public_company(cur, key)
    conn.commit()
    return {"company": company, "created": True}


def _canonical_for_update(cur, key: str) -> dict | None:
    cur.execute(
        f"select {COMPANY_COLUMNS} from companies where key = %s and merged_into is null for update",
        (key,),
    )
    return cur.fetchone()


def rename_company(conn: psycopg.Connection, key: str, name: str, expected: int | None = None) -> dict | None:
    """Store a display name, so the scout keeps matching on the key."""
    name = name.strip()
    if not company_key(name) or len(name) > 500:
        raise ValueError("A company needs a name under 500 characters.")
    with conn.cursor() as cur:
        lock_companies(cur)
        row = _canonical_for_update(cur, key)
        if row is None:
            conn.rollback()
            return None
        previous = {**row, "name": company_name(row), "hidden": row["hidden_at"] is not None}
        # undo must not undo another tab
        if expected is not None and row["revision"] != expected:
            conn.rollback()
            raise RecordConflict(previous)
        try:
            _taken_name(company_rows(cur), name, key)
        except ValueError:
            conn.rollback()
            raise
        cur.execute(
            "update companies set display_name = %s, revision = revision + 1, updated_at = now() where key = %s",
            (None if name == row["name"] and not row["manual"] else name, key),
        )
        company = _public_company(cur, key)
    conn.commit()
    return {"company": company, "previous": previous}


def hide_company(conn: psycopg.Connection, key: str, hidden: bool) -> dict | None:
    with conn.cursor() as cur:
        lock_companies(cur)
        cur.execute(
            f"""
            update companies set hidden_at = {'now()' if hidden else 'null'}
             where key = %s and merged_into is null and (hidden_at is null) = %s
            returning key
            """,
            (key, hidden),
        )
        found = cur.fetchone()
        company = found and _public_company(cur, key)
    conn.commit()
    return company


def merge_companies(conn: psycopg.Connection, source: str, target: str) -> dict | None:
    """Fold one company into another; the target keeps its own fields."""
    if source == target:
        raise ValueError("Pick a different company to merge into.")
    with conn.cursor() as cur:
        lock_companies(cur)
        # both rows, in a stable order
        cur.execute(
            "select key from companies where key = any(%s) and merged_into is null order by key for update",
            ([source, target],),
        )
        if len(cur.fetchall()) != 2:
            conn.rollback()
            return None
        target_row = _public_company(cur, target)
        source_row = _public_company(cur, source)
        hidden = next((row for row in (source_row, target_row) if row["hidden"]), None)
        if hidden:
            conn.rollback()
            raise ValueError(f"{hidden['name']} is hidden. Show it in Companies before merging.")
        cur.execute("select company_key, company_name, body, updated_at from company_notes where company_key = any(%s)", ([source, target],))
        notes = {row["company_key"]: row for row in cur.fetchall()}
        source_note, target_note = notes.get(source), notes.get(target)
        if source_note and source_note["body"].strip():
            kept = (target_note or {}).get("body", "").rstrip()
            appended = f"From {source_row['name']}:\n{source_note['body'].strip()}"
            body = f"{kept}\n\n{appended}" if kept else appended
            cur.execute(
                """
                insert into company_notes (company_key, company_name, body) values (%s, %s, %s)
                on conflict (company_key) do update set body = excluded.body, updated_at = clock_timestamp()
                """,
                (target, target_row["name"], body),
            )
        if source_note:
            cur.execute("delete from company_notes where company_key = %s", (source,))
        cur.execute(
            "update contacts set company_key = %s, company_name = %s where company_key = %s returning id",
            (target, target_row["name"], source),
        )
        contacts = [row["id"] for row in cur.fetchall()]
        links = _move_links(cur, source, target)
        cur.execute(
            "update companies set merged_into = %s, revision = revision + 1, updated_at = now() where key = %s",
            (target, source),
        )
        cur.execute(
            "update companies set revision = revision + 1, updated_at = now() where key = %s returning revision",
            (target,),
        )
        revision = cur.fetchone()["revision"]
        cur.execute("select updated_at from company_notes where company_key = %s", (target,))
        note_at = (cur.fetchone() or {}).get("updated_at")
        before = {
            "target_note": target_note and {"body": target_note["body"], "updated_at": target_note["updated_at"].isoformat()},
            "source_note": source_note and {
                "body": source_note["body"], "company_name": source_note["company_name"],
                "updated_at": source_note["updated_at"].isoformat(),
            },
            "contacts": contacts, "links": links,
        }
        cur.execute(
            """
            insert into company_merges (source_key, target_key, before, revision, note_at)
            values (%s, %s, %s, %s, %s) returning id
            """,
            (source, target, Json(before), revision, note_at),
        )
        merge_id = cur.fetchone()["id"]
        company = _public_company(cur, target)
    conn.commit()
    return {"merge_id": merge_id, "company": company, "source": source_row, "moved": {"contacts": len(contacts), "links": len(links)}}


def _move_links(cur, source: str, target: str, ids: list[int] | None = None) -> list[int]:
    # deleted ones too, so undo restores there
    cur.execute(
        f"update company_links set company_key = %s where company_key = %s {'and id = any(%s)' if ids is not None else ''} returning id",
        (target, source, *([ids] if ids is not None else [])),
    )
    return [row["id"] for row in cur.fetchall()]


_LINK_COLUMNS = "id, label, url, revision, created_at, updated_at"


def add_company_link(conn: psycopg.Connection, key: str, label: str, url: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("select key from companies where key = %s and merged_into is null for share", (key,))
        if cur.fetchone() is None:
            conn.rollback()
            return None
        cur.execute(
            f"insert into company_links (company_key, label, url) values (%s, %s, %s) returning {_LINK_COLUMNS}",
            (key, label, url),
        )
        link = cur.fetchone()
    conn.commit()
    return link


def update_company_link(conn: psycopg.Connection, link_id: int, changes: dict, expected: int | None = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(f"select {_LINK_COLUMNS} from company_links where id = %s and deleted_at is null for update", (link_id,))
        previous = cur.fetchone()
        if previous is None:
            return None
        if expected is not None and previous["revision"] != expected:
            conn.rollback()
            raise RecordConflict(previous)
        merged = {**previous, **changes}
        cur.execute(
            f"""
            update company_links set label = %s, url = %s, revision = revision + 1, updated_at = now()
             where id = %s returning {_LINK_COLUMNS}
            """,
            (merged["label"], merged["url"], link_id),
        )
        link = cur.fetchone()
    conn.commit()
    return {"link": link, "previous": previous}


def delete_company_link(conn: psycopg.Connection, link_id: int) -> bool:
    with conn.cursor() as cur:
        # undo only lasts a moment
        cur.execute("delete from company_links where deleted_at < now() - interval '1 day'")
        cur.execute(
            "update company_links set deleted_at = now() where id = %s and deleted_at is null returning id",
            (link_id,),
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


def restore_company_link(conn: psycopg.Connection, link_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            f"update company_links set deleted_at = null where id = %s and deleted_at is not null returning {_LINK_COLUMNS}",
            (link_id,),
        )
        link = cur.fetchone()
    conn.commit()
    return link


def merge_target(conn: psycopg.Connection, merge_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("select target_key from company_merges where id = %s", (merge_id,))
        row = cur.fetchone()
    return row and row["target_key"]


def undo_company_merge(conn: psycopg.Connection, merge_id: int, expected: int | None = None) -> dict | None:
    with conn.cursor() as cur:
        lock_companies(cur)
        cur.execute("select source_key, target_key from company_merges where id = %s", (merge_id,))
        found = cur.fetchone()
        if found is None:
            conn.rollback()
            return None
        cur.execute(
            "select key, merged_into, revision from companies where key = any(%s) order by key for update",
            ([found["source_key"], found["target_key"]],),
        )
        rows = {row["key"]: row for row in cur.fetchall()}
        cur.execute("select * from company_merges where id = %s", (merge_id,))
        merge = cur.fetchone()
        source, target = rows.get(merge["source_key"]), rows.get(merge["target_key"])
        cur.execute("select updated_at from company_notes where company_key = %s", (merge["target_key"],))
        note_at = (cur.fetchone() or {}).get("updated_at")
        current = merge["revision"] if expected is None else expected
        # later changes would be lost
        if (
            merge["undone_at"] is not None or source is None or target is None
            or source["merged_into"] != merge["target_key"] or target["revision"] != current
        ):
            conn.rollback()
            raise EditConflict("This company changed after the merge, so it can't be undone.")
        if note_at != merge["note_at"]:
            conn.rollback()
            raise EditConflict("Its notes changed after the merge, so it can't be undone.")
        before = merge["before"]
        source_key, target_key = merge["source_key"], merge["target_key"]
        source_name = _public_company(cur, source_key)["name"]
        if before["target_note"] is None:
            cur.execute("delete from company_notes where company_key = %s", (target_key,))
        else:
            cur.execute(
                "update company_notes set body = %s, updated_at = %s where company_key = %s",
                (before["target_note"]["body"], before["target_note"]["updated_at"], target_key),
            )
        if before["source_note"] is not None:
            cur.execute(
                """
                insert into company_notes (company_key, company_name, body, updated_at) values (%s, %s, %s, %s)
                on conflict (company_key) do update set
                    company_name = excluded.company_name, body = excluded.body, updated_at = excluded.updated_at
                """,
                (source_key, *(before["source_note"][name] for name in ("company_name", "body", "updated_at"))),
            )
        cur.execute(
            "update contacts set company_key = %s, company_name = %s where id = any(%s) and company_key = %s",
            (source_key, source_name, before["contacts"], target_key),
        )
        _move_links(cur, target_key, source_key, before["links"])
        cur.execute(
            "update companies set merged_into = null, revision = revision + 1, updated_at = now() where key = %s",
            (source_key,),
        )
        cur.execute("update companies set revision = revision + 1, updated_at = now() where key = %s", (target_key,))
        cur.execute("update company_merges set undone_at = now() where id = %s", (merge_id,))
        result = {"company": _public_company(cur, source_key), "target": _public_company(cur, target_key)}
    conn.commit()
    return result


def save_contact(conn: psycopg.Connection, payload: dict, source: str = "manual") -> dict:
    company = str(payload.get("company") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not company or not name or source not in {"manual", "linkedin_csv"}:
        raise ValueError("company and contact name are required")
    title = str(payload.get("title") or "").strip()[:500]
    email = str(payload.get("email") or "").strip()[:500]
    linkedin_url = str(payload.get("linkedin_url") or "").strip()[:2_000]
    if linkedin_url and not linkedin_url.startswith("https://"):
        raise ValueError("LinkedIn URL must use HTTPS")
    with conn.cursor() as cur:
        key = resolve_company(_read_companies(cur), company)
        cur.execute("select name, display_name from companies where key = %s", (key,))
        known_company = cur.fetchone()
        if known_company is None:
            conn.rollback()
            raise ValueError("company not found")
        import_key = None
        if source == "linkedin_csv":
            fingerprint = "\0".join((key, normalise(name), linkedin_url.casefold()))
            import_key = hashlib.sha256(fingerprint.encode()).hexdigest()
        cur.execute(
            """
            insert into contacts
                (company_key, company_name, name, title, email, linkedin_url, source, import_key)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (import_key) do update set
                company_name = excluded.company_name, name = excluded.name,
                title = excluded.title, email = excluded.email,
                linkedin_url = excluded.linkedin_url, deleted_at = null,
                revision = contacts.revision + 1, updated_at = now()
            returning *
            """,
            (key, company_name(known_company), name, title, email, linkedin_url, source, import_key),
        )
        contact = cur.fetchone()
    conn.commit()
    return contact


def update_contact(conn: psycopg.Connection, contact_id: int, changes: dict, expected: int | None = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            f"select {_CONTACT_COLUMNS} from contacts where id = %s and deleted_at is null for update",
            (contact_id,),
        )
        previous = cur.fetchone()
        if previous is None:
            return None
        # undo must not undo another tab
        if expected is not None and previous["revision"] != expected:
            conn.rollback()
            raise RecordConflict(previous)
        merged = {**previous, **changes}
        cur.execute(
            f"""
            update contacts set name = %s, title = %s, email = %s, linkedin_url = %s,
                   revision = revision + 1, updated_at = now()
             where id = %s returning {_CONTACT_COLUMNS}
            """,
            (merged["name"], merged["title"], merged["email"], merged["linkedin_url"], contact_id),
        )
        contact = cur.fetchone()
    conn.commit()
    return {"contact": contact, "previous": previous}


def delete_contact(conn: psycopg.Connection, contact_id: int) -> bool:
    with conn.cursor() as cur:
        # undo only lasts a moment
        cur.execute("delete from contacts where deleted_at < now() - interval '1 day'")
        cur.execute(
            "update contacts set deleted_at = now() where id = %s and deleted_at is null returning id",
            (contact_id,),
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


def restore_contact(conn: psycopg.Connection, contact_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            f"update contacts set deleted_at = null where id = %s and deleted_at is not null returning {_CONTACT_COLUMNS}",
            (contact_id,),
        )
        contact = cur.fetchone()
    conn.commit()
    return contact


def eligibility_overrides(conn: psycopg.Connection, dedupe_key: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, blocker_key, evidence, comparison, note, created_at
              from eligibility_overrides
             where dedupe_key = %s and revoked_at is null
             order by created_at, id
            """,
            (dedupe_key,),
        )
        return cur.fetchall()


def record_eligibility_overrides(
    conn: psycopg.Connection,
    dedupe_key: str,
    blockers: list[dict],
    note: str = "",
) -> list[int]:
    if len(note) > 1_000:
        raise ValueError("override note is too long")
    if not blockers or len(blockers) > 4:
        raise ValueError("there are no active blockers to override")
    allowed = {"sponsorship", "clearance", "degree", "graduation"}
    rows = []
    for blocker in blockers:
        key = str(blocker.get("key") or "")
        evidence = str(blocker.get("evidence") or "")
        comparison = str(blocker.get("comparison") or "")
        if key not in allowed or not evidence or len(evidence) > 5_000 or len(comparison) > 2_000:
            raise ValueError("invalid eligibility blocker")
        rows.append((dedupe_key, key, evidence, comparison, note.strip()))
    with conn.cursor() as cur:
        cur.execute("select 1 from postings where dedupe_key = %s limit 1", (dedupe_key,))
        if cur.fetchone() is None:
            raise ValueError("job not found")
        ids = []
        for row in rows:
            cur.execute(
                """
                insert into eligibility_overrides
                    (dedupe_key, blocker_key, evidence, comparison, note)
                values (%s, %s, %s, %s, %s) returning id
                """,
                row,
            )
            ids.append(cur.fetchone()["id"])
    conn.commit()
    return ids


_VISIBLE_OVERRIDE_JOB = """
    not exists (select 1 from postings p where p.dedupe_key = eligibility_overrides.dedupe_key and p.hidden_by is not null)
"""


def revoke_eligibility_overrides(conn: psycopg.Connection, dedupe_key: str, ids: list[int] | None = None) -> list[int]:
    """Revoked rows stay as evidence."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            update eligibility_overrides set revoked_at = now()
             where dedupe_key = %s and revoked_at is null and (%s::bigint[] is null or id = any(%s::bigint[]))
               and {_VISIBLE_OVERRIDE_JOB}
            returning id
            """,
            (dedupe_key, ids, ids),
        )
        revoked = sorted(row["id"] for row in cur.fetchall())
    conn.commit()
    return revoked


def restore_eligibility_overrides(conn: psycopg.Connection, dedupe_key: str, ids: list[int]) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            update eligibility_overrides set revoked_at = null
             where dedupe_key = %s and revoked_at is not null and id = any(%s::bigint[])
               and {_VISIBLE_OVERRIDE_JOB}
            returning id
            """,
            (dedupe_key, ids),
        )
        restored = sorted(row["id"] for row in cur.fetchall())
    conn.commit()
    return restored


def clean_tag(value) -> str:
    tag = " ".join(str(value or "").split())
    if not tag or len(tag) > 40:
        raise ValueError("A tag needs 1 to 40 characters")
    return tag


def manual_tags(conn: psycopg.Connection) -> dict[str, list[str]]:
    with conn.cursor() as cur:
        cur.execute("select dedupe_key, tag from job_tags where deleted_at is null order by created_at, id")
        tags: dict[str, list[str]] = {}
        for row in cur.fetchall():
            tags.setdefault(row["dedupe_key"], []).append(row["tag"])
        return tags


_VISIBLE_TAG_JOB = """
    not exists (select 1 from postings p where p.dedupe_key = job_tags.dedupe_key and p.hidden_by is not null)
"""


def tag_jobs(conn: psycopg.Connection, dedupe_keys: list[str], tag: str, *, remove: bool = False) -> dict | None:
    """Returns the rows it changed, so undo touches only those."""
    tag = clean_tag(tag)
    with conn.cursor() as cur:
        cur.execute(
            """
            select dedupe_key from postings where dedupe_key = any(%s)
             group by dedupe_key having bool_and(hidden_by is null)
            """,
            (dedupe_keys,),
        )
        if len(cur.fetchall()) != len(set(dedupe_keys)):
            return None
        if remove:
            # undo only lasts a moment
            cur.execute("delete from job_tags where deleted_at < now() - interval '1 day'")
            cur.execute(
                """
                update job_tags set deleted_at = now()
                 where dedupe_key = any(%s) and lower(tag) = lower(%s) and deleted_at is null
                returning id, dedupe_key
                """,
                (dedupe_keys, tag),
            )
        else:
            cur.execute(
                """
                insert into job_tags (dedupe_key, tag)
                select key, %s from unnest(%s::text[]) as key
                on conflict (dedupe_key, lower(tag)) where deleted_at is null do nothing
                returning id, dedupe_key
                """,
                (tag, dedupe_keys),
            )
        rows = cur.fetchall()
    conn.commit()
    changed = {row["dedupe_key"] for row in rows}
    return {"changed": [key for key in dict.fromkeys(dedupe_keys) if key in changed], "ids": sorted(row["id"] for row in rows)}


def tag_row_keys(conn: psycopg.Connection, ids: list[int]) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("select distinct dedupe_key from job_tags where id = any(%s)", (ids,))
        return [row["dedupe_key"] for row in cur.fetchall()]


def undo_tag_rows(conn: psycopg.Connection, ids: list[int], *, remove: bool) -> dict:
    """Undo by row, never by name."""
    with conn.cursor() as cur:
        if remove:
            cur.execute(
                f"""
                update job_tags set deleted_at = now()
                 where id = any(%s) and deleted_at is null and {_VISIBLE_TAG_JOB}
                returning id, dedupe_key
                """,
                (ids,),
            )
        else:
            # a tag added since wins
            cur.execute(
                f"""
                update job_tags set deleted_at = null
                 where id = any(%s) and deleted_at is not null and {_VISIBLE_TAG_JOB}
                   and not exists (
                       select 1 from job_tags live
                        where live.dedupe_key = job_tags.dedupe_key and lower(live.tag) = lower(job_tags.tag)
                          and live.deleted_at is null
                   )
                returning id, dedupe_key
                """,
                (ids,),
            )
        rows = cur.fetchall()
    conn.commit()
    return {"changed": sorted({row["dedupe_key"] for row in rows}), "ids": sorted(row["id"] for row in rows)}


RULE_KINDS = frozenset({"archive_title", "tag_title", "boost_company"})


def normalize_rule(payload: dict) -> dict:
    kind = str(payload.get("kind") or "")
    name = str(payload.get("name") or "").strip()
    pattern = str(payload.get("pattern") or "").strip()
    value = str(payload.get("value") or "").strip()
    if kind not in RULE_KINDS:
        raise ValueError("unknown rule kind")
    if not name or len(name) > 120 or not pattern or len(pattern) > 120:
        raise ValueError("rule name and pattern are required and must be at most 120 characters")
    if kind == "tag_title" and (not value or len(value) > 40):
        raise ValueError("tag rules need a tag of at most 40 characters")
    if kind == "boost_company":
        try:
            amount = int(value)
        except ValueError as error:
            raise ValueError("company boost must be a whole number from 1 to 100") from error
        if not 1 <= amount <= 100:
            raise ValueError("company boost must be a whole number from 1 to 100")
        value = str(amount)
    if kind == "archive_title":
        value = ""
    return {"name": name, "kind": kind, "pattern": pattern, "value": value}


_RULE_COLUMNS = "id, name, kind, pattern, value, enabled, position, revision, created_at, updated_at"


def rules(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(f"select {_RULE_COLUMNS} from rules where deleted_at is null order by position, id")
        return cur.fetchall()


def _apply_rules_cursor(cur) -> None:
    # first match in order claims it
    cur.execute(
        "select id, name, kind, pattern, value from rules where enabled and deleted_at is null order by position, id"
    )
    active = cur.fetchall()
    archive_rules = [rule for rule in active if rule["kind"] == "archive_title"]
    if not archive_rules:
        return
    # one hidden row hides the job
    cur.execute(
        "select dedupe_key, min(title) as title from postings group by dedupe_key having bool_and(hidden_by is null)"
    )
    for job in cur.fetchall():
        title = normalise(job["title"])
        for rule in archive_rules:
            if normalise(rule["pattern"]) not in title:
                continue
            cur.execute(
                "select status from application_states where dedupe_key = %s for update",
                (job["dedupe_key"],),
            )
            state = cur.fetchone()
            previous = state["status"] if state else "new"
            if previous != "new":
                continue
            cur.execute(
                """
                select 1 from rule_actions
                 where rule_id = %s and dedupe_key = %s and action = 'archive'
                """,
                (rule["id"], job["dedupe_key"]),
            )
            if cur.fetchone() is not None:
                continue
            if state is None:
                cur.execute(
                    "insert into application_states (dedupe_key, status) values (%s, 'archived')",
                    (job["dedupe_key"],),
                )
            else:
                cur.execute(
                    "update application_states set status = 'archived', updated_at = now() where dedupe_key = %s",
                    (job["dedupe_key"],),
                )
            cur.execute(
                """
                insert into application_status_history (dedupe_key, from_status, to_status)
                values (%s, %s, 'archived')
                """,
                (job["dedupe_key"], previous),
            )
            cur.execute(
                """
                insert into rule_actions (rule_id, dedupe_key, action, previous_status)
                values (%s, %s, 'archive', %s)
                """,
                (rule["id"], job["dedupe_key"], previous),
            )
            break


def save_rule(conn: psycopg.Connection, payload: dict) -> dict:
    clean = normalize_rule(payload)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            insert into rules (name, kind, pattern, value, position)
            values (%s, %s, %s, %s, (select coalesce(max(position) + 1, 0) from rules))
            returning {_RULE_COLUMNS}
            """,
            (clean["name"], clean["kind"], clean["pattern"], clean["value"]),
        )
        saved = cur.fetchone()
        _apply_rules_cursor(cur)
    conn.commit()
    return saved


def set_rule_enabled(conn: psycopg.Connection, rule_id: int, enabled: bool) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            update rules set enabled = %s, updated_at = now()
             where id = %s and deleted_at is null
             returning {_RULE_COLUMNS}
            """,
            (enabled, rule_id),
        )
        saved = cur.fetchone()
        if saved and enabled:
            _apply_rules_cursor(cur)
    conn.commit()
    return saved


def update_rule(conn: psycopg.Connection, rule_id: int, changes: dict, expected: int | None = None) -> dict | None:
    """Edit what a rule matches; past actions stay."""
    with conn.cursor() as cur:
        cur.execute(f"select {_RULE_COLUMNS} from rules where id = %s and deleted_at is null for update", (rule_id,))
        previous = cur.fetchone()
        if previous is None:
            return None
        if expected is not None and previous["revision"] != expected:
            conn.rollback()
            raise RecordConflict(previous)
        clean = normalize_rule({**previous, **changes})
        cur.execute(
            f"""
            update rules set name = %s, kind = %s, pattern = %s, value = %s,
                   revision = revision + 1, updated_at = now()
             where id = %s returning {_RULE_COLUMNS}
            """,
            (clean["name"], clean["kind"], clean["pattern"], clean["value"], rule_id),
        )
        rule = cur.fetchone()
        if rule["enabled"]:
            _apply_rules_cursor(cur)
    conn.commit()
    return {"rule": rule, "previous": previous}


def delete_rule(conn: psycopg.Connection, rule_id: int) -> bool:
    # rule actions still name it
    with conn.cursor() as cur:
        cur.execute(
            "update rules set deleted_at = now() where id = %s and deleted_at is null returning id",
            (rule_id,),
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


def restore_rule(conn: psycopg.Connection, rule_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            f"update rules set deleted_at = null where id = %s and deleted_at is not null returning {_RULE_COLUMNS}",
            (rule_id,),
        )
        rule = cur.fetchone()
        if rule and rule["enabled"]:
            _apply_rules_cursor(cur)
    conn.commit()
    return rule


def rules_with_order(conn: psycopg.Connection) -> dict:
    """The order and its revision from one moment."""
    with conn.cursor() as cur:
        # waits out a reorder in flight
        cur.execute("select revision from rule_order for share")
        revision = cur.fetchone()["revision"]
        cur.execute(f"select {_RULE_COLUMNS} from rules where deleted_at is null order by position, id")
        listed = cur.fetchall()
    conn.commit()
    return {"rules": listed, "order_revision": revision}


def save_rule_order(conn: psycopg.Connection, rule_ids: list[int], expected: int) -> dict:
    if len(rule_ids) != len(set(rule_ids)) or len(rule_ids) > 500:
        raise ValueError("rule order contains duplicates or too many rules")
    with conn.cursor() as cur:
        cur.execute("select revision from rule_order for update")
        revision = cur.fetchone()["revision"]
        cur.execute("select id from rules where deleted_at is null for update")
        # a stale tab has another order
        if revision != expected or {row["id"] for row in cur.fetchall()} != set(rule_ids):
            conn.rollback()
            raise EditConflict("The rules changed in another tab. Reload and retry.")
        cur.executemany(
            "update rules set position = %s where id = %s",
            list(enumerate(rule_ids)),
        )
        cur.execute("update rule_order set revision = revision + 1 returning revision")
        revision = cur.fetchone()["revision"]
    conn.commit()
    return {"ids": rule_ids, "order_revision": revision}


def undo_rule_action(conn: psycopg.Connection, action_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, dedupe_key, previous_status from rule_actions
             where id = %s and undone_at is null for update
            """,
            (action_id,),
        )
        action = cur.fetchone()
        if action is None:
            return None
        cur.execute(
            "select status from application_states where dedupe_key = %s for update",
            (action["dedupe_key"],),
        )
        state = cur.fetchone()
        if state is None or state["status"] != "archived":
            raise ValueError("the job is no longer archived by this rule")
        cur.execute(
            "update rule_actions set undone_at = now() where id = %s returning *",
            (action_id,),
        )
        saved = cur.fetchone()
        cur.execute(
            "update application_states set status = %s, updated_at = now() where dedupe_key = %s",
            (action["previous_status"], action["dedupe_key"]),
        )
        cur.execute(
            """
            insert into application_status_history (dedupe_key, from_status, to_status)
            values (%s, 'archived', %s)
            """,
            (action["dedupe_key"], action["previous_status"]),
        )
    conn.commit()
    return saved
