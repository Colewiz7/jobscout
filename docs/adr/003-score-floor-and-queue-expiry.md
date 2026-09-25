# 003. A score floor and an expiring queue, not just a cap

Status: accepted, 2026-09-25

## Context

Over the per-run cap, the job used to send one summary message and then mark
every pending posting notified. The summary was visible, so the behaviour
looked reasonable. It was data loss: everything past the cap was recorded as
sent and never sent.

The first fix was to hold the tail unnotified so it leads the next run.

That fix created a slower version of the same problem. The feed is ranked, and
some postings score below zero: a discipline that shares the vocabulary, a
term that has already gone. Holding them means that on a quiet week they reach
the front of the queue and get sent, having been ranked last precisely because
they should not be.

## Decision

Two bounds on the queue.

A **floor**, `min_notify_score`, default 40. Below it a posting is stored and
scored and explained, and never sent, and never carried. It stays available to
`export` and to a database query.

An **expiry**, `notify_queue_days`, default 14. A posting that has waited that
long leaves the queue. An internship that has been queued a fortnight has
either been filled or was never interesting.

## Consequences

The queue cannot grow without bound and cannot promote by attrition. Both
bounds are config, so they move without an image build.

The risk is a floor set too high silently hiding good matches. That is why the
score and its full breakdown are written to the database for every candidate
rather than only for the ones that push: what was dropped and why is always
answerable after the fact.
