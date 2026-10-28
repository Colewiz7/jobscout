import assert from "node:assert/strict";
import test from "node:test";

import { Countdown, EditSessions, SNACKBAR, UndoStack, closedUndoIds, exitDelay, mergeSnackbar, snackbarDuration, textChange, withdrawSnackbar } from "../src/jobscout/static/undo-stack.js";

const labels = (taken) => taken && [taken.entry.label, ...taken.dropped.map((entry) => entry.label)];

test("UndoStack pops newest first", () => {
  const stack = new UndoStack();
  stack.push({ label: "a" }); stack.push({ label: "b" }); stack.push({ label: "c" });
  assert.deepEqual([stack.pop().entry.label, stack.pop().entry.label, stack.pop().entry.label], ["c", "b", "a"]);
  assert.equal(stack.pop(), null);
});

test("UndoStack keeps older entries when a new one lands", () => {
  const stack = new UndoStack();
  const first = stack.push({ label: "a", keys: ["job1"] });
  stack.push({ label: "b", keys: ["job1"] });
  assert.ok(stack.has(first));
  assert.equal(stack.size, 2);
});

test("UndoStack drops the oldest past the cap", () => {
  const stack = new UndoStack(3);
  const ids = ["a", "b", "c", "d"].map((label) => stack.push({ label }));
  assert.equal(stack.size, 3);
  assert.ok(!stack.has(ids[0]));
  assert.equal(stack.pop().entry.label, "d");
});

test("UndoStack take removes one entry and only once", () => {
  const stack = new UndoStack();
  const a = stack.push({ label: "a" });
  stack.push({ label: "b" });
  assert.deepEqual(labels(stack.take(a)), ["a"]);
  assert.equal(stack.take(a), null);
  assert.equal(stack.pop().entry.label, "b");
});

test("UndoStack ids stay unique after pops", () => {
  const stack = new UndoStack();
  const a = stack.push({});
  stack.pop();
  assert.notEqual(stack.push({}), a);
});

test("undoing an older entry drops newer ones on the same job", () => {
  const stack = new UndoStack();
  stack.push({ label: "saved", keys: ["job1"] });
  const queued = stack.push({ label: "queued", keys: ["job1"] });
  stack.push({ label: "archived", keys: ["job1"] });
  assert.deepEqual(labels(stack.take(queued)), ["queued", "archived"]);
  assert.equal(stack.size, 1);
  assert.equal(stack.pop().entry.label, "saved");
});

test("an older field edit waits for the newer one on the same record", () => {
  const stack = new UndoStack();
  const first = stack.push({ label: "A to B", keys: ["interview:1"], latest: "interview:1" });
  const second = stack.push({ label: "B to C", keys: ["interview:1"], latest: "interview:1" });
  const blocked = stack.take(first);
  assert.equal(blocked.blocked, true);
  assert.deepEqual(blocked.dropped, []);
  assert.ok(stack.has(first) && stack.has(second));
  assert.deepEqual(labels(stack.take(second)), ["B to C"]);
  assert.deepEqual(labels(stack.take(first)), ["A to B"]);
});

test("a newer delete also holds back an older field edit", () => {
  const stack = new UndoStack();
  const edit = stack.push({ label: "edit", keys: ["contact:4"], latest: "contact:4" });
  stack.push({ label: "delete", keys: ["contact:4"] });
  assert.equal(stack.take(edit).blocked, true);
  assert.equal(stack.pop().entry.label, "delete");
});

test("a field edit only waits on its own scope", () => {
  const stack = new UndoStack();
  const edit = stack.push({ label: "edit", keys: ["job1", "posting:job1"], latest: "posting:job1" });
  stack.push({ label: "saved", keys: ["job1"] });
  stack.push({ label: "other", keys: ["posting:job2"], latest: "posting:job2" });
  assert.deepEqual(labels(stack.take(edit)), ["edit", "saved"]);
  assert.equal(stack.size, 1);
});

