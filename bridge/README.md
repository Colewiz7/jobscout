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

`jobseer-bridge serve` runs the same tools over streamable HTTP so a hosted client can use them.
The bridge is an OAuth resource server only: it checks bearer tokens and never issues them. Your
identity provider does the sign-in. The stdio mode above is unchanged.

    pip install '.[remote]'
    jobseer-bridge serve

**This endpoint can change live data.** Keep writes off unless you need them. The defaults are
read-only (`JOBSEER_READONLY` unset means on) and the read tool list (`JOBSEER_TOOLS=read`). Writes need
both `JOBSEER_READONLY=0` and a tool list that includes them; the stricter of the two always wins.

It refuses to start unless these are set: `JOBSEER_AUTH_ISSUER`, `JOBSEER_AUTH_JWKS_URL`,
`JOBSEER_AUTH_AUDIENCE`, `JOBSEER_AUTH_SUBJECT`, `JOBSEER_PUBLIC_URL`, `JOBSEER_ALLOWED_HOSTS`,
`JOBSEER_ALLOWED_ORIGINS`. Issuer, key set and public addresses must be https; plain http is
accepted only for loopback.

| env | meaning |
| --- | --- |
| `JOBSEER_AUTH_ISSUER` | token issuer, matched exactly against `iss`, also advertised in the metadata |
| `JOBSEER_AUTH_JWKS_URL` | where the signing keys are fetched from |
| `JOBSEER_AUTH_AUDIENCE` | value that must appear in `aud` (the client id) |
| `JOBSEER_AUTH_SUBJECT` | the one `sub` that may use the endpoint |
| `JOBSEER_AUTH_SCOPE` | scope the token must carry, default `jobseer:mcp` |
| `JOBSEER_PUBLIC_URL` | public address of the endpoint, for example `https://HOST/mcp`; the path must be `/mcp` |
| `JOBSEER_ALLOWED_HOSTS` | comma list of exact `Host` values, `host` or `host:port`; wildcards, schemes and empty entries are refused |
| `JOBSEER_ALLOWED_ORIGINS` | comma list of exact origins like `https://host[:port]`; `http` is allowed only for a loopback origin when the bind host is loopback |
| `JOBSEER_BIND_HOST`, `JOBSEER_BIND_PORT` | listen address, default `127.0.0.1` and `8000` |
| `JOBSEER_MAX_BODY` | request body cap in bytes, default 262144 |
| `JOBSEER_READONLY` | read-only unless set to `0` |
| `JOBSEER_TOOLS` | `read` (default), `all`, or a comma list of tool names |
| `JOBSEER_RATE_LIMIT`, `JOBSEER_RATE_WINDOW` | calls allowed per window per subject, default 60 per 60 seconds; limit 0 turns it off |
| `JOBSEER_AUDIT_LOG` | path of the audit file, unset means no audit |
| `JOBSEER_URL`, `JOBSEER_TOKEN`, `JOBSEER_TOKEN_FILE`, `JOBSEER_USER` | the dashboard to drive and how to sign in to it, as above |

Placeholders to start from (use your own values, and keep the app password in a mounted file):

    JOBSEER_AUTH_ISSUER=https://auth.example.com/application/o/APP/
    JOBSEER_AUTH_JWKS_URL=https://auth.example.com/application/o/APP/jwks/
    JOBSEER_AUTH_AUDIENCE=CLIENT_ID
    JOBSEER_AUTH_SUBJECT=USERNAME
    JOBSEER_PUBLIC_URL=https://mcp.example.com/mcp
    JOBSEER_ALLOWED_HOSTS=mcp.example.com
    JOBSEER_ALLOWED_ORIGINS=https://client.example.com
    JOBSEER_URL=https://jobseer.example.com
    JOBSEER_TOKEN_FILE=/run/secrets/app-password

What is checked on every request:

- Only RS256 is accepted. `none` and HMAC tokens are refused, as are a wrong signature, issuer,
  audience, subject or scope, and an expired token or one not yet valid (30 seconds of skew).
- Keys are cached for five minutes. An unknown key id triggers one refetch at most every 30 seconds.
  If the key set cannot be fetched, every token is refused.
- The authorization server is assumed to ignore resource indicators, so the audience is checked
  here instead.
- `GET /healthz` answers `ok` without a token and nothing else.
- Hosts and origins outside the allowed lists are rejected before any tool runs.

**Single replica.** Confirm ids are held in process memory and bound to the caller's token subject
and client. Another caller's id is refused and does not use the original up. A second replica would
not know the first one's ids, so run exactly one. A restart drops pending previews.

**Audit log.** With `JOBSEER_AUDIT_LOG` set, each call appends one JSON line: time, subject, tool
name, SHA-256 of the arguments, status (`ok`, `error`, `rate_limited`, `denied`) and the confirm
step (`previewed`, `applied`, `rejected`). Arguments, results and the `Authorization` header are
never written, and known secrets are scrubbed from the text fields. If a write to the file fails a
warning goes to stderr and the call is not blocked.

