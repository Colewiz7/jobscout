import assert from "node:assert/strict";
import test from "node:test";

import {
  PENDING_READS, POLL_MS, agoLabel, buildView, confirmText, createScanController, exitMeaning, focusTarget, minutesLabel, retryAfterSeconds, safeState, scanMarkup, summarizeRun,
} from "../src/jobscout/static/scan-run.js";

const NOW = Date.parse("2026-11-05T15:00:00Z");
const iso = (minutesAgo) => new Date(NOW - minutesAgo * 60000).toISOString();

const run = (extra) => ({
  id: 1, trigger: "scheduled", state: "ok", started_at: iso(80), finished_at: iso(60),
  exit_code: 0, new_rows: 4, notified: 1, ...extra,
});

const payload = (extra = {}) => ({
  runs: [run()], running: false, status_known: true, cooldown_until: null, next_scheduled_at: null, ...extra,
});

function harness({ enabled = true, responses = [], start = { status: 202 }, open = true } = {}) {
  const timers = [];
  const calls = { runs: 0, start: 0, announced: [], changes: 0, cleared: 0 };
  const queue = [...responses];
  const state = { open, now: NOW };
  const controller = createScanController({
    fetchEnabled: async () => enabled,
    fetchRuns: async (signal) => {
      calls.runs += 1;
      const next = queue.length > 1 ? queue.shift() : queue[0];
      if (next instanceof Error) throw next;
      return typeof next === "function" ? next(signal) : next;
    },
    startRun: async () => {
      calls.start += 1;
      return typeof start === "function" ? start() : start;
    },
    onChange: () => { calls.changes += 1; },
    announce: (text) => calls.announced.push(text),
    isOpen: () => state.open,
    now: () => state.now,
    setTimer: (fn, ms) => {
      const timer = { ms, live: true, fn: () => { timer.live = false; return fn(); } };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => { timer.live = false; calls.cleared += 1; },
  });
  return { controller, timers, calls, state };
}

const live = (timers) => timers.filter((timer) => timer.live);

test("exit codes read in plain words", () => {
  assert.match(exitMeaning(3), /came back empty/);
  assert.match(exitMeaning(5), /notification failed/);
  assert.match(exitMeaning(99), /stopped with an error/);
  assert.equal(exitMeaning(0), "Finished without problems.");
});

test("minutes read naturally", () => {
  assert.equal(minutesLabel(30000), "less than a minute");
  assert.equal(minutesLabel(60000), "1 minute");
  assert.equal(minutesLabel(90 * 60000), "1 hour 30 minutes");
});

test("a good run reports counts and a failed run says why", () => {
  assert.equal(summarizeRun(run(), NOW), "Finished 1 hour ago. 4 new postings and 1 notification.");
  const failed = summarizeRun(run({ state: "failed", exit_code: 5, new_rows: 2, notified: 0 }), NOW);
  assert.match(failed, /^Stopped 1 hour ago\. Sending a notification failed\. \(exit code 5\)/);
  assert.match(failed, /2 new postings and 0 notifications/);
});

test("a run without counts says nothing about them", () => {
  assert.equal(summarizeRun(run({ new_rows: null, notified: null }), NOW), "Finished 1 hour ago.");
});

test("the confirm text warns only when the next scheduled run is close", () => {
  assert.equal(confirmText(null, NOW), "Scan now? Takes about 20 minutes and may send notifications.");
  assert.match(confirmText(new Date(NOW + 12 * 60000).toISOString(), NOW), /next scheduled scan is in 12 minutes, so this may delay or skip it/);
  assert.doesNotMatch(confirmText(new Date(NOW + 3 * 3600000).toISOString(), NOW), /delay/);
  assert.doesNotMatch(confirmText(new Date(NOW - 60000).toISOString(), NOW), /delay/);
});

test("a running scan blocks the button with a reason", () => {
  const model = { enabled: true, data: payload({ running: true, runs: [run({ state: "running", finished_at: null, started_at: iso(7) })] }) };
  const view = buildView(model, NOW);
  assert.equal(view.canStart, false);
  assert.equal(view.disabledReason, "A scan is already running.");
  assert.equal(view.runningLine, "Running for 7 minutes.");
});

test("the fifteen minute limit blocks with the time left", () => {
  const view = buildView({ enabled: true, data: payload({ cooldown_until: new Date(NOW + 9 * 60000).toISOString() }) }, NOW);
  assert.equal(view.canStart, false);
  assert.equal(view.disabledReason, "You can scan again in 9 minutes.");
});

test("an idle scan with no limit can start", () => {
  assert.equal(buildView({ enabled: true, data: payload() }, NOW).canStart, true);
});

test("markup is empty when the feature is off", () => {
  assert.equal(scanMarkup(buildView({ enabled: false, data: null }, NOW)), "");
});

test("markup has no em dashes and keeps the blocked button focusable", () => {
  const view = buildView({ enabled: true, data: payload({ running: true, runs: [run({ state: "running", finished_at: null })] }) }, NOW);
  const html = scanMarkup(view);
  assert.ok(!html.includes("\u2014"));
  assert.match(html, /data-scan-start aria-disabled="true" aria-describedby="scan-reason"/);
  assert.doesNotMatch(html, /data-scan-start[^>]*\sdisabled/);
});

test("markup escapes what the server sends", () => {
  const view = buildView({ enabled: true, data: payload(), error: '<img src=x onerror="1">' }, NOW);
  assert.ok(!scanMarkup(view).includes("<img"));
});

test("confirm markup carries the two-step text and both buttons", () => {
  const html = scanMarkup(buildView({ enabled: true, data: payload(), confirming: true }, NOW));
  assert.match(html, /Scan now\? Takes about 20 minutes and may send notifications\./);
  assert.match(html, /data-scan-confirm/);
  assert.match(html, /data-scan-cancel/);
});

test("a disabled feature never fetches the runs", async () => {
  const { controller, calls } = harness({ enabled: false, responses: [payload()] });
  await controller.open();
  assert.equal(calls.runs, 0);
  assert.equal(controller.view().enabled, false);
});

test("an idle page does not poll", async () => {
  const { controller, timers } = harness({ responses: [payload()] });
  await controller.open();
  assert.equal(timers.length, 0);
});

test("a running scan polls every fifteen seconds and stops when it ends", async () => {
  const running = payload({ running: true, runs: [run({ id: 2, state: "running", finished_at: null })] });
  const done = payload({ runs: [run({ id: 2 })] });
  const { controller, timers, calls } = harness({ responses: [running, running, done] });
  await controller.open();
  assert.equal(live(timers).length, 1);
  assert.equal(timers[0].ms, POLL_MS);
  await timers[0].fn();
  assert.equal(calls.runs, 2);
  assert.equal(live(timers).length, 1);
  await live(timers)[0].fn();
  assert.equal(calls.runs, 3);
  assert.equal(live(timers).length, 0);
  assert.equal(controller.view().running, false);
  assert.equal(calls.announced.filter((text) => text.startsWith("Scan finished")).length, 1);
});

test("leaving the page stops polling without another request", async () => {
  const running = payload({ running: true, runs: [run({ state: "running", finished_at: null })] });
  const { controller, timers, calls, state } = harness({ responses: [running] });
  await controller.open();
  const timer = live(timers)[0];
  state.open = false;
  await timer.fn();
  assert.equal(calls.runs, 1);
  assert.equal(live(timers).filter((t) => t !== timer).length, 0);
});

test("close cancels the pending timer", async () => {
  const running = payload({ running: true, runs: [run({ state: "running", finished_at: null })] });
  const { controller, timers } = harness({ responses: [running] });
  await controller.open();
  controller.close();
  assert.equal(live(timers).length, 0);
});

test("a late reply after close is ignored", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const running = payload({ running: true, runs: [run({ state: "running", finished_at: null })] });
  const { controller, timers, calls } = harness({ responses: [() => gate] });
  const opening = controller.open();
  // wait for the status request to start
  while (calls.runs === 0) await new Promise((resolve) => setImmediate(resolve));
  controller.close();
  const changes = calls.changes;
  release(running);
  await opening;
  assert.equal(live(timers).length, 0);
  assert.equal(calls.changes, changes);
  assert.equal(controller.view().running, false);
});

