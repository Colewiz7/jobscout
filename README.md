# jobscout

Finds DevOps/SRE/platform internships and co-ops, dedupes them into Postgres,
and pushes new ones to ntfy. Runs as a k3s CronJob every 6 hours; the manifests
live in `homelab-gitops` under `apps/jobscout/`.

## Sources

- `SimplifyJobs/Summer2027-Internships` `README.md` and `README-Off-Season.md`,
  from the **`dev`** branch. `main` does not exist.
- Greenhouse, Lever and Ashby public board APIs, for the slugs listed in the
  ConfigMap. No auth needed on any of them.

## Commands

```bash
python -m jobscout run              # fetch, filter, store, notify (default)
python -m jobscout discover-boards  # derive board slugs from the READMEs
python -m jobscout selftest         # import + filter smoke test, used by CI
```

### discover-boards

Pulls every apply URL out of both READMEs, extracts the Greenhouse/Lever/Ashby
slug where the URL exposes one, probes each against the live API and writes the
ones that answer:

```bash
python -m jobscout discover-boards -o boards.discovered.yaml
```

Roughly 245 candidate slugs come out of the two files and about 194 answer. The
output has a `boards:` block ready to paste into the ConfigMap and a `companies:`
block mapping each slug back to a company name so the list can be pruned by
hand. Locked rows are included on purpose: a company whose Summer 2027 posting
has closed still has a live board worth watching.

## Environment

| variable | meaning |
|---|---|
| `DATABASE_URL` | Postgres DSN. In-cluster this is the `uri` key of the CNPG-generated `jobscout-db-app` secret. |
| `NTFY_URL` | e.g. `http://ntfy.ntfy.svc.cluster.local` |
| `NTFY_TOPIC` | `jobs` |
| `NTFY_TOKEN` | bearer token; ntfy is deny-all |
| `JOBSCOUT_SEED` | `true` marks everything known as notified and pushes nothing. Use on the first run. |
| `JOBSCOUT_CONFIG` | path to the filter YAML, default `config/filters.yaml` |

## Filtering

A posting is kept when the title looks like infrastructure work *and* like an
internship, the location is US or remote, and the term is one we want.

|  | Simplify S27 | Simplify off-season | Board APIs |
|---|---|---|---|
| title pattern | required | required | required |
| co-op/intern | required | required | required |
| terms | implicit | from the Terms column | from the title, if named |
| US or remote | required | required | required |

Everything above is in `config/filters.yaml`, mounted from a ConfigMap in the
cluster, so tuning the search is an edit and a sync rather than a rebuild.

Two notes on the term rule. Simplify's off-season Terms column is a
comma-separated list, so a row qualifies if *any* of its terms is wanted. Board
APIs have no such column and only about 17% of real board internship titles name
a season, so a title with no season passes rather than being treated as the
wrong cycle. Set `board_require_explicit_terms: true` to tighten that.

## Dedupe

`dedupe_key` is the provider job id whenever one can be extracted, from a
Greenhouse/Lever/Ashby URL or from an embedded `gh_jid` parameter, and otherwise
`sha256(company|title|terms)`. The URL host is deliberately not part of the
hash: the same job is reachable at a company careers page and at the provider's
own domain, and hashing the host would file those as two different jobs.

That is also why URL cleaning strips `utm_*` and `ref` but **keeps `gh_jid`**.

Because location is not in the hash, one role posted in three cities collapses
to a single row.

## Closing

- **Simplify:** a locked (🔒) row closes a posting we already track. A locked row
  we have never seen is not inserted at all.
- **Boards:** a posting absent from two consecutive *successful* fetches of its
  own board is closed. Scoping to boards that actually answered is what stops a
  single 404 from closing everything a board ever listed.

Closed postings are never notified.

## Guards

- A Simplify file that parses to zero rows fails the run. An empty parse is a
  format change, not an empty job market, and a scraper that quietly reports "no
  new jobs" forever is the failure mode worth engineering against.
- More than `max_notify_per_run` new matches in one run sends a single summary
  instead of one push per posting.

## Two bugs worth remembering

Both were found before this ever ran in the cluster, and both are the kind that
fail silently rather than loudly.

### A locked posting could never close the job it was tracking

`dedupe_key` prefers a provider job id, extracted from the apply URL. While a
posting is open, the Simplify row carries an apply URL, so the job is stored
under `greenhouse:777`.

When the role closes, Simplify replaces the entire Application cell with a
single 🔒. No link, no URL. So the closing row regenerates its key as
`sha256:...`, matches nothing, and the close is a silent no-op. The posting
stays "open" forever, and because it is already marked notified it never
re-notifies either. Nothing errors. The row is just quietly wrong.

The fix is `fallback_key`: the hash form is computed and stored on *every* row,
including rows that also have a provider id, so the close path always has
something a URL-less row can still produce. `mark_closed` matches on either key.

Accepted edge case: two genuinely different postings sharing a company, title
and terms also share a fallback key, so closing one closes both. Rare enough to
live with.

### `create table if not exists` skips new columns

Schema is applied by the job on every start, which is what makes a fresh CNPG
cluster work with no migration step. But `create table if not exists` is a
complete no-op on an existing table. It does not diff columns.

So adding `fallback_key` to the `create table` block would have done nothing to
an already-deployed database, and the next insert would have failed on a column
that the code was certain existed. The schema now carries explicit
`alter table ... add column if not exists` statements, verified by standing up a
table with the old shape and upgrading it.

The general lesson: an idempotent create is not an idempotent migration.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                  # DB tests skip without a Postgres

docker run -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=jobscout \
    -p 55432:5432 postgres:16-alpine
JOBSCOUT_TEST_DSN=postgresql://postgres:test@localhost:55432/jobscout pytest
```

CI runs the same suite against a Postgres service container, then builds the
image and runs `selftest` inside it before pushing to GHCR.
