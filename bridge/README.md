# jobseer bridge

Stdio tool server that lets a tool client manage JobSeer through the dashboard's
own HTTP API. It is a thin client: the dashboard keeps every safety check (hidden
job rules, eligibility gate, revisions, CSRF). This package is separate from the
root install, so nothing here is part of the image, requirements or CI.

## Run

    python3.12 -m venv .venv
    .venv/bin/pip install -e .
    JOBSEER_URL=http://127.0.0.1:8080 .venv/bin/jobseer-bridge

Point it at a local demo dashboard (`python -m jobscout dashboard --demo`) first.
The server speaks the stdio tool protocol, so register the `jobseer-bridge`
command and its env in your client's server config.

## Client setup

Register the bridge as a stdio server in your tool client. Use the absolute path to the
venv script, since the system python does not have the protocol library and the venv is required:

    {
      "mcpServers": {
        "jobseer": {
          "command": "/ABSOLUTE/PATH/TO/bridge/.venv/bin/jobseer-bridge",
          "env": {
            "JOBSEER_URL": "http://127.0.0.1:8080",
            "JOBSEER_READONLY": "1"
          }
        }
      }
    }

Drop `JOBSEER_READONLY` to enable writes. Most clients also have a command-line add form that
takes the same command and env pairs; check your client's docs for it. Keep the token itself out of
this config; use `JOBSEER_TOKEN_CMD` below.

- The profile tools (`profile_get`, `profile_update`, `profile_context_set`) need
  `JOBSCOUT_QUICK_FILL_ENABLED=1` on the dashboard. It is off in a plain `--demo` run.
- In read-only mode the write tools are not registered, so calling one gets "Unknown tool".
- A confirm attempt with different arguments burns its `confirm_id`; ask for a new preview.
- `python -m jobscout dashboard --demo` binds 0.0.0.0. Only run it on a trusted network.

| env | meaning |
| --- | --- |
| `JOBSEER_URL` | dashboard address, default `http://127.0.0.1:8080` |
| `JOBSEER_READONLY` | `1` registers only the read tools and refuses writes |
| `JOBSEER_CACHE_TTL` | seconds to reuse the inbox list, default 10 |
| `JOBSEER_TOKEN` | app password, sent as HTTP Basic auth on every request |
| `JOBSEER_TOKEN_FILE` | file holding the token, read once at startup; warns if group or other can read it |
| `JOBSEER_TOKEN_CMD` | command whose output is the token, run once at startup |
| `JOBSEER_USER` | Basic auth user name, default `cole` |
| `JOBSEER_AUTHENTIK_USER` | send the proxy headers, for a local non-demo server only |
| `JOBSEER_AUTHENTIK_APP`, `JOBSEER_AUTHENTIK_OUTPOST` | header values, defaults `jobseer` and `local` |

There is no live address built in. Every result is wrapped as
`{"target": <url>, "data": ...}` so you can see which server answered.

## Live access with an app password

To reach a server behind the identity proxy, create an app password for your user and point
the bridge at the public https address. Prefer a command that reads it from a password manager:

    JOBSEER_URL=https://jobseer.example.com
    JOBSEER_USER=cole
    JOBSEER_TOKEN_CMD='op read op://VAULT/ITEM/password'

Then verify access before registering the server:

    JOBSEER_URL=... JOBSEER_TOKEN_CMD=... .venv/bin/jobseer-bridge check

`check` makes one read-only call (`GET /api/v1/session`) and prints the target, HTTP status,
whether auth was accepted and the session user. It exits 0 on success and never prints the token.

- `JOBSEER_TOKEN_CMD` is split into words and run without a shell, once, with a 5 second timeout.
  Its stderr is discarded. If `JOBSEER_TOKEN` is also set, that value is used and the command is skipped.
  Precedence is `JOBSEER_TOKEN`, then `JOBSEER_TOKEN_FILE`, then `JOBSEER_TOKEN_CMD`.
- With a token, a non-loopback target must use https. Plain http is allowed only for localhost,
  `127.0.0.0/8` and `::1`.
- A token turns off the proxy header mode, so the two are never sent together.
- A 401, a redirect to sign-in or a challenge page fails once with the status and the hop that
  answered, and is not retried. Response bodies are never shown.
- The token is kept out of logs, errors, tool results and the target label.
- The app password acts as your user everywhere the proxy lets you in, not just in this bridge.
  Treat it like a password and keep `JOBSEER_READONLY=1` on until you need writes.
- App passwords expire. When `check` starts reporting 401, create a new one, update the vault
  item and run `check` again. Nothing else needs to change if you use `JOBSEER_TOKEN_CMD`.

## Remote mode

`jobseer-bridge serve` is the network form of the server (full setup below). Confirm ids are held in
the process memory and bound to the caller's token subject and client, so another caller's id is
refused and does not use the original up. Because of that, run exactly one replica; a second one
would not know the ids the first issued. A restart drops pending previews.

## Behaviour

- One keep-alive connection. `GET /jobs` is cached for a few seconds and dropped on any write.
- Writes fetch the session for the CSRF token, send the cookie, token and a matching
  Origin. A 403 refetches the session once and retries once.
- Writes send the current `expected_*` or `revision` value unless you pass one. A 409 comes back as
  `{"ok": false, "conflict": true, "message": ..., "current": ...}` rather than an error.
- `job_capture` refuses non-https and local or private targets before it previews, because the
  server fetches the address. The dashboard itself only requires https.
- Confirm tools return a preview and a short-lived `confirm_id` (two minutes, single use, tied to the
  exact arguments). Repeat the call with the id to apply it.

## Known limits

- The rule on/off route ignores revisions, so the bridge checks the revision itself just before
  the toggle. That check is not atomic; a change in that gap is not caught. Making it atomic needs
  a change in the dashboard.
- Deleting a follow-up has no restore route, and removing a profile template, document or story is
  permanent. Both sit behind preview and confirm; the profile diff preview is the safeguard.

## Tools

read (11, always on): `status`, `inbox_list`, `job_get`, `queue_get`, `tracker_get`,
`companies_list`, `company_get`, `rules_list`, `views_list`, `profile_get`, `interviews_ics`

write (16): `job_set_status`, `jobs_tag` (confirm above 10 jobs), `job_edit`, `queue_reorder`,
`board_reorder`, `apply_session`, `interview_save`, `reminder_save`, `contact_save`, `company_save`,
`rule_save`, `view_save`, `profile_context_set`, plus the undo tools `jobs_hide_undo`,
`restore_record`, `company_merge_undo`

confirm (8, two steps): `jobs_hide`, `delete_record`, `company_merge`, `company_hide`,
`profile_update` (shows a diff, fails if the profile moved after the preview), `connections_import`,
`job_capture`, `eligibility_override`

Skipped on purpose: activity feed, summary worker routes, tag and posting-edit undo, copy bookkeeping.
The list lives in `src/jobseer_bridge/skipped.py`.

## Test

    python3.12 -m venv .venv
    .venv/bin/pip install -r ../requirements.txt -e '.[test]'
    make test

Tests start the dashboard in-process with the demo store, so no database or network is needed.
`tests/test_route_coverage.py` fails when a mutating dashboard route has no tool and is not in
`skipped.py`. Set `JOBSCOUT_TEST_DSN` to also run `tests/test_pg_smoke.py` against Postgres.