test("a failed poll keeps the last state and keeps polling", async () => {
  const running = payload({ running: true, runs: [run({ state: "running", finished_at: null })] });
  const { controller, timers, calls } = harness({ responses: [running, new Error("offline")] });
  await controller.open();
  await live(timers)[0].fn();
  const view = controller.view();
  assert.equal(view.running, true);
  assert.equal(view.error, "Couldn't check the scan just now.");
  assert.equal(calls.runs, 2);
  assert.equal(live(timers).length, 1);
});

test("a failed first load offers a retry instead of polling", async () => {
  const { controller, timers } = harness({ responses: [new Error("offline"), payload()] });
  await controller.open();
  assert.equal(controller.view().showRetry, true);
  assert.equal(timers.length, 0);
  await controller.retry();
  assert.equal(controller.view().error, "");
});

test("starting asks first and only a confirm sends the request", async () => {
  const { controller, calls } = harness({ responses: [payload()] });
  await controller.open();
  controller.askConfirm();
  assert.equal(controller.view().confirming, true);
  assert.equal(controller.consumeFocus(), "confirm");
  assert.equal(calls.start, 0);
  controller.cancel();
  assert.equal(controller.view().confirming, false);
  assert.equal(controller.consumeFocus(), "start");
  assert.equal(calls.start, 0);
});

