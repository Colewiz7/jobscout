import assert from "node:assert/strict";
import test from "node:test";

import { keepsNewer, recordedAs, restoreUnsaved, retried, routeIndex, saveStep, sessionFromServer, sessionLists, sessionPatch, staleRead, stepAfter, stepUndo, undoStep, undoUnsaved, withRecord } from "../src/jobscout/static/apply-session.js";

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

const conflict = () => Object.assign(new Error("The session changed in another tab."), { conflict: true });

// tab b still shows a job tab a applied
test("a stale tab's skip never replaces an applied step", async () => {
  const stale = sessionFromServer(row({ applied: [], skipped: [] }));
  const newer = sessionFromServer(row({ revision: 3, applied: ["a"], skipped: [] }));
  const writes = [];
  const outcome = await saveStep({
    session: stale, key: "a", kind: "skipped",
    change: (lists) => withRecord(lists, "a", "skipped", "Skipped in session"),
    write: async (session, lists) => { writes.push([session.revision, lists]); throw conflict(); },
    latest: () => newer,
  });
  assert.equal(outcome.saved, false);
  assert.equal(outcome.kept, "applied");
  assert.equal(writes.length, 1);
  assert.deepEqual(newer.applied, ["a"]);
});

test("a conflict retries on the newer session when the job is still open", async () => {
  const stale = sessionFromServer(row({ applied: [], skipped: [] }));
  const newer = sessionFromServer(row({ revision: 3, applied: ["b"], skipped: [] }));
  const writes = [];
  const outcome = await saveStep({
    session: stale, key: "a", kind: "skipped",
    change: (lists) => withRecord(lists, "a", "skipped"),
    write: async (session, lists) => { writes.push([session.revision, lists]); if (session === stale) throw conflict(); },
    latest: () => newer,
  });
  assert.equal(outcome.saved, true);
  assert.deepEqual(outcome.before.applied, ["b"]);
  assert.deepEqual(writes[1], [3, { applied: ["b"], skipped: [{ dedupe_key: "a", reason: "" }], ended: false }]);
});

test("a second conflict gives up without writing again", async () => {
  let calls = 0;
  const outcome = await saveStep({
    session: sessionFromServer(row({ applied: [], skipped: [] })), key: "c", kind: "skipped",
    change: (lists) => withRecord(lists, "c", "skipped"),
    write: async () => { calls += 1; throw conflict(); },
    latest: () => sessionFromServer(row({ revision: 5, applied: [], skipped: [] })),
  });
  assert.equal(calls, 2);
  assert.equal(outcome.error.conflict, true);
});

test("keepsNewer lets a real application replace a skip only", () => {
  const session = sessionFromServer(row());
  assert.equal(keepsNewer(session, "a", "skipped"), true);
  assert.equal(keepsNewer(session, "a", "applied"), true);
  assert.equal(keepsNewer(session, "b", "skipped"), true);
  assert.equal(keepsNewer(session, "b", "applied"), false);
  assert.equal(keepsNewer(session, "c", "skipped"), false);
});

test("undo reverts nothing when the session save conflicts", async () => {
  let reverted = false;
  const outcome = await undoStep({ saveSession: async () => { throw conflict(); }, revertJob: async () => { reverted = true; } });
  assert.equal(outcome.undone, false);
  assert.equal(outcome.error.conflict, true);
  assert.equal(reverted, false);
});

test("undo saves the session before reverting the job", async () => {
  const order = [];
  const outcome = await undoStep({ saveSession: async () => { order.push("session"); }, revertJob: async () => { order.push("job"); } });
  assert.deepEqual(order, ["session", "job"]);
  assert.deepEqual(outcome, { undone: true });
});

test("undo reports a job revert that fails after the session landed", async () => {
  const outcome = await undoStep({ saveSession: async () => {}, revertJob: async () => { throw new Error("Offline."); } });
  assert.equal(outcome.undone, true);
  assert.equal(outcome.jobError.message, "Offline.");
});

test("staleRead drops older, equal and foreign revisions", () => {
  const held = sessionFromServer(row({ revision: 4 }));
  assert.equal(staleRead(held, row({ revision: 3 })), true);
  assert.equal(staleRead(held, row({ revision: 4 })), true);
  assert.equal(staleRead(held, row({ id: 9, revision: 8 })), true);
  assert.equal(staleRead(held, null), true);
  assert.equal(staleRead(held, row({ revision: 5 })), false);
});

// tab b ended, stale tab a skips
test("a stale skip never reopens a session ended in another tab", async () => {
  const stale = sessionFromServer(row({ applied: [], skipped: [] }));
  const ended = sessionFromServer(row({ revision: 3, applied: [], skipped: [], ended_at: "2026-10-28T12:00:00Z" }));
  const writes = [];
  const outcome = await saveStep({
    session: stale, key: "a", kind: "skipped",
    change: (lists) => ({ ...withRecord(lists, "a", "skipped"), ended: false }),
    write: async (session, lists) => { writes.push(lists); throw conflict(); },
    latest: () => ended,
  });
  assert.equal(outcome.ended, true);
  assert.equal(outcome.saved, false);
  assert.equal(writes.length, 1);
  assert.equal(stepUndo(outcome, true), "");
});

test("retried only changes the acting job and never clears an end", () => {
  const before = { applied: ["b"], skipped: [{ dedupe_key: "c", reason: "other tab" }], ended: true };
  const wanted = { applied: [], skipped: [{ dedupe_key: "a", reason: "Skipped in session" }], ended: false };
  assert.deepEqual(retried(before, wanted, "a"), { applied: ["b"], skipped: [{ dedupe_key: "c", reason: "other tab" }, { dedupe_key: "a", reason: "Skipped in session" }], ended: true });
  assert.deepEqual(retried({ applied: [], skipped: [], ended: false }, { applied: ["a"], skipped: [], ended: true }, "a"), { applied: ["a"], skipped: [], ended: true });
});