test("dropping only touches overlapping newer entries", () => {
  const stack = new UndoStack();
  const older = stack.push({ label: "older", keys: ["job2"] });
  const bulk = stack.push({ label: "bulk", keys: ["job1", "job2"] });
  stack.push({ label: "other", keys: ["job3"] });
  stack.push({ label: "later", keys: ["job1"] });
  stack.push({ label: "view", keys: ["view:Remote"] });
  assert.deepEqual(labels(stack.take(bulk)), ["bulk", "later"]);
  assert.ok(stack.has(older));
  assert.deepEqual(stack.entries.map((entry) => entry.label), ["older", "other", "view"]);
});

test("entries without keys never drop others", () => {
  const stack = new UndoStack();
  const a = stack.push({ label: "a" });
  stack.push({ label: "b" });
  assert.deepEqual(labels(stack.take(a)), ["a"]);
  assert.equal(stack.size, 1);
});

test("snackbarDuration follows the carrel timings", () => {
  assert.equal(snackbarDuration("Saved.", false), 4000);
  assert.equal(snackbarDuration("x".repeat(61), false), 6000);
  assert.equal(snackbarDuration("Saved.", true), 10000);
  assert.equal(SNACKBAR.action, 10000);
});

const timing = (overrides) => ({ shownAt: 0, deadline: 10000, queued: false, held: false, ...overrides });

test("exitDelay runs the full clock with nothing queued", () => {
  assert.equal(exitDelay(timing(), 1000), 9000);
  assert.equal(exitDelay(timing(), 12000), 0);
});

test("exitDelay lets a queued newcomer replace after 2s shown", () => {
  assert.equal(exitDelay(timing({ queued: true }), 500), 1500);
  assert.equal(exitDelay(timing({ queued: true }), 3000), 0);
});

test("removing the queued entry gives back the full remaining time", () => {
  const visible = timing({ queued: true });
  assert.equal(exitDelay(visible, 500), 1500);
  assert.equal(exitDelay({ ...visible, queued: false }, 500), 9500);
  assert.equal(exitDelay({ ...visible, queued: false }, 1900), 8100);
});

test("exitDelay never cuts a resumed snackbar under 2s", () => {
  const clock = new Countdown(10000);
  clock.start(0);
  clock.hold("hover", 1000);
  assert.equal(exitDelay({ shownAt: 0, resumedAt: clock.resumedAt, deadline: clock.deadline, queued: true, held: true }, 20000), -1);
  clock.release("hover", 30000);
  assert.equal(clock.resumedAt, 30000);
  assert.equal(exitDelay({ shownAt: 0, resumedAt: clock.resumedAt, deadline: clock.deadline, queued: true, held: false }, 30000), 2000);
  assert.equal(exitDelay({ shownAt: 0, resumedAt: clock.resumedAt, deadline: clock.deadline, queued: false, held: false }, 30000), 9000);
});

test("exitDelay keeps the natural end when it comes first", () => {
  assert.equal(exitDelay(timing({ queued: true, deadline: 1200 }), 1000), 200);
});

test("Countdown pauses and resumes with the time left", () => {
  const clock = new Countdown(10000);
  assert.equal(clock.start(0), 10000);
  assert.ok(clock.hold("hidden", 3000));
  assert.equal(clock.remaining, 7000);
  assert.equal(clock.release("hidden", 60000), 7000);
  assert.equal(clock.deadline, 67000);
});

test("Countdown never resumes under 2s", () => {
  const clock = new Countdown(10000);
  clock.start(0);
  clock.hold("hover", 9500);
  assert.equal(clock.release("hover", 20000), 2000);
});

test("Countdown waits for every hold to clear", () => {
  const clock = new Countdown(10000);
  clock.start(0);
  clock.hold("hover", 1000);
  assert.equal(clock.hold("hidden", 2000), false);
  assert.equal(clock.release("hover", 3000), -1);
  assert.equal(clock.running, false);
  assert.equal(clock.release("hidden", 4000), 9000);
});

test("Countdown started while held waits for release", () => {
  const clock = new Countdown(10000);
  clock.hold("hidden", 0);
  assert.equal(clock.start(0), -1);
  assert.equal(clock.release("hidden", 5000), 10000);
});