test("a blocked button cannot open the confirm", async () => {
  const { controller } = harness({ responses: [payload({ running: true, runs: [run({ state: "running", finished_at: null })] })] });
  await controller.open();
  controller.askConfirm();
  assert.equal(controller.view().confirming, false);
});

test("a confirmed start shows running at once and polls", async () => {
  const running = payload({ running: true, runs: [run({ id: 3, state: "running", finished_at: null, trigger: "manual" })] });
  const { controller, calls, timers } = harness({ responses: [payload(), running], start: { status: 202, runId: 3 } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(calls.start, 1);
  assert.equal(controller.view().running, true);
  assert.equal(live(timers).length, 1);
  assert.ok(calls.announced.some((text) => text.startsWith("Scan started")));
});

test("a double confirm sends one request", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { controller, calls } = harness({ responses: [payload()], start: () => gate });
  await controller.open();
  controller.askConfirm();
  const first = controller.confirm();
  const second = controller.confirm();
  release({ status: 202 });
  await Promise.all([first, second]);
  assert.equal(calls.start, 1);
});

test("a 409 says a scan is running and refreshes", async () => {
  const { controller, calls } = harness({ responses: [payload()], start: { status: 409 } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().message, "A scan is already running.");
  assert.equal(calls.runs, 2);
});

test("a 429 reports how long to wait", async () => {
  const { controller } = harness({ responses: [payload()], start: { status: 429, retryAfter: "420" } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().message, "A scan started recently. Try again in 7 minutes.");
});

test("other failures stay generic and keep the page state", async () => {
  for (const status of [0, 403, 500, 503]) {
    const { controller } = harness({ responses: [payload()], start: { status } });
    await controller.open();
    controller.askConfirm();
    await controller.confirm();
    const view = controller.view();
    assert.ok(view.message.length > 0);
    assert.doesNotMatch(view.message, /kube|token|\d+\.\d+\.\d+/i);
    assert.equal(view.lastState, "ok");
    assert.equal(view.confirming, false);
  }
});

test("a thrown start is handled like a failure", async () => {
  const { controller } = harness({ responses: [payload()], start: () => { throw new Error("boom"); } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().message, "Couldn't start a scan. Try again shortly.");
});

test("a hostile state never reaches an attribute", () => {
  const hostile = 'failed" onmouseover="alert(1)';
  const data = payload({ runs: [run({ state: hostile, exit_code: '5"><script>', new_rows: '<b>9</b>', notified: "x", finished_at: '"><img src=x>', trigger: '<i>' })] });
  const html = scanMarkup(buildView({ enabled: true, data, error: '"><svg onload=1>', message: "<u>" }, NOW));
  assert.equal(safeState(hostile), "unknown");
  assert.match(html, /data-scan-state="unknown"/);
  for (const bad of ["onmouseover", "<script", "<img", "<svg", "<b>", "<i>", "<u>"]) assert.ok(!html.includes(bad), bad);
  assert.equal([...html.matchAll(/data-scan-state=/g)].length, 1);
});

test("known states stay as they are", () => {
  for (const state of ["running", "ok", "failed"]) assert.equal(safeState(state), state);
  assert.equal(safeState(undefined), "unknown");
});

test("a stale read that finishes after the start cannot undo it", async () => {
  let release;
  const stale = new Promise((resolve) => { release = resolve; });
  const idle = payload();
  const running = payload({ running: true, runs: [run({ id: 9, state: "running", finished_at: null, trigger: "manual" })] });
  const { controller, timers, calls } = harness({ responses: [idle, () => stale, running], start: { status: 202, runId: 9 } });
  await controller.open();
  controller.askConfirm();
  const slow = controller.retry();
  while (calls.runs < 2) await new Promise((resolve) => setImmediate(resolve));
  await controller.confirm();
  assert.equal(controller.view().running, true);
  release(idle);
  await slow;
  assert.equal(controller.view().running, true);
  assert.equal(live(timers).length, 1);
});

test("a read that does not list our run yet keeps the running state and polling", async () => {
  const { controller, timers } = harness({ responses: [payload(), payload()], start: { status: 202, runId: 9 } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().running, true);
  assert.equal(live(timers).length, 1);
});

test("a read that fails right after the start still leaves a poll timer", async () => {
  const { controller, timers } = harness({ responses: [payload(), new Error("offline")] });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().running, true);
  assert.equal(live(timers).length, 1);
});

test("a run that failed before the 202 arrived shows as failed at once", async () => {
  const failed = payload({ runs: [run({ id: 9, trigger: "manual", state: "failed", exit_code: 3, started_at: iso(1), finished_at: iso(0) })] });
  const { controller, timers, announced } = (() => {
    const made = harness({ responses: [payload(), failed], start: { status: 202, runId: 9 } });
    return { ...made, announced: made.calls.announced };
  })();
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  const view = controller.view();
  assert.equal(view.running, false);
  assert.equal(view.lastState, "failed");
  assert.match(view.lastLine, /exit code 3/);
  assert.equal(live(timers).length, 0);
  assert.ok(announced.some((text) => text.startsWith("Scan started")));
});

test("a listed run that is still open keeps polling, and the id match is by id not time", async () => {
  const open = payload({ running: true, runs: [run({ id: 9, trigger: "manual", state: "running", finished_at: null })] });
  const { controller, timers, state } = harness({ responses: [payload(), open, open], start: { status: 202, runId: 9 } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  // a skewed client clock changes nothing
  state.now += 3 * 3600000;
  assert.equal(controller.view().running, true);
  assert.equal(live(timers).length, 1);
});

test("an old run with the same time but another id does not end ours", async () => {
  const older = payload({ runs: [run({ id: 4, finished_at: iso(0) })] });
  const { controller, timers } = harness({ responses: [payload(), older], start: { status: 202, runId: 9 } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().running, true);
  assert.equal(live(timers).length, 1);
});

test("a late feature answer from an earlier visit cannot hide the section", async () => {
  const gates = [];
  const timers = [];
  const calls = { runs: 0 };
  const controller = createScanController({
    fetchEnabled: () => new Promise((resolve) => gates.push(resolve)),
    fetchRuns: async () => { calls.runs += 1; return payload({ running: true, runs: [run({ state: "running", finished_at: null })] }); },
    onChange: () => {},
    now: () => NOW,
    setTimer: (fn, ms) => { const timer = { fn, ms, live: true }; timers.push(timer); return timer; },
    clearTimer: (timer) => { timer.live = false; },
  });
  const first = controller.open();
  controller.close();
  const second = controller.open();
  gates[1](true);
  await second;
  gates[0](false);
  await first;
  assert.equal(controller.view().enabled, true);
  assert.equal(controller.view().running, true);
  assert.equal(timers.filter((timer) => timer.live).length, 1);
});

test("a confirm that is open closes when the scan turns out to be running", async () => {
  const running = payload({ running: true, runs: [run({ state: "running", finished_at: null })] });
  const { controller } = harness({ responses: [payload(), running] });
  await controller.open();
  controller.askConfirm();
  assert.equal(controller.view().confirming, true);
  await controller.retry();
  const view = controller.view();
  assert.equal(view.confirming, false);
  assert.equal(view.disabledReason, "A scan is already running.");
  assert.equal(controller.consumeFocus(), "start");
});

test("a skipped run reads as skipped, not as a success", () => {
  const skipped = run({ state: "skipped", new_rows: null, notified: null });
  assert.equal(summarizeRun(skipped, NOW), "Skipped 1 hour ago. Another scan was already running.");
  assert.match(scanMarkup(buildView({ enabled: true, data: payload({ runs: [skipped] }) }, NOW)), /data-scan-state="skipped"/);
});

test("retry after reads seconds, dates and the body", () => {
  assert.equal(retryAfterSeconds("420", undefined, NOW), 420);
  assert.equal(retryAfterSeconds(new Date(NOW + 90000).toUTCString(), undefined, NOW), 90);
  assert.equal(retryAfterSeconds(null, 300, NOW), 300);
  assert.equal(retryAfterSeconds("soon", "nope", NOW), 0);
});

test("a 429 with only a date header still reports the wait", async () => {
  const header = new Date(NOW + 5 * 60000).toUTCString();
  const { controller } = harness({ responses: [payload()], start: { status: 429, retryAfter: header } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().message, "A scan started recently. Try again in 5 minutes.");
});

test("a 429 with only the body field reports the wait", async () => {
  const { controller } = harness({ responses: [payload()], start: { status: 429, retryAfter: null, retryAfterBody: 120 } });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.equal(controller.view().message, "A scan started recently. Try again in 2 minutes.");
});

test("bad dates leave no dangling words", () => {
  assert.equal(agoLabel("not a date", NOW), "");
  assert.equal(summarizeRun(run({ finished_at: "garbage", new_rows: null, notified: null }), NOW), "Finished.");
  assert.match(summarizeRun(run({ state: "failed", exit_code: 3, finished_at: null, new_rows: null, notified: null }), NOW), /^Stopped\. A job source/);
});

test("a start in the future reads as just started", () => {
  const data = payload({ running: true, runs: [run({ state: "running", finished_at: null, started_at: new Date(NOW + 5 * 60000).toISOString() })] });
  assert.equal(buildView({ enabled: true, data }, NOW).runningLine, "Started just now.");
  const fresh = payload({ running: true, runs: [run({ state: "running", finished_at: null, started_at: iso(0) })] });
  assert.equal(buildView({ enabled: true, data: fresh }, NOW).runningLine, "Started just now.");
});

test("focus falls back from a disabled confirm to the scan button, then the heading", () => {
  const present = new Set(["[data-scan-start]", "#scan-title"]);
  assert.equal(focusTarget("[data-scan-confirm]", (selector) => present.has(selector)), "[data-scan-start]");
  present.delete("[data-scan-start]");
  assert.equal(focusTarget("[data-scan-confirm]", (selector) => present.has(selector)), "#scan-title");
  assert.equal(focusTarget("[data-scan-start]", () => true), "[data-scan-start]");
  assert.equal(focusTarget("[data-scan-start]", () => false), null);
});

async function startWith(runId, responses) {
  const made = harness({ responses, start: { status: 202, runId } });
  await made.controller.open();
  made.controller.askConfirm();
  await made.controller.confirm();
  return made;
}

async function tickUntilIdle(made) {
  let ticks = 0;
  while (live(made.timers).length && ticks < 20) {
    await live(made.timers)[0].fn();
    ticks += 1;
  }
  return ticks;
}

test("a run id that never shows up stops being believed after a few idle reads", async () => {
  const made = await startWith(9, [payload(), payload()]);
  assert.equal(made.controller.view().running, true);
  const ticks = await tickUntilIdle(made);
  assert.equal(ticks, PENDING_READS - 1);
  assert.equal(made.controller.view().running, false);
  assert.equal(live(made.timers).length, 0);
});

test("the bound counts reads, not the clock", async () => {
  const made = await startWith(9, [payload(), payload()]);
  made.state.now += 5 * 3600000;
  assert.equal(made.controller.view().running, true);
  assert.equal(live(made.timers).length, 1);
  // only reads use it up
  const ticks = await tickUntilIdle(made);
  assert.equal(ticks, PENDING_READS - 1);
  assert.equal(made.controller.view().running, false);
});

test("pending is believed for at most the documented number of reads", async () => {
  const made = await startWith(9, [payload(), payload()]);
  let believed = 1;
  while (live(made.timers).length) {
    await live(made.timers)[0].fn();
    if (made.controller.view().running) believed += 1;
  }
  assert.equal(believed, PENDING_READS - 1);
});

test("an old finished row is not announced when our run never appears", async () => {
  const made = await startWith(9, [payload(), payload()]);
  await tickUntilIdle(made);
  assert.equal(made.controller.view().running, false);
  assert.deepEqual(made.calls.announced.filter((text) => /finished|stopped/i.test(text)), []);
});

test("a finish is announced for the run we started", async () => {
  const done = payload({ runs: [run({ id: 9, trigger: "manual", state: "ok" })] });
  const made = await startWith(9, [payload(), done]);
  assert.equal(made.calls.announced.filter((text) => text.startsWith("Scan finished")).length, 1);
});

test("a finish is announced for a row we watched running, and only once", async () => {
  const running = payload({ running: true, runs: [run({ id: 5, state: "running", finished_at: null })] });
  const done = payload({ runs: [run({ id: 5 })] });
  const { controller, timers, calls } = harness({ responses: [running, done, done] });
  await controller.open();
  await live(timers)[0].fn();
  await controller.retry();
  assert.equal(calls.announced.filter((text) => text.startsWith("Scan finished")).length, 1);
});

for (const bad of [undefined, null, "7", 0, -1, 1.5, NaN]) {
  test(`a bad run id (${String(bad)}) keeps running for the bound and then ends`, async () => {
    const made = await startWith(bad, [payload(), payload()]);
    assert.equal(made.controller.view().running, true);
    assert.equal(live(made.timers).length, 1);
    await tickUntilIdle(made);
    assert.equal(made.controller.view().running, false);
    assert.equal(live(made.timers).length, 0);
  });
}

test("an open row means running even when the top-level flag lags", async () => {
  const lagging = payload({ running: false, runs: [run({ id: 9, trigger: "manual", state: "running", finished_at: null })] });
  const done = payload({ runs: [run({ id: 9, trigger: "manual", state: "ok" })] });
  const made = await startWith(9, [payload(), lagging, lagging, done]);
  assert.equal(made.controller.view().running, true);
  assert.equal(live(made.timers).length, 1);
  await live(made.timers)[0].fn();
  assert.equal(made.controller.view().running, true);
  assert.equal(live(made.timers).length, 1);
  await live(made.timers)[0].fn();
  assert.equal(made.controller.view().running, false);
  assert.equal(live(made.timers).length, 0);
});

test("a skipped run shows the server note as escaped text", () => {
  const skipped = run({ state: "skipped", new_rows: null, notified: null, note: "Another scan held the lock <b>x</b>" });
  assert.equal(summarizeRun(skipped, NOW), "Skipped 1 hour ago. Another scan held the lock <b>x</b>");
  const html = scanMarkup(buildView({ enabled: true, data: payload({ runs: [skipped] }) }, NOW));
  assert.ok(!html.includes("<b>x"));
  assert.match(html, /&lt;b&gt;x/);
});

const finishedRow = (id, extra = {}) => run({ id, state: "ok", ...extra });
const runningRow = (id) => run({ id, state: "running", finished_at: null });
const said = (calls) => calls.announced.filter((text) => !text.startsWith("Scan started"));

test("running with no row yet, then the finished row, announces once", async () => {
  const { controller, timers, calls } = harness({ responses: [
    payload({ running: true, runs: [finishedRow(1)] }),
    payload({ runs: [finishedRow(2), finishedRow(1)] }),
  ] });
  await controller.open();
  await live(timers)[0].fn();
  await controller.retry();
  assert.equal(said(calls).length, 1);
  assert.match(said(calls)[0], /^Scan finished\./);
});

test("two completions in a row are each announced exactly once", async () => {
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [runningRow(2), finishedRow(1)] }),
    payload({ runs: [finishedRow(2), finishedRow(1)] }),
    payload({ runs: [finishedRow(2), finishedRow(1)] }),
    payload({ running: true, runs: [runningRow(3), finishedRow(2), finishedRow(1)] }),
    payload({ runs: [finishedRow(3, { state: "failed", exit_code: 5 }), finishedRow(2), finishedRow(1)] }),
    payload({ runs: [finishedRow(3, { state: "failed", exit_code: 5 }), finishedRow(2), finishedRow(1)] }),
  ] });
  await controller.open();
  for (let step = 0; step < 5; step += 1) await controller.retry();
  const lines = said(calls);
  assert.equal(lines.length, 2);
  assert.match(lines[0], /^Scan finished\./);
  assert.match(lines[1], /^Scan stopped\./);
});

test("finished rows from before any activity are never announced", async () => {
  const idle = payload({ runs: [finishedRow(3), finishedRow(2), finishedRow(1)] });
  const { controller, calls } = harness({ responses: [idle, idle] });
  await controller.open();
  await controller.retry();
  assert.deepEqual(said(calls), []);
});

test("a run that finished in another tab while this page showed running is announced", async () => {
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [runningRow(5), finishedRow(4)] }),
    payload({ runs: [finishedRow(5), finishedRow(4)] }),
  ] });
  await controller.open();
  await controller.retry();
  assert.equal(said(calls).length, 1);
});

