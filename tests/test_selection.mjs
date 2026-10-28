import assert from "node:assert/strict";
import test from "node:test";

import { companyDismissTargets, companyLabel, eligibilityVerdict, fitActions, loadLatest, moveGroup, pruneKeys, rangeSelection, restoreSlots, selectAllVisible, splitEligible, stepKey, toggleKey, visibleSelection } from "../src/jobscout/static/selection.js";

const job = (key, status = "new", company = "Acme") => ({ dedupe_key: key, status, company });
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

test("companyDismissTargets only takes pre-application jobs", () => {
  const jobs = ["new", "saved", "queued", "applying", "applied", "interviewing", "offer", "rejected", "archived"].map((status) => job(status, status));
  jobs.push(job("other", "new", "Globex"));
  const { companies, targets } = companyDismissTargets(jobs, new Set(["new"]));
  assert.deepEqual(companies, ["Acme"]);
  assert.deepEqual(keys(targets), ["new", "saved", "queued"]);
});

test("companyDismissTargets covers every selected company", () => {
  const jobs = [job("a", "applied"), job("b", "saved"), job("c", "new", "Globex"), job("d", "new", "Initech")];
  const { companies, targets } = companyDismissTargets(jobs, new Set(["a", "c"]));
  assert.deepEqual(companies, ["Acme", "Globex"]);
  assert.deepEqual(keys(targets), ["b", "c"]);
});

test("companyLabel names three companies then counts the rest", () => {
  assert.equal(companyLabel(["Acme"]), "Acme");
  assert.equal(companyLabel(["Acme", "Globex"]), "Acme and Globex");
  assert.equal(companyLabel(["Acme", "Globex", "Initech"]), "Acme, Globex and Initech");
  assert.equal(companyLabel(["Acme", "Globex", "Initech", "Hooli", "Umbrella"]), "Acme, Globex, Initech and 2 more");
  assert.equal(companyLabel(["A", "B", "C", "D"]), "A, B, C and 1 more");
});

test("visibleSelection drops jobs hidden by search or filter", () => {
  const selected = new Set(["a", "b", "gone"]);
  const pruned = visibleSelection(selected, [job("b"), job("c")]);
  assert.deepEqual([...pruned], ["b"]);
  assert.deepEqual([...selected], ["a", "b", "gone"]);
  assert.equal(visibleSelection(new Set(["a"]), []).size, 0);
});

test("pruneKeys keeps only keys still on screen", () => {
  const selected = new Set(["a", "b", "gone"]);
  assert.deepEqual([...pruneKeys(selected, ["b", "c", "a"])], ["a", "b"]);
  assert.deepEqual([...selected], ["a", "b", "gone"]);
  assert.equal(pruneKeys(selected, []).size, 0);
});

test("toggleKey adds and removes without touching the input", () => {
  const selected = new Set(["a"]);
  assert.deepEqual([...toggleKey(selected, "b")], ["a", "b"]);
  assert.deepEqual([...toggleKey(selected, "a")], []);
  assert.deepEqual([...selected], ["a"]);
});

test("rangeSelection extends either way from the anchor", () => {
  const order = ["a", "b", "c", "d", "e"];
  assert.deepEqual([...rangeSelection(order, new Set(), "b", "d")].sort(), ["b", "c", "d"]);
  assert.deepEqual([...rangeSelection(order, new Set(), "d", "b")].sort(), ["b", "c", "d"]);
  assert.deepEqual([...rangeSelection(order, new Set(), "c", "c")], ["c"]);
});

test("rangeSelection shrinks back to the base", () => {
  const order = ["a", "b", "c", "d", "e"];
  const base = new Set(["e"]);
  const wide = rangeSelection(order, base, "a", "c");
  const narrow = rangeSelection(order, base, "a", "b");
  assert.deepEqual([...wide].sort(), ["a", "b", "c", "e"]);
  assert.deepEqual([...narrow].sort(), ["a", "b", "e"]);
  assert.deepEqual([...base], ["e"]);
});