**Capture hosts.** In remote mode `job_capture` only previews postings on Greenhouse, Lever, Ashby
and Workday hosts (port 443), the sites the dashboard reads through their own APIs. A posting on any
other site is refused, because the dashboard stores its address and later checks whether it is still
live by fetching it and following redirects. The host is then resolved at preview time and refused
if any returned address is not public. The DNS step is a pre-check only: the dashboard resolves the
name again when it fetches, so an answer that changes in between is not caught. A test compares the
site list with the dashboard's so the two cannot drift silently. Stdio mode still accepts any public
https address.

**Container.** `docker build -t jobseer-bridge bridge/` builds a slim image that runs as a
non-root user, holds no secrets and starts `jobseer-bridge serve`. It writes nothing to disk, so a
read-only root filesystem works. Pass the values above as env, mount the token file, and mount a
writable volume if you use the audit log.

## Behaviour

- One keep-alive connection. `GET /jobs` is cached for a few seconds and dropped on any write.
- Writes fetch the session for the CSRF token, send the cookie, token and a matching
  Origin. A 403 refetches the session once and retries once.
- Writes send the current `expected_*` or `revision` value unless you pass one. A 409 comes back as
  `{"ok": false, "conflict": true, "message": ..., "current": ...}` rather than an error.
- `job_capture` refuses non-https and local or private targets before it previews, because the
  server fetches the address. The dashboard itself only requires https. Remote mode is stricter, see above.
- Confirm tools return a preview and a short-lived `confirm_id` (two minutes, single use, tied to the
  exact arguments). Repeat the call with the id to apply it.

## Known limits

- The rule on/off route ignores revisions, so the bridge checks the revision itself just before
  the toggle. That check is not atomic; a change in that gap is not caught. Making it atomic needs
  a change in the dashboard.
- Deleting a follow-up has no restore route, and removing a profile template, document or story is
  permanent. Both sit behind preview and confirm; the profile diff preview is the safeguard.

## Tools

51 tools. Every mutating dashboard route has one; the coverage test fails if one is missing.

read (13, always on): `status`, `inbox_list`, `job_get`, `queue_get`, `tracker_get` (also returns the
activity history), `companies_list`, `company_get`, `rules_list`, `views_list`, `profile_get`,
`interviews_ics`, `apply_session_get`, `overview_get` (saved overview only, never generates)

write (24): `job_set_status`, `jobs_tag` (confirm above 10 jobs), `job_edit`,
`queue_reorder`, `board_reorder`, `apply_session`, `interview_save`,
`reminder_save`, `contact_save`, `company_save`, `rule_save`, `view_save`, `profile_context_set`,
`profile_copy_mark`, `activity_dismiss`, `overview_request`,
`overview_regenerate`, plus the undo tools `jobs_hide_undo`, `jobs_tag_undo`, `job_edit_undo`,
`rule_action_undo`, `restore_record`, `company_merge_undo`, `overview_regenerate_undo`

confirm (14, two steps): `jobs_hide`, `delete_record`, `company_merge`, `company_hide`,
`profile_update` (shows a diff, fails if the profile moved after the preview), `profile_field_set`,
`profile_story_save`, `profile_template_save`, `profile_document_save` (same diff and revision check,
one item at a time), `connections_import`, `job_capture`, `eligibility_override`, `job_description_paste`,
`overview_dismiss`

Where the app offers undo, the write result carries what the undo tool needs: `undo_token`
(`jobs_hide_undo`), tag `ids` (`jobs_tag_undo`), `edit_id` (`job_edit_undo`), `rule_action_id` on the
job (`rule_action_undo`), `merge_id` (`company_merge_undo`) and the overview `revision`.

Version checks: writes send the version the route accepts (`expected_*`, `revision`). The undo tools
that take a server-issued id (`jobs_tag_undo`, `job_edit_undo`, `rule_action_undo`) send none, because
the id is the guard and the server refuses a second use. `overview_regenerate_undo` needs the
`expected_revision` you last saw. `job_description_paste` stores a hash of the saved text at preview, re-reads it at confirm and
returns a conflict if it changed. `activity_dismiss`, `profile_copy_mark` and the overview dismiss
and regenerate routes accept no version; the server answers a repeat with a conflict where it can.

The overview tools need the server's AI overview feature and say so when it is off. The quick-fill
profile tools need quick-fill on.

## Test

    python3.12 -m venv .venv
    .venv/bin/pip install -r ../requirements.txt -e '.[test]'
    make test

Tests start the dashboard in-process with the demo store, so no database or network is needed.
`tests/test_route_coverage.py` fails when a mutating dashboard route has no tool. Set `JOBSCOUT_TEST_DSN` to also run `tests/test_pg_smoke.py` against Postgres.
