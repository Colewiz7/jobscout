# JobSeer product roadmap

This file records approved product requirements that are intentionally deferred
to their implementation phase. It is a scope guard, not a promise to create
tables early: each migration lands only with the first UI that uses it.

## Phase 3 — Quick-fill

- Implemented: a story bank capped at 10 STAR+Reflection stories. Stories carry
  multiple competency tags, appear as their own Quick-fill group, participate
  in server-backed edit/autosave and JSON import/export, and remain behind the
  same production-disabled feature flag as the rest of Profile.
- Story facts are an approved source for later local-model drafts. Empty story
  fields remain hidden outside edit mode.

## Phase 4 — Queue and apply sessions

- Check posting liveness before a job enters Queue and again when an apply
  session starts.
- Prefer the provider detail API, then a conservative application-URL request.
  Treat an inconclusive request as unknown, not closed.
- A confirmed dead posting receives the visible status “Posting closed” and is
  automatically skipped. Record the check time and evidence so the decision is
  explainable.

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
