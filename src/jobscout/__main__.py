"""jobscout CLI: run | discover-boards | selftest."""
from __future__ import annotations

import argparse
import logging
import os
import sys

import yaml

from . import db as database
from . import discover as discovery
from . import filters, models, notify
from .config import Config
from .http import Fetcher
from .sources import boards as boards_source
from .sources import simplify

log = logging.getLogger("jobscout")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def cmd_run(args) -> int:
    config = Config.load(args.config)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL is not set")
        return 2

    with Fetcher() as fetcher:
        simplify_rows = simplify.fetch(fetcher)
        board_rows, fetched = boards_source.fetch(fetcher, config.boards)

        # A source that parses to nothing where it used to have thousands of
        # rows is a format change, not an empty job market. Fail loudly rather
        # than quietly reporting "no new jobs" forever, which is exactly how a
        # broken scraper hides.
        for source, rows in simplify_rows.items():
            if not rows:
                log.error("%s parsed zero rows; refusing to treat that as empty", source)
                return 3

        matched_simplify, closed_keys = [], {}
        for source, rows in simplify_rows.items():
            closed_keys[source] = []
            for posting in rows:
                if not filters.keep(posting, config):
                    continue
                if posting.closed:
                    closed_keys[source].append(posting.fallback_key)
                else:
                    matched_simplify.append(posting)

        matched_boards = [p for p in board_rows if filters.keep(p, config)]
        log.info(
            "matched: %d simplify open, %d simplify closed, %d board",
            len(matched_simplify),
            sum(len(v) for v in closed_keys.values()),
            len(matched_boards),
        )

        conn = database.connect(dsn)
        try:
            database.ensure_schema(conn)
            new_rows = database.upsert_open(conn, matched_simplify + matched_boards)

            for source, keys in closed_keys.items():
                if keys:
                    log.info("%s: closed %d", source, database.mark_closed(conn, source, keys))

            # Only boards that answered are eligible to age their postings out.
            seen_ids: dict[tuple[str, str], set[str]] = {}
            for posting in matched_boards:
                seen_ids.setdefault((posting.source, posting.board), set()).add(posting.source_id)
            aged = database.close_missing_boards(conn, fetched, seen_ids)
            log.info("new rows: %d, closed by absence: %d", new_rows, aged)

            if _env_flag("JOBSCOUT_SEED"):
                marked = database.seed(conn)
                log.info("seed mode: marked %d rows notified, pushed nothing", marked)
                return 0

            pending = database.pending(conn)
            if not pending:
                log.info("no new matches")
                return 0

            base = os.environ.get("NTFY_URL", "")
            topic = os.environ.get("NTFY_TOPIC", "")
            token = os.environ.get("NTFY_TOKEN", "")
            if not base or not topic:
                log.error("NTFY_URL/NTFY_TOPIC not set; %d matches unsent", len(pending))
                return 4

            if len(pending) > config.max_notify_per_run:
                title, body = notify.format_summary(pending, config.max_notify_per_run)
                if not notify.push(fetcher, base, topic, token, title, body):
                    return 5
            else:
                for row in pending:
                    title, body = notify.format_posting(row)
                    if not notify.push(fetcher, base, topic, token, title, body, click=row["url"]):
                        return 5
            database.mark_notified(conn, [r["dedupe_key"] for r in pending])
            log.info("notified %d matches", len(pending))
        finally:
            conn.close()
    return 0


def cmd_export(args) -> int:
    """Dump every open posting as markdown, newest first.

    Seeding marks postings notified so they never push, which is right for a
    first run but would otherwise mean never hearing about the backlog at all.
    This is how the backlog gets read.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL is not set")
        return 2
    conn = database.connect(dsn)
    try:
        rows = database.open_postings(conn, only_unnotified=False)
    finally:
        conn.close()

    # Newest first; unknown age sorts last rather than pretending to be new.
    rows.sort(key=lambda r: (r["age_days"] is None, r["age_days"] or 0))

    lines = [
        f"# jobscout matches ({len(rows)} open)",
        "",
        "| Age | Company | Role | Locations | Terms | Link |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        link = f"[apply]({row['url']})" if row["url"] else "-"
        lines.append(
            "| {age} | {company} | {title} | {location} | {terms} | {link} |".format(
                age=models.age_label(row["age_days"]),
                company=row["company"].replace("|", "/"),
                title=row["title"].replace("|", "/"),
                location=(row["location"] or "-").replace("|", "/"),
                terms=row["terms"] or "-",
                link=link,
            )
        )
    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
        log.info("wrote %s (%d postings)", args.out, len(rows))
    else:
        sys.stdout.write(text)
    return 0


def cmd_discover(args) -> int:
    with Fetcher() as fetcher:
        result = discovery.discover(fetcher)
    payload = {
        "# generated by": "python -m jobscout discover-boards",
        "boards": {provider: [row["slug"] for row in rows] for provider, rows in result.items()},
        "companies": {
            provider: {row["slug"]: row["company"] for row in rows}
            for provider, rows in result.items()
        },
    }
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
        log.info("wrote %s", args.out)
    else:
        sys.stdout.write(text)
    return 0


def cmd_selftest(args) -> int:
    """Prove the image actually contains a working package.

    The unit tests run from the source tree, so they cannot catch a module that
    exists in git but was never copied into the image.
    """
    config = Config.load(args.config)
    from .models import Posting

    sample = Posting(
        source="simplify-s27",
        company="Acme",
        title="Cloud Infrastructure Intern",
        location="Austin, TX",
        url="https://boards.greenhouse.io/acme/jobs/123",
    )
    assert filters.keep(sample, config), "filter rejected a known-good posting"
    assert sample.dedupe_key == "greenhouse:123", sample.dedupe_key
    assert simplify.FILES and boards_source.PROVIDERS
    print(f"jobscout selftest ok: {len(boards_source.PROVIDERS)} providers, config loaded")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jobscout")
    parser.add_argument("--config", default=None, help="path to filters YAML")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="fetch, filter, store, notify")
    discover_parser = sub.add_parser("discover-boards", help="derive and probe board slugs")
    discover_parser.add_argument("-o", "--out", help="write YAML here instead of stdout")
    export_parser = sub.add_parser("export", help="dump open postings as markdown")
    export_parser.add_argument("-o", "--out", help="write markdown here instead of stdout")
    sub.add_parser("selftest", help="import and filter smoke test")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return {
        "run": cmd_run,
        "discover-boards": cmd_discover,
        "export": cmd_export,
        "selftest": cmd_selftest,
        None: cmd_run,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
