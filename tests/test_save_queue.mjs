import assert from "node:assert/strict";
import test from "node:test";

import { conflictMessage, createSaveQueue } from "../src/jobscout/static/save-queue.js";

// each send waits until the test settles it
function harness() {
  const sends = [];
  const conflicts = [];
  const queue = createSaveQueue({
    send: () => new Promise((resolve) => sends.push(resolve)),
    onConflict: (count, error) => conflicts.push([count, error.message]),
  });
  return { queue, sends, conflicts };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const conflict = () => Object.assign(new Error("changed elsewhere"), { conflict: true });

test("saves during a send batch into one", async () => {
  const { queue, sends } = harness();
  const first = queue.save();
  const second = queue.save();
  const third = queue.save();
  assert.equal(sends.length, 1);
  sends[0](null);
  assert.equal(await first, null);
  await tick();
  assert.equal(sends.length, 2);
  sends[1](null);
  assert.deepEqual(await Promise.all([second, third]), [null, null]);
  assert.equal(queue.busy, false);
});

test("a conflict drops every queued save", async () => {
  const { queue, sends, conflicts } = harness();
  const first = queue.save();
  const second = queue.save();
  const third = queue.save();
  const error = conflict();
  sends[0](error);
  assert.deepEqual(await Promise.all([first, second, third]), [error, error, error]);
  await tick();
  assert.equal(sends.length, 1); // the reload is never resent
  assert.deepEqual(conflicts, [[3, "changed elsewhere"]]);
});

test("a conflict with nothing queued counts one", async () => {
  const { queue, sends, conflicts } = harness();
  const only = queue.save();
  sends[0](conflict());
  assert.equal((await only).conflict, true);
  assert.deepEqual(conflicts, [[1, "changed elsewhere"]]);
});

test("a conflict counts changes, not saves", async () => {
  const { queue, sends, conflicts } = harness();
  queue.save(2);
  queue.save();
  queue.save(3);
  sends[0](conflict());
  await tick();
  assert.deepEqual(conflicts, [[6, "changed elsewhere"]]);
});

test("a plain failure still sends the queued save", async () => {
  const { queue, sends, conflicts } = harness();
  const first = queue.save();
  const second = queue.save();
  sends[0](new Error("offline"));
  assert.equal((await first).message, "offline");
  await tick();
  assert.equal(sends.length, 2);
  sends[1](null);
  assert.equal(await second, null);
  assert.deepEqual(conflicts, []);
});

test("saves after a conflict start fresh", async () => {
  const { queue, sends, conflicts } = harness();
  queue.save();
  sends[0](conflict());
  await tick();
  const later = queue.save();
  assert.equal(sends.length, 2);
  sends[1](null);
  assert.equal(await later, null);
  assert.equal(conflicts.length, 1);
});

test("a thrown send resolves as an error", async () => {
  const queue = createSaveQueue({ send: async () => { throw new Error("boom"); } });
  assert.equal((await queue.save()).message, "boom");
});

test("conflictMessage counts what was lost", () => {
  assert.equal(conflictMessage(1), "Your profile changed in another tab. The latest version is loaded and your last change wasn't saved.");
  assert.match(conflictMessage(3), /your last 3 changes weren't saved\.$/);
  assert.match(conflictMessage(0), /your last change wasn't saved/);
});
