import assert from "node:assert/strict";
import test from "node:test";
import { dayCount, dayCountLabel, dayCountMarkup } from "../src/jobscout/static/day-count.js";

// fixed zone so the dst tests always cross a shift
process.env.TZ = "America/New_York";

const noon = (y, m, d) => new Date(y, m, d, 12);

test("applying today is day 0", () => {
  assert.equal(dayCount("applied", noon(2026, 10, 9), noon(2026, 10, 9)), 0);
  assert.equal(dayCount("applied", new Date(2026, 10, 9, 0, 5), new Date(2026, 10, 9, 23, 55)), 0);
});

test("counts local calendar days, not 24 hour blocks", () => {
  assert.equal(dayCount("applied", new Date(2026, 10, 8, 23, 59), new Date(2026, 10, 9, 0, 1)), 1);
  assert.equal(dayCount("applied", noon(2026, 10, 1), noon(2026, 10, 13)), 12);
});

test("dst boundaries dont shift the count", () => {
  assert.equal(dayCount("applied", new Date(2026, 2, 7, 22), new Date(2026, 2, 9, 1)), 2);
  assert.equal(dayCount("applied", new Date(2026, 10, 1, 0, 30), new Date(2026, 10, 2, 23, 30)), 1);
  assert.equal(dayCount("applied", new Date(2026, 9, 31, 23), new Date(2026, 10, 3, 1)), 3);
});

test("a future date clamps to zero", () => {
  assert.equal(dayCount("applied", noon(2026, 10, 20), noon(2026, 10, 9)), 0);
});

test("a date only value reads as a local day", () => {
  assert.equal(dayCount("applied", "2026-11-01", noon(2026, 10, 13)), 12);
});

test("missing or bad dates show nothing", () => {
  assert.equal(dayCount("applied", null), null);
  assert.equal(dayCount("applied", ""), null);
  assert.equal(dayCount("interviewing", undefined), null);
  assert.equal(dayCount("applied", "not a date"), null);
  assert.equal(dayCountMarkup("applied", null), "");
});

test("only waiting states count", () => {
  for (const status of ["applied", "interviewing"]) assert.equal(dayCount(status, noon(2026, 10, 1), noon(2026, 10, 4)), 3);
  for (const status of ["rejected", "withdrawn", "offer", "archived", "new", "saved", "queued", "applying"]) {
    assert.equal(dayCount(status, noon(2026, 10, 1), noon(2026, 10, 4)), null);
  }
});

test("labels read naturally", () => {
  assert.equal(dayCountLabel(0), "Applied today");
  assert.equal(dayCountLabel(1), "Applied 1 day ago");
  assert.equal(dayCountLabel(12), "Applied 12 days ago");
});

test("markup pairs the visible text with a spoken one", () => {
  const html = dayCountMarkup("applied", noon(2026, 10, 1), noon(2026, 10, 13));
  assert.match(html, /aria-hidden="true">Day 12</);
  assert.match(html, /Applied 12 days ago/);
});
