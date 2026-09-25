# 002. Read Phenom career sites through their sitemap

Status: accepted, 2026-09-25

## Context

Eight employers on the target list run Phenom career sites, Cisco and Excellus
among them. Phenom renders search results from a client-side template, so
fetching the page returns markup with `${eachJob.title}` in it and no jobs.

The obvious reading is that this source needs a headless browser. A browser in
a CronJob is a large dependency, a slow run and a fragile scraper, so it was
worth a few minutes to be sure.

There is a JSON endpoint, `/api/apply/v2/jobs`. It answers
`{"status":"failure","errorMsg":"Tenant not identified"}` for every domain
parameter tried, and the tenant identifier is not in the rendered page.

## Decision

Read the sitemap instead. Every Phenom site publishes `/sitemap.xml` listing
each job, and every job page carries a schema.org `JobPosting` block with
title, location, `datePosted`, `validThrough` and `employmentType`.

The slug in the job URL is enough to discard most jobs without opening them.
Only plausible ones cost a request, throttled, with a ceiling per board.

## Consequences

No browser, and the structured data is better than what scraping the rendered
page would have produced: `employmentType` is a more reliable level signal
than parsing the title.

The slug prefilter must stay loose. Slugs are truncated and lose punctuation,
so it only discards what carries no role word at all, and the real filter runs
against the title from the job page. `cloud-platform-sales` gets opened and
then rejected on "Cloud Platform Sales Specialist". Contract cases cover that.

Three of the eight publish no root sitemap and remain unreachable. A partial
source beat a browser.