test("Countdown ignores a release it never held", () => {
  const clock = new Countdown(10000);
  clock.start(0);
  assert.equal(clock.release("focus", 100), -1);
  assert.equal(clock.deadline, 10000);
});

test("remove drops one entry and leaves later ones alone", () => {
  const stack = new UndoStack();
  const first = stack.push({ keys: ["a"] });
  const second = stack.push({ keys: ["a"] });
  assert.equal(stack.remove(first), true);
  assert.equal(stack.remove(first), false);
  assert.equal(stack.has(second), true);
  assert.equal(stack.size, 1);
});

const move = (undoId) => ({ message: `Moved ${undoId}.`, action: "Undo", onAction: () => undoId, undoId });

test("withdrawing the shown move falls back to the newest undoable one", () => {
  let item = mergeSnackbar({}, move(1));
  item = { ...item, ...mergeSnackbar(item, move(2)) };
  item = { ...item, ...mergeSnackbar(item, move(3)) };
  const live = new Set([1, 3]);
  const patch = withdrawSnackbar(item, 3, (id) => live.has(id));
  assert.equal(patch.undoId, 1);
  assert.equal(patch.message, "Moved 1.");
  assert.equal(patch.action, "Undo");
  assert.equal(withdrawSnackbar(item, 2, () => true), null); // not the one shown
});

test("withdrawing the only move drops the action", () => {
  const item = mergeSnackbar({}, move(1));
  assert.deepEqual(withdrawSnackbar(item, 1, () => false), { action: "", onAction: null, undoId: 0 });
});

test("a merge after a withdrawal brings the action back", () => {
  let item = mergeSnackbar({}, move(1));
  item = { ...item, ...withdrawSnackbar(item, 1, () => false) };
  assert.equal(item.action, "");
  item = { ...item, ...mergeSnackbar(item, move(2)) };
  assert.equal(item.action, "Undo");
  assert.equal(item.undoId, 2);
  assert.equal(item.onAction(), 2);
  assert.deepEqual(item.history.map((step) => step.undoId), [1, 2]);
});

test("EditSessions keeps the value typing started from", () => {
  const sessions = new EditSessions();
  sessions.begin("notes:a", "first");
  sessions.begin("notes:a", "first and more");
  assert.ok(sessions.has("notes:a"));
  assert.equal(sessions.end("notes:a"), "first");
  assert.ok(!sessions.has("notes:a"));
  assert.equal(sessions.end("notes:a"), undefined);
});

test("EditSessions scopes each record apart", () => {
  const sessions = new EditSessions();
  sessions.begin("notes:a", "a");
  sessions.begin("notes:b", "b");
  assert.equal(sessions.end("notes:b"), "b");
  assert.equal(sessions.end("notes:a"), "a");
});

test("textChange skips edits that end where they began", () => {
  assert.equal(textChange("same", "same"), null);
  assert.equal(textChange(undefined, "typed"), null);
  assert.deepEqual(textChange("old", ""), { before: "old", after: "" });
  assert.equal(textChange(["a", "b"], ["a", "b"]), null);
  assert.deepEqual(textChange(["a"], ["a", "b"]), { before: ["a"], after: ["a", "b"] });
});

test("closedUndoIds names only undos that end with the bar", () => {
  const closing = new Set([3, 5]);
  const item = { undoId: 5, history: [{ undoId: 2 }, { undoId: 3 }, { undoId: 5 }] };
  assert.deepEqual(closedUndoIds(item, closing), [5, 3]);
  assert.deepEqual(closedUndoIds({ undoId: 0, history: [] }, closing), []);
});

test("a hide entry leaves the stack once its bar closes", () => {
  const stack = new UndoStack();
  const closing = new Set();
  const kept = stack.push({ label: "notes" });
  const hide = stack.push({ label: "hide" });
  closing.add(hide);
  for (const id of closedUndoIds({ undoId: hide, history: [{ undoId: hide }] }, closing)) { stack.remove(id); closing.delete(id); }
  assert.ok(!stack.has(hide));
  assert.ok(stack.has(kept));
  assert.equal(stack.pop().entry.label, "notes");
});