test("leaving and coming back announces nothing for a finish that happened while away", async () => {
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [runningRow(5)] }),
    payload({ runs: [finishedRow(5)] }),
  ] });
  await controller.open();
  controller.close();
  await controller.open();
  assert.deepEqual(said(calls), []);
});

test("an empty history gives a baseline of zero", async () => {
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [] }),
    payload({ runs: [finishedRow(1)] }),
  ] });
  await controller.open();
  await controller.retry();
  assert.equal(said(calls).length, 1);
});

test("many completions never grow the memory past its cap", async () => {
  const responses = [];
  for (let id = 1; id <= 25; id += 1) {
    responses.push(payload({ running: true, runs: [runningRow(id)] }));
    responses.push(payload({ runs: [finishedRow(id)] }));
  }
  const { controller, calls } = harness({ responses });
  await controller.open();
  for (let step = 0; step < 49; step += 1) await controller.retry();
  assert.equal(said(calls).length, 25);
  assert.ok(controller.announcedSize() <= 20);
});

test("a skipped run announces the skipped text, not a finish", async () => {
  const skipped = finishedRow(2, { state: "skipped", note: "Another scan held the lock, so this one did not run.", new_rows: null, notified: null });
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [runningRow(2), finishedRow(1)] }),
    payload({ runs: [skipped, finishedRow(1)] }),
  ] });
  await controller.open();
  await controller.retry();
  const lines = said(calls);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^Skipped /);
  assert.ok(!/finished/i.test(lines[0]));
});

