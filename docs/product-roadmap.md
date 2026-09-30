# JobSeer product roadmap

This file records approved product requirements that are intentionally deferred
to their implementation phase. It is a scope guard, not a promise to create
tables early: each migration lands only with the first UI that uses it.

## Phase 3 — Quick-fill

- Implemented behind the production-disabled Profile flag: docked and pop-out
  Quick-fill, ATS-specific group ordering, keyboard copy, persistent per-job
  copy markers, global templates with per-job overrides, dated documents,
  server-backed autosave with rollback, and JSON import/export with undo.
- Implemented: password-free per-company ATS account metadata. It records
  yes/no/unknown, sign-in email, and an HTTPS link to the user's password
  manager; it never accepts or stores a password.
- Implemented: a story bank capped at 10 STAR+Reflection stories. Stories carry
  multiple competency tags and appear as their own Quick-fill group.
- Story facts are an approved source for later local-model drafts. Empty story
  fields remain hidden outside edit mode.
- Resume-used-per-application moves with the Application/Snapshot work in
  Phase 4, where there is an application record to attach it to.

## Phase 4 — Queue and apply sessions

- Implemented: persistent Queue order with drag, move-earlier, and move-later
  controls; initial order is deadline then scout score until manually changed.
- Implemented: focused, full-screen apply sessions with progress, elapsed time,
  embedded Quick-fill, tab-return submission prompt, automatic advance, and an
  applied/skipped/time summary.
- Implemented: posting liveness checks before Queue entry and again when a
  session starts. Provider detail APIs are preferred, followed by a conservative
  application-URL check. Inconclusive results remain unknown and do not block.
- Implemented: a confirmed dead posting receives the visible status “Posting
  closed,” is archived and skipped automatically, and retains check time and
  plain-language evidence.
- Implemented: marking applied stores the application timestamp and selected
  resume version and snapshots the posting’s structured, plain-text state.

## Phase 5 — Tracker and companies

- Add a read-only score calibration view comparing the scout score at discovery
  with the eventual application outcome. It must never tune or mutate scoring.
- Import a user-selected LinkedIn connections CSV locally. Match normalized
  company names against companies in the funnel and show “N connections” on
  company pages and job rows.
- Treat the CSV as third-party personal data: do not commit it, include it in
  logs, send it to a model, or expose unmatched contact details in list views.

## Phase 6 — Eligibility and matching

- A hard blocker produces one “Do not apply” verdict at the top of the reading
  pane. Hard blockers are limited to explicit no-sponsorship language,
  clearance requirements, and degree or graduation-date mismatches.
- The verdict has one-click override. Every override records the job, blocker,
  original evidence, timestamp, and override note; it never erases the finding.
- Requirement weights identify their provenance as explicit wording, posting
  structure, or estimate. Estimated weights can never produce a top-band match.
- Keep the existing rule against fake percentages: show evidence and plain
  matched/missing requirements rather than invented precision.

## Scout and Inbox

- Detect reposts when the same company and normalized title reappear within 90
  days. Surface one neutral “Reposted” chip.
- At three or more appearances, add a ghost-job flag with the count and dates.
  This is an observation, not a claim about the employer’s intent.

## Phase 8 — Local-model drafts

- Enforce faithfulness in deterministic code after generation. A draft may use
  only facts present in Profile, the story bank, or the current posting.
- Unsupported claims block the draft from ready-to-copy state and render a
  highlighted diff. The user edits or removes them; the model cannot waive the
  gate.
