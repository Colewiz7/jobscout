"""Postgres persistence and the dedupe/close rules.

Schema changes belong to the locked one-shot migration runner. Runtime scout
and dashboard processes only verify that the recorded version is current.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import urllib.parse

import psycopg
from psycopg.types.json import Json
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
                       (array_agg(p.score_detail order by p.score desc nulls last)
                           filter (where p.score_detail is not null))[1] as score_detail
                  from postings p
                 where (%s or not p.closed)
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
            select g.dedupe_key, g.company, g.title, g.terms, g.age_days,
                   g.first_seen, g.last_seen, coalesce(g.url, '') as url,
                   g.sources, g.closed, g.notified, g.score,
                   coalesce(g.score_detail, '{}'::jsonb) as score_detail,
                   coalesce(s.status, 'new') as status,
                   coalesce(s.notes, '') as notes,
                   s.updated_at as application_updated_at,
                   (
                       select max(e.changed_at)
                         from application_events e
                         join postings other on other.dedupe_key = e.dedupe_key
                        where lower(other.company) = lower(g.company)
                          and other.dedupe_key <> g.dedupe_key
                          and e.changed_at >= now() - interval '30 days'
                   ) as recent_company_application_at,
                   (select coalesce(
                       string_agg(distinct btrim(part), '; ' order by btrim(part)), '')
                      from unnest(string_to_array(g.locations_raw, ';')) as part
                     where btrim(part) <> '') as location
              from grouped g
              left join application_states s on s.dedupe_key = g.dedupe_key
             order by g.score desc nulls last, g.age_days asc nulls last,
                      lower(g.company), lower(g.title)
            """,
            (include_closed,),
        )
        return cur.fetchall()


def saved_views(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, name, filters, sort, pinned, created_at, updated_at
              from saved_views
             order by pinned desc, lower(name), id
            """
        )
        return cur.fetchall()


def save_view(
    conn: psycopg.Connection,
    *,
    name: str,
    filters: dict,
    sort: str,
    pinned: bool = True,
) -> dict:
    name = name.strip()
    if not name or len(name) > 40:
        raise ValueError("view name must be between 1 and 40 characters")
    if sort not in {"score", "newest", "company"}:
        raise ValueError("unknown saved-view sort")
    allowed = {"query", "status", "source", "remote"}
    clean = {key: filters[key] for key in allowed if key in filters}
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into saved_views (name, filters, sort, pinned)
            values (%s, %s, %s, %s)
            on conflict (name) do update set
                filters = excluded.filters,
                sort = excluded.sort,
                pinned = excluded.pinned,
                updated_at = now()
            returning id, name, filters, sort, pinned, created_at, updated_at
            """,
            (name, Json(clean), sort, pinned),
        )
        row = cur.fetchone()
    conn.commit()
    return row