test("the newest finished row is the one spoken when several land at once", async () => {
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [runningRow(3), finishedRow(1)] }),
    payload({ runs: [finishedRow(3, { state: "failed", exit_code: 5 }), finishedRow(2), finishedRow(1)] }),
    payload({ runs: [finishedRow(3, { state: "failed", exit_code: 5 }), finishedRow(2), finishedRow(1)] }),
  ] });
  await controller.open();
  await controller.retry();
  await controller.retry();
  assert.equal(said(calls).length, 1);
  assert.match(said(calls)[0], /^Scan stopped\./);
});

test("a newer skipped run in the list does not hide our running run's finish", async () => {
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [finishedRow(6, { state: "skipped", note: "x" }), runningRow(5), finishedRow(4)] }),
    payload({ runs: [finishedRow(6, { state: "skipped", note: "x" }), finishedRow(5), finishedRow(4)] }),
    payload({ runs: [finishedRow(6, { state: "skipped", note: "x" }), finishedRow(5), finishedRow(4)] }),
  ] });
  await controller.open();
  await controller.retry();
  await controller.retry();
  const lines = said(calls);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^Scan finished\./);
});

test("after a failed first read an old finished row is not announced, our run is", async () => {
  const old = [finishedRow(3), finishedRow(2), finishedRow(1)];
  const { controller, calls } = harness({
    responses: [new Error("offline"), payload({ runs: old }), payload({ runs: [finishedRow(9), ...old] })],
    start: { status: 202, runId: 9 },
  });
  await controller.open();
  controller.askConfirm();
  await controller.confirm();
  assert.deepEqual(said(calls), []);
  await controller.retry();
  assert.equal(said(calls).length, 1);
  await controller.retry();
  assert.equal(said(calls).length, 1);
});

test("running with no row, then a row already known, announces nothing", async () => {
  const rows = [finishedRow(3), finishedRow(2), finishedRow(1)];
  const { controller, calls } = harness({ responses: [payload({ runs: rows }), payload({ running: true, runs: rows }), payload({ runs: rows })] });
  await controller.open();
  await controller.retry();
  await controller.retry();
  assert.deepEqual(said(calls), []);
});

test("running with no row on the very first read never guesses", async () => {
  const { controller, calls } = harness({ responses: [
    payload({ running: true, runs: [finishedRow(2)] }),
    payload({ runs: [finishedRow(2)] }),
  ] });
  await controller.open();
  await controller.retry();
  assert.deepEqual(said(calls), []);
});

test("after a failed first read a running flag with old rows never guesses", async () => {
  const { controller, calls } = harness({ responses: [
    new Error("offline"),
    payload({ running: true, runs: [finishedRow(2), finishedRow(1)] }),
    payload({ runs: [finishedRow(2), finishedRow(1)] }),
  ] });
  await controller.open();
  await controller.retry();
  await controller.retry();
  assert.deepEqual(said(calls), []);
});
