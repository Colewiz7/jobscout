# jobscout

Finds DevOps, SRE, platform and infrastructure internships and co-ops, and
pushes the ones worth reading to a phone.

It exists because the general-purpose internship lists are software
engineering lists. Filtering one for infrastructure work returns almost
nothing, not because the roles are rare but because they are posted somewhere
else, under titles that share their vocabulary with the sales org.

## How it runs

A Kubernetes CronJob every six hours. No web service, no always-on process:
the job wakes, reads every configured board, writes what it found, pushes what
is new, and exits.

```
CronJob (6h)
  ├── sources ──> 264 boards across 6 providers + the Simplify lists
  ├── filters ──> shape, level, location, term
  ├── scoring ──> rank, collapse duplicates, apply the floor
  ├── CNPG Postgres ──> every posting, its score, and why
  │       └── WAL + daily base backup to Cloudflare R2
  └── ntfy ──> the top of the queue, one topic
```

Nothing in the pod holds state. The database is a CloudNativePG cluster in the
same namespace, archiving WAL and a daily base backup to R2, so the history of
what was seen and when survives the cluster. Notifications go to ntfy on its
own topic, separate from the Kubernetes alert stream, because a job match and
a failing pod want different levels of attention.

Configuration is a ConfigMap. The board list, the filters and the scoring
knobs are all data, so tuning the search is a commit and an Argo CD sync
rather than an image build.

## Providers

A provider is a callable that takes a fetcher, a board identifier and a
display name, and returns postings or `None`. `None` means the board did not
answer, which matters: a board that returns an empty list is a board with
nothing open, and its old postings are eligible to be closed. A board that
failed is not, and conflating the two silently retires jobs that are still
live.

| provider | board identifier | shape |
| --- | --- | --- |
| greenhouse, lever, ashby | slug | one GET, public JSON |
| workday | `tenant/wdN/site` | POST, paged, one query per search term |
| amazon | a search query | one GET against an undocumented endpoint |
| phenom | careers host | sitemap, then a detail fetch per plausible job |

Two of these took a detour worth recording.

**Workday** wants three values rather than a slug, and its `searchText` is a
substring match. Searching `intern` returns 755 of one employer's 804
postings because it also matches "Internal Audit"; searching `devops` returns
23. So the fan out is over the infrastructure words and the level is decided
locally, where it can be precise.

**Phenom** renders its results from a client-side template, so the obvious
conclusion is that it needs a browser. It does not. Every Phenom site
publishes a sitemap listing each job, and every job page carries schema.org
`JobPosting` with title, location, `datePosted` and `employmentType`. The
sitemap slug is enough to discard most jobs without opening them, and only
plausible ones cost a request.

## Filtering

Four gates, in order:

1. **Shape.** Is the title about this work. The pattern carries an anchored
   negative lookahead, because `platform`, `cloud` and `network` belong to the
   sales org too. One employer lists twelve "account" roles against one
   "devops".
2. **Level.** Is it a co-op or an internship. Where a feed publishes
   `employmentType`, `INTERN` settles it even when the title never says so,
   since real co-ops get titled "Technology Development Program".
3. **Location.** US or remote. Workday writes locations as
   `US-NY-Rochester`, and `CA-ON-Toronto` opens with Canada rather than
   California, so the country field is read first.
4. **Term.** Spring or Summer 2027, with the caveat that most titles name no
   season at all and an unnamed term is not evidence of the wrong one.

## Scoring

Matching is binary, but the feed is ordered and capped, so the ordering is
what decides which matches are ever seen.

| signal | points |
| --- | --- |
| title names the work (`devops`, `site reliability`, `data center`) | +100 |
| title only hints at it (`technology`, `platform`) | +25 |
| co-op | +40 |
| internship | +20 |
| names a wanted term | +30 |
| posted within a week | +10 |
| a discipline that shares the vocabulary (aerodynamics, actuarial) | −60 |
| names a term that has already gone | −70 |

The score and its breakdown are written to the database for every candidate,
not only the ones that push, so a posting that never arrived can still be
explained afterwards.

Three rules sit on top:

- **Collapse duplicates.** One employer lists a single co-op once per office,
  eleven rows for one job, enough to fill the notification budget alone.
- **A floor.** Below it a posting is stored and explained but never sent, so a
  quiet week cannot promote the dross by attrition.
- **Hold, do not drop.** Anything over the per-run cap stays unnotified and
  leads the next run. An earlier version summarised the tail and then marked
  it notified, which meant it was never sent at all.

## The drift guard

The deployed config and the one in this repo are different files and always
will be: the deployed one carries the boards and the employers being targeted,
which do not belong in a public repo. They drifted once, and the tests kept
passing against rules that were not the rules in production.

So they are held to the same behaviour rather than the same bytes.
`tests/contract_cases.yaml` is that contract. This repo runs it against its own
default, and the gitops repo runs the same file against the ConfigMap it
deploys, so either side drifting is a failing build.

```bash
python -m jobscout --config path/to/filters.yaml check
```

## Running it

```bash
pip install -e .
python -m jobscout --config config/filters.yaml check   # contract
python -m jobscout run                                  # needs DATABASE_URL
JOBSCOUT_SEED=true python -m jobscout run               # record, push nothing
python -m jobscout discover-boards                      # derive slugs
```

`JOBSCOUT_SEED` exists because the first run after adding boards treats every
existing match as new. Seeding records them and pushes nothing, so the first
real notification is a genuinely new posting.

## Layout

```
src/jobscout/
  __main__.py      CLI: run, check, discover-boards, export
  config.py        the ConfigMap, typed
  filters.py       the four gates, scoring, ranking
  models.py        Posting, and the dedupe key
  db.py            schema, upsert, the close rule, scores
  notify.py        ntfy
  sources/
    boards.py      every provider
    simplify.py    the community lists
```