def delete_saved_view(conn: psycopg.Connection, view_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("delete from saved_views where id = %s", (view_id,))
        changed = cur.rowcount == 1
    conn.commit()
    return changed


def profile_data(
    conn: psycopg.Connection,
    *,
    dedupe_key: str | None = None,
    company: str | None = None,
) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            select key, group_name as "group", label, value, pinned, sort_order
              from profile_fields
             order by pinned desc, sort_order, key
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
            select id, name, document_date as date, url
              from documents
             order by document_date desc nulls last, lower(name), id
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
            cur.execute(
                """
                select company_name, account_exists, sign_in_email, password_manager_url, updated_at
                  from company_accounts where company_key = %s
                """,
                (company.casefold().strip(),),
            )
            company_account = cur.fetchone()
    return {
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


def normalize_quick_fill_context(
    *,
    dedupe_key: str,
    company: str,
    answer_overrides: dict,
    company_account: dict,
) -> dict:
    """Validate job-specific answers and password-free ATS account metadata."""
    dedupe_key = dedupe_key.strip()
    company = company.strip()
    if not dedupe_key or len(dedupe_key) > 500 or not company or len(company) > 240:
        raise ValueError("invalid job or company")
    if not isinstance(answer_overrides, dict) or len(answer_overrides) > 50:
        raise ValueError("answer_overrides must be an object with at most 50 entries")
    overrides = {}
    for name, body in answer_overrides.items():
        clean_name = str(name).strip()
        clean_body = str(body)
        if not clean_name or len(clean_name) > 120 or len(clean_body) > 10_000:
            raise ValueError("answer override name or body is invalid")
        overrides[clean_name] = clean_body
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
    return {
        "dedupe_key": dedupe_key,
        "company": company,
        "answer_overrides": overrides,
        "company_account": {
            "company_name": company,
            "account_exists": exists,
            "sign_in_email": email,
            "password_manager_url": manager_url,
        },
    }


def save_quick_fill_context(
    conn: psycopg.Connection,
    *,
    dedupe_key: str,
    company: str,
    answer_overrides: dict,
    company_account: dict,
) -> dict:
    clean = normalize_quick_fill_context(
        dedupe_key=dedupe_key,
        company=company,
        answer_overrides=answer_overrides,
        company_account=company_account,
    )
    dedupe_key = clean["dedupe_key"]
    company = clean["company"]
    overrides = clean["answer_overrides"]
    account = clean["company_account"]

    with conn.cursor() as cur:
        cur.execute("delete from answer_template_overrides where dedupe_key = %s", (dedupe_key,))
        cur.executemany(
            "insert into answer_template_overrides (dedupe_key, template_name, body) values (%s, %s, %s)",
            [(dedupe_key, name, body) for name, body in overrides.items()],
        )
        cur.execute(
            """
            insert into company_accounts
                (company_key, company_name, account_exists, sign_in_email, password_manager_url)
            values (%s, %s, %s, %s, %s)
            on conflict (company_key) do update set
                company_name = excluded.company_name,
                account_exists = excluded.account_exists,
                sign_in_email = excluded.sign_in_email,
                password_manager_url = excluded.password_manager_url,
                updated_at = now()
            """,
            (
                company.casefold(), company, account["account_exists"],
                account["sign_in_email"], account["password_manager_url"],
            ),
        )
    conn.commit()
    return profile_data(conn, dedupe_key=dedupe_key, company=company)


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
            "id": row.get("id", f"template-{index + 1}"), "name": name, "body": body,
            "sort_order": int(row.get("sort_order", index * 10)),
        })

    documents = []
    for index, row in enumerate(objects("documents", 50)):
        name = str(row.get("name", "")).strip()
        url = str(row.get("url", ""))
        date = str(row.get("date", "")).strip() or None
        if not name or len(name) > 160 or len(url) > 2_000:
            raise ValueError(f"documents[{index}] has an invalid name or URL")
        if date:
            try:
                dt.date.fromisoformat(date)
            except ValueError as error:
                raise ValueError(f"documents[{index}].date must be YYYY-MM-DD") from error
        documents.append({
            "id": row.get("id", f"document-{index + 1}"),
            "name": name, "url": url, "date": date,
        })

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


def replace_profile_data(conn: psycopg.Connection, payload: dict) -> dict:
    """Atomically replace the feature-gated single-user Quick-fill profile."""
    clean = normalize_profile_data(payload)
    with conn.cursor() as cur:
        cur.execute("delete from profile_fields")
        cur.executemany(
            """
            insert into profile_fields (key, group_name, label, value, pinned, sort_order)
            values (%s, %s, %s, %s, %s, %s)
            """,
            [(row["key"], row["group"], row["label"], row["value"], row["pinned"], row["sort_order"]) for row in clean["fields"]],
        )
        cur.execute("delete from answer_templates")
        cur.executemany(
            "insert into answer_templates (name, body, sort_order) values (%s, %s, %s)",
            [(row["name"], row["body"], row["sort_order"]) for row in clean["answer_templates"]],
        )
        cur.execute("delete from documents")
        cur.executemany(
            "insert into documents (name, document_date, url) values (%s, %s, %s)",
            [(row["name"], row["date"], row["url"]) for row in clean["documents"]],
        )
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
        return cur.fetchone()


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
    conn.commit()


def save_application_state(
    conn: psycopg.Connection, dedupe_key: str, status: str, notes: str
) -> bool:
    """Persist a person's workflow state, if the posting actually exists."""
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"unknown application status: {status}")
    with conn.cursor() as cur:
        cur.execute("select 1 from postings where dedupe_key = %s limit 1", (dedupe_key,))
        if cur.fetchone() is None:
            return False
        cur.execute(
            "select status from application_states where dedupe_key = %s",
            (dedupe_key,),
        )
        existing = cur.fetchone()
        previous = existing["status"] if existing else "new"
        cur.execute(
            """
            insert into application_states (dedupe_key, status, notes)
            values (%s, %s, %s)
            on conflict (dedupe_key) do update set
                status = excluded.status,
                notes = excluded.notes,
                updated_at = now()
            """,
            (dedupe_key, status, notes),
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
    conn.commit()
    return True