test("a retry leaves other jobs as the newer tab saved them", async () => {
  const stale = sessionFromServer(row({ applied: [], skipped: [{ dedupe_key: "b", reason: "mine" }] }));
  const newer = sessionFromServer(row({ revision: 3, applied: ["c"], skipped: [{ dedupe_key: "b", reason: "theirs" }] }));
  const writes = [];
  await saveStep({
    session: stale, key: "a", kind: "skipped",
    change: (lists) => ({ ...withRecord(lists, "a", "skipped"), applied: [], ended: false }),
    write: async (session, lists) => { writes.push(lists); if (session === stale) throw conflict(); },
    latest: () => newer,
  });
  assert.deepEqual(writes[1], { applied: ["c"], skipped: [{ dedupe_key: "b", reason: "theirs" }, { dedupe_key: "a", reason: "" }], ended: false });
});

test("stepUndo still offers undo when the job saved but the session did not", () => {
  assert.equal(stepUndo({ saved: true }, false), "session");
  assert.equal(stepUndo({ saved: false, error: new Error("Offline.") }, true), "job");
  assert.equal(stepUndo({ saved: false, error: new Error("Offline.") }, false), "");
  assert.equal(stepUndo({ saved: false, kept: "applied" }, true), "");
  assert.equal(stepUndo({ saved: false, error: conflict() }, true), "");
});

test("undoUnsaved reverts the job, then tries the session", async () => {
  const order = [];
  const outcome = await undoUnsaved({ revertJob: async () => { order.push("job"); }, saveSession: async () => { order.push("session"); throw new Error("Offline."); } });
  assert.deepEqual(order, ["job", "session"]);
  assert.equal(outcome.undone, true);
  assert.equal(outcome.sessionError.message, "Offline.");
  let touched = false;
  const failed = await undoUnsaved({ revertJob: async () => { throw conflict(); }, saveSession: async () => { touched = true; } });
  assert.equal(failed.undone, false);
  assert.equal(touched, false);
});

// adopts on 409 like writeSession
function fakeServer(record) {
  const server = { ...record, applied: [...record.applied], skipped: record.skipped.map((item) => ({ ...item })) };
  const write = async (session, lists, expected = session.revision) => {
    if (expected !== server.revision) {
      Object.assign(session, sessionFromServer(server), { revision: server.revision });
      throw conflict();
    }
    Object.assign(server, { applied: [...lists.applied], skipped: lists.skipped.map((item) => ({ ...item })), revision: server.revision + 1 });
    if (lists.ended) server.ended_at ||= "2026-10-28T12:00:00Z";
    Object.assign(session, sessionFromServer(server));
  };
  return { server, write };
}

// stale a must not erase b
test("an unsaved step's undo never erases another tab's save", async () => {
  const { server, write } = fakeServer(row({ revision: 2, applied: [], skipped: [] }));
  const session = sessionFromServer(server);
  const before = sessionLists(session);
  const revision = session.revision;
  session.applied = ["a"]; // the step only landed locally
  Object.assign(server, { revision: 3, skipped: [{ dedupe_key: "b", reason: "other tab" }], ended_at: "2026-10-28T12:00:00Z" });
  Object.assign(session, sessionFromServer(server)); // the refresh adopts it
  await assert.rejects(restoreUnsaved({ session, before, revision, key: "a", write }), (error) => error.conflict === true);
  assert.equal(server.revision, 3);
  assert.deepEqual(server.skipped, [{ dedupe_key: "b", reason: "other tab" }]);
  assert.ok(server.ended_at);
  assert.ok(session.endedAt);
  assert.deepEqual(session.skipped, [{ dedupe_key: "b", reason: "other tab" }]);
});

test("an unsaved step's undo writes when nothing changed since", async () => {
  const { server, write } = fakeServer(row({ revision: 2, applied: [], skipped: [{ dedupe_key: "b", reason: "mine" }] }));
  const session = sessionFromServer(server);
  const before = sessionLists(session);
  session.applied = ["a"];
  await restoreUnsaved({ session, before, revision: 2, key: "a", write });
  assert.equal(server.revision, 3);
  assert.deepEqual(server.applied, []);
  assert.deepEqual(session.applied, []);
});

test("an offline undo drops only its own job locally", async () => {
  const session = sessionFromServer(row({ revision: 2, applied: ["a", "c"], skipped: [{ dedupe_key: "b", reason: "x" }] }));
  session.endedAt = Date.now(); // the failed step ended it
  const before = { applied: ["c"], skipped: [{ dedupe_key: "b", reason: "x" }], ended: false };
  await assert.rejects(restoreUnsaved({ session, before, revision: 2, key: "a", write: async () => { throw new Error("Offline."); } }));
  assert.deepEqual(session.applied, ["c"]);
  assert.deepEqual(session.skipped, [{ dedupe_key: "b", reason: "x" }]);
  assert.equal(session.endedAt, null);
});

test("saveStep sends each write with the revision its lists came from", async () => {
  const stale = sessionFromServer(row({ revision: 2, applied: [], skipped: [] }));
  const newer = sessionFromServer(row({ revision: 5, applied: [], skipped: [] }));
  const expected = [];
  await saveStep({
    session: stale, key: "a", kind: "skipped",
    change: (lists) => withRecord(lists, "a", "skipped"),
    write: async (session, lists, revision) => { expected.push(revision); if (session === stale) throw conflict(); },
    latest: () => newer,
  });
  assert.deepEqual(expected, [2, 5]);
});