test("rangeSelection ignores an anchor that left the list", () => {
  assert.deepEqual([...rangeSelection(["a", "b"], new Set(["x"]), "gone", "b")], ["x"]);
  assert.deepEqual([...rangeSelection(["a", "b"], new Set(), "a", "gone")], []);
});

test("selectAllVisible adds every visible key once", () => {
  assert.deepEqual([...selectAllVisible(new Set(["b"]), ["a", "b", "c"])].sort(), ["a", "b", "c"]);
  assert.deepEqual([...selectAllVisible(new Set(), [])], []);
});

test("stepKey clamps at both ends and starts from an edge", () => {
  const order = ["a", "b", "c"];
  assert.equal(stepKey(order, "b", 1), "c");
  assert.equal(stepKey(order, "c", 1), "c");
  assert.equal(stepKey(order, "a", -1), "a");
  assert.equal(stepKey(order, "", 1), "a");
  assert.equal(stepKey(order, "gone", -1), "c");
  assert.equal(stepKey([], "a", 1), "");
});

// board keys come column by column
test("stepKey and rangeSelection cross board columns", () => {
  const order = [...["a1", "a2"], ...["b1"], ...["c1", "c2"]];
  assert.equal(stepKey(order, "a2", 1), "b1");
  assert.equal(stepKey(order, "b1", 1), "c1");
  assert.equal(stepKey(order, "c1", -1), "b1");
  assert.deepEqual([...rangeSelection(order, new Set(), "a2", "c1")].sort(), ["a2", "b1", "c1"]);
});

test("moveGroup keeps the picked jobs in queue order", () => {
  const order = ["a", "b", "c", "d", "e"];
  assert.deepEqual(moveGroup(order, new Set(["d", "b"]), "top"), ["b", "d", "a", "c", "e"]);
  assert.deepEqual(moveGroup(order, new Set(["d", "b"]), "bottom"), ["a", "c", "e", "b", "d"]);
  assert.deepEqual(moveGroup(order, new Set(), "top"), order);
});

test("fitActions folds trailing actions behind More", () => {
  assert.equal(fitActions([50, 50, 50], 150, 40), 3);
  assert.equal(fitActions([50, 50, 50], 149, 40), 2);
  assert.equal(fitActions([50, 50, 50], 120, 40), 1);
  assert.equal(fitActions([50, 50, 50], 60, 40), 0);
  assert.equal(fitActions([50, 50], 108, 40, 4), 2);
  assert.equal(fitActions([50, 50], 103, 40, 4), 1);
  assert.equal(fitActions([], 0, 40), 0);
});

test("restoreSlots keeps a move made after the save", () => {
  // save B, move D to top, undo
  assert.deepEqual(restoreSlots(["A", "B", "C", "D"], ["D", "A", "C"], new Set(["B"])), ["D", "A", "B", "C"]);
});

test("restoreSlots keeps neighbours together and handles the ends", () => {
  const previous = ["A", "B", "C", "D", "E"];
  assert.deepEqual(restoreSlots(previous, ["A", "D", "E"], new Set(["B", "C"])), ["A", "B", "C", "D", "E"]);
  assert.deepEqual(restoreSlots(previous, ["C", "D", "E"], new Set(["A", "B"])), ["A", "B", "C", "D", "E"]);
  assert.deepEqual(restoreSlots(previous, ["A", "B", "C"], new Set(["E"])), ["A", "B", "C", "E"]);
  assert.deepEqual(restoreSlots(previous, [], new Set(["B", "D"])), ["B", "D"]);
});

test("restoreSlots moves a restored job out of the tail", () => {
  // undo lands it at the end first
  assert.deepEqual(restoreSlots(["A", "B", "C"], ["A", "C", "B"], new Set(["B"])), ["A", "B", "C"]);
  assert.deepEqual(restoreSlots(["A", "B", "C"], ["C", "A"], new Set()), ["C", "A"]);
});
