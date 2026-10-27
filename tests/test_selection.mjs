import assert from "node:assert/strict";
import test from "node:test";

import { eligibilityVerdict, loadLatest, splitEligible } from "../src/jobscout/static/selection.js";

const job = (key, status = "new") => ({ dedupe_key: key, status });
const keys = (jobs) => jobs.map((item) => item.dedupe_key);

test("splitEligible keeps blocked jobs out of the batch", () => {
  const blocked = new Set(["b"]);
  const { eligible, blocked: skipped } = splitEligible([job("a"), job("b"), job("c")], (item) => blocked.has(item.dedupe_key) ? "blocked" : "eligible");
  assert.deepEqual(keys(eligible), ["a", "c"]);
  assert.deepEqual(keys(skipped), ["b"]);
});

test("splitEligible skips jobs whose check failed", () => {
  const verdicts = { a: "eligible", b: "unknown", c: "blocked", d: "unknown" };
  const groups = splitEligible([job("a"), job("b"), job("c"), job("d")], (item) => verdicts[item.dedupe_key]);
  assert.deepEqual(keys(groups.eligible), ["a"]);
  assert.deepEqual(keys(groups.blocked), ["c"]);
  assert.deepEqual(keys(groups.unknown), ["b", "d"]);
});

test("splitEligible handles all or none blocked", () => {
  assert.deepEqual(keys(splitEligible([job("a"), job("b")], () => "blocked").eligible), []);
  assert.deepEqual(keys(splitEligible([job("a"), job("b")], () => "eligible").blocked), []);
  assert.deepEqual(splitEligible([], () => "blocked"), { eligible: [], blocked: [], unknown: [] });
});

test("eligibilityVerdict only trusts a settled verdict", () => {
  assert.equal(eligibilityVerdict(undefined, true), "unknown");
  assert.equal(eligibilityVerdict({ loading: true, value: null, error: "" }, true), "unknown");
  assert.equal(eligibilityVerdict({ loading: false, value: null, error: "Eligibility did not respond." }, true), "unknown");
  assert.equal(eligibilityVerdict({ loading: false, value: null, error: "" }, true), "unknown");
  assert.equal(eligibilityVerdict({ loading: false, value: { verdict: "do_not_apply" }, error: "" }, true), "blocked");
  assert.equal(eligibilityVerdict({ loading: false, value: { verdict: "apply" }, error: "" }, true), "eligible");
});

test("eligibilityVerdict passes everything with quick-fill off", () => {
  assert.equal(eligibilityVerdict(undefined, false), "eligible");
  assert.equal(eligibilityVerdict({ loading: false, value: { verdict: "do_not_apply" }, error: "" }, false), "eligible");
});

// lets a test reorder responses
function deferredFetch() {
  const calls = [];
  const fetch = (key) => new Promise((resolve) => calls.push({ key, resolve }));
  return { calls, fetch };
}

test("loadLatest shares one request per key", async () => {
  const cache = new Map();
  const { calls, fetch } = deferredFetch();
  const first = loadLatest(cache, "a", fetch);
  const second = loadLatest(cache, "a", fetch);
  assert.equal(first, second);
  assert.equal(calls.length, 1);
  calls[0].resolve({ value: "ok" });
  assert.equal(await first, true);
  assert.deepEqual(cache.get("a"), { value: "ok" });
  assert.equal(await loadLatest(cache, "a", fetch), false);
});

test("loadLatest drops a response the cache moved past", async () => {
  const cache = new Map();
  const { calls, fetch } = deferredFetch();
  const stale = loadLatest(cache, "a", fetch);
  cache.clear();
  const fresh = loadLatest(cache, "a", fetch);
  calls[0].resolve({ value: "old profile" });
  await Promise.resolve();
  assert.equal(cache.get("a").loading, true);
  calls[1].resolve({ value: "new profile" });
  assert.equal(await stale, true);
  assert.equal(await fresh, true);
  assert.deepEqual(cache.get("a"), { value: "new profile" });
});

test("loadLatest rechecks when a stale response finds the cache empty", async () => {
  const cache = new Map();
  const { calls, fetch } = deferredFetch();
  const stale = loadLatest(cache, "a", fetch);
  cache.clear();
  calls[0].resolve({ value: "old profile" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 2);
  calls[1].resolve({ value: "new profile" });
  assert.equal(await stale, true);
  assert.deepEqual(cache.get("a"), { value: "new profile" });
});

test("loadLatest keeps a settled entry written over a request", async () => {
  const cache = new Map();
  const { calls, fetch } = deferredFetch();
  const pending = loadLatest(cache, "a", fetch);
  cache.set("a", { value: "override" });
  calls[0].resolve({ value: "old" });
  assert.equal(await pending, false);
  assert.deepEqual(cache.get("a"), { value: "override" });
});

test("loadLatest force replaces an in-flight request", async () => {
  const cache = new Map();
  const { calls, fetch } = deferredFetch();
  const first = loadLatest(cache, "a", fetch);
  const forced = loadLatest(cache, "a", fetch, true);
  assert.notEqual(first, forced);
  calls[0].resolve({ value: "old" });
  calls[1].resolve({ value: "new" });
  await first;
  assert.deepEqual(cache.get("a"), { value: "new" });
});

test("loadLatest stops forwarding after two hops", async () => {
  const cache = new Map();
  const { calls, fetch } = deferredFetch();
  const pending = loadLatest(cache, "a", fetch);
  for (let hop = 0; hop < 3; hop++) {
    cache.clear();
    calls[hop].resolve({ value: hop });
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.equal(await pending, false);
  assert.equal(calls.length, 3);
  assert.equal(cache.has("a"), false);
});
