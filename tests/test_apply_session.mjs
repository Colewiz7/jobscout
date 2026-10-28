import assert from "node:assert/strict";
import test from "node:test";

import { recordedAs, routeIndex, sessionFromServer, sessionLists, sessionPatch, stepAfter, withRecord } from "../src/jobscout/static/apply-session.js";

const row = (extra = {}) => ({ id: 4, revision: 2, keys: ["a", "b", "c"], applied: ["a"], skipped: [{ dedupe_key: "b", reason: "Skipped in session" }], ended_at: null, elapsed_seconds: 90, ...extra });

test("sessionFromServer runs the clock from elapsed, not server time", () => {
  const session = sessionFromServer(row(), 100_000);
  assert.equal(session.startedAt, 10_000);
  assert.equal(session.endedAt, null);
  const ended = sessionFromServer(row({ ended_at: "2026-10-27T12:00:00Z" }), 100_000);
  assert.equal(ended.endedAt - ended.startedAt, 90_000);
});

test("sessionFromServer copies so undo snapshots stay put", () => {
  const source = row();
  const session = sessionFromServer(source);
  session.applied.push("c");
  session.skipped[0].reason = "changed";
  assert.deepEqual(source.applied, ["a"]);
  assert.equal(source.skipped[0].reason, "Skipped in session");
  const lists = sessionLists(session);
  session.applied.pop();
  assert.deepEqual(lists.applied, ["a", "c"]);
});

test("withRecord keeps a job in one list", () => {
  const session = sessionFromServer(row());
  const applied = withRecord(sessionLists(session), "b", "applied");
  assert.deepEqual(applied.applied, ["a", "b"]);
  assert.deepEqual(applied.skipped, []);
  const skipped = withRecord(applied, "a", "skipped", "Posting closed");
  assert.deepEqual(skipped.applied, ["b"]);
  assert.deepEqual(skipped.skipped, [{ dedupe_key: "a", reason: "Posting closed" }]);
  assert.equal(recordedAs({ ...session, ...skipped }, "a"), "skipped");
  assert.equal(recordedAs({ ...session, ...skipped }, "b"), "applied");
  assert.equal(recordedAs({ ...session, ...skipped }, "c"), "");
});

test("stepAfter ends on the last job only", () => {
  const session = sessionFromServer(row());
  assert.deepEqual(stepAfter(session, 0), { index: 1, ended: false });
  assert.deepEqual(stepAfter(session, 2), { index: 3, ended: true });
});

test("sessionPatch sends whole lists and the end flag", () => {
  const lists = { applied: ["a"], skipped: [], ended: true, extra: 1 };
  assert.deepEqual(sessionPatch(lists), { applied: ["a"], skipped: [], ended: true });
});

test("routeIndex falls back to the first job", () => {
  const session = sessionFromServer(row());
  assert.equal(routeIndex(session, "c"), 2);
  assert.equal(routeIndex(session, "zz"), 0);
});
