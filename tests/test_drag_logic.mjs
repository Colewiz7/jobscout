import assert from "node:assert/strict";
import test from "node:test";

import { autoScrollSpeed, autoScrollStep, boardUndoOrders, columnAt, columnKeys, createLoadGate, createOrderSaver, manualCompare, placeKey, positionedKeys, keyboardTarget, liftAnnouncement, moveAnnouncement, moveKey, restoreOrder, settleVelocity, slotIndex, sortedInsertIndex, startGesture } from "../src/jobscout/static/drag-logic.js";
import { fromRatio, solve, springTokens } from "../src/jobscout/static/motion.js";

const rows = (...heights) => {
  let top = 0;
  return heights.map((height) => { const rect = { top, height }; top += height + 8; return rect; });
};

test("slotIndex uses sibling midpoints", () => {
  const rects = rows(100, 100, 100); // mids 50, 158, 266
  assert.equal(slotIndex(rects, -20), 0);
  assert.equal(slotIndex(rects, 49), 0);
  assert.equal(slotIndex(rects, 51), 1);
  assert.equal(slotIndex(rects, 104), 1); // in the gap
  assert.equal(slotIndex(rects, 200), 2);
  assert.equal(slotIndex(rects, 900), 3); // below the last card
  assert.equal(slotIndex([], 40), 0);
});

test("moveKey moves one key and clamps", () => {
  assert.deepEqual(moveKey(["a", "b", "c", "d"], "a", 2), ["b", "c", "a", "d"]);
  assert.deepEqual(moveKey(["a", "b", "c", "d"], "d", 0), ["d", "a", "b", "c"]);
  assert.deepEqual(moveKey(["a", "b", "c"], "b", 9), ["a", "c", "b"]);
  assert.deepEqual(moveKey(["a", "b", "c"], "b", -3), ["b", "a", "c"]);
  assert.deepEqual(moveKey(["a", "b"], "z", 0), ["a", "b"]);
});

test("keyboardTarget handles arrows, home and end", () => {
  assert.equal(keyboardTarget(2, 5, "ArrowUp"), 1);
  assert.equal(keyboardTarget(0, 5, "ArrowUp"), 0);
  assert.equal(keyboardTarget(2, 5, "ArrowDown"), 3);
  assert.equal(keyboardTarget(4, 5, "ArrowDown"), 4);
  assert.equal(keyboardTarget(3, 5, "Home"), 0);
  assert.equal(keyboardTarget(1, 5, "End"), 4);
  assert.equal(keyboardTarget(1, 5, "x"), null);
});

test("restoreOrder brings back the old order over a changed queue", () => {
  assert.deepEqual(restoreOrder(["a", "b", "c"], ["c", "a", "b"]), ["a", "b", "c"]);
  assert.deepEqual(restoreOrder(["a", "b", "c"], ["c", "a"]), ["a", "c"]);
  assert.deepEqual(restoreOrder(["a", "b"], ["b", "x", "a"]), ["a", "x", "b"]);
  assert.deepEqual(restoreOrder(["a", "b"], ["b", "a", "y"]), ["a", "b", "y"]);
});

test("startGesture lifts mouse at 4px and touch at 400ms", () => {
  assert.equal(startGesture({ pointerType: "mouse", dx: 2, dy: 2, elapsed: 900 }), "pending");
  assert.equal(startGesture({ pointerType: "mouse", dx: 4, dy: 0, elapsed: 0 }), "lift");
  assert.equal(startGesture({ pointerType: "pen", dx: 0, dy: -5, elapsed: 0 }), "lift");
  assert.equal(startGesture({ pointerType: "touch", dx: 3, dy: 3, elapsed: 399 }), "pending");
  assert.equal(startGesture({ pointerType: "touch", dx: 3, dy: 3, elapsed: 400 }), "lift");
  assert.equal(startGesture({ pointerType: "touch", dx: 0, dy: 11, elapsed: 100 }), "cancel");
  assert.equal(startGesture({ pointerType: "touch", dx: 0, dy: 11, elapsed: 600 }), "cancel");
});

test("autoScrollSpeed ramps to 1200px/s at the edge", () => {
  assert.equal(autoScrollSpeed(48), 0);
  assert.equal(autoScrollSpeed(200), 0);
  assert.equal(autoScrollSpeed(0), 1200);
  assert.equal(autoScrollSpeed(-30), 1200);
  assert.equal(autoScrollSpeed(24), 300);
});

test("autoScrollStep covers the same ground at 60 and 120hz", () => {
  const speed = autoScrollSpeed(12);
  let at60 = 0;
  let at120 = 0;
  for (let frame = 0; frame < 60; frame += 1) at60 += autoScrollStep(speed, 1000 / 60);
  for (let frame = 0; frame < 120; frame += 1) at120 += autoScrollStep(speed, 1000 / 120);
  assert.ok(Math.abs(at60 - at120) < 1e-9);
  assert.ok(Math.abs(at60 - speed) < 1e-9);
});

test("settleVelocity keeps sign and clamps to half", () => {
  const { stiffness } = springTokens["spatial-default"];
  const max = 0.5 * Math.sqrt(stiffness) * 40;
  assert.equal(settleVelocity(5000, 40, stiffness), max);
  assert.equal(settleVelocity(-5000, -40, stiffness), -max);
  assert.equal(settleVelocity(-5000, 40, stiffness), -max);
  assert.equal(settleVelocity(30, 40, stiffness), 30);
  assert.equal(settleVelocity(800, 0, stiffness), 0);
});

test("settleVelocity keeps spatial-default overshoot near 0.2%", () => {
  const { ratio, stiffness } = springTokens["spatial-default"];
  const phys = fromRatio(ratio, stiffness);
  for (const distance of [12, 80, 400]) {
    const v = settleVelocity(1e6, distance, stiffness);
    let peak = 0;
    for (let t = 0; t < 1; t += 0.001) peak = Math.max(peak, solve(phys.stiffness, phys.damping, phys.mass, -distance, v, t).x);
    assert.ok(peak / distance < 0.0025, `overshoot ${(peak / distance * 100).toFixed(3)}% at ${distance}px`);
  }
});

test("columnAt picks the nearer column across gaps", () => {
  const columns = [{ left: 0, right: 100 }, { left: 108, right: 208 }, { left: 216, right: 316 }];
  assert.equal(columnAt(columns, 50), 0);
  assert.equal(columnAt(columns, 103), 0);
  assert.equal(columnAt(columns, 105), 1);
  assert.equal(columnAt(columns, 316), 2);
  assert.equal(columnAt(columns, -1), -1);
  assert.equal(columnAt(columns, 400), -1);
  assert.equal(columnAt([], 10), -1);
});

test("sortedInsertIndex predicts the landing slot under each sort", () => {
  const apps = [
    { dedupe_key: "a", company: "Acme", title: "A", status: "applied", applied_at: "2026-10-20", application_updated_at: "2026-10-25" },
    { dedupe_key: "c", company: "Cobalt", title: "C", status: "applied", applied_at: "2026-10-10", application_updated_at: "2026-10-01" },
  ];
  const moved = { dedupe_key: "b", company: "Birch", title: "B", status: "applying", applied_at: null, application_updated_at: "2026-09-01" };
  const now = "2026-10-27T12:00:00Z";
  assert.equal(sortedInsertIndex(apps, moved, "applied", "company", now), 1);
  assert.equal(sortedInsertIndex(apps, moved, "applied", "last_activity", now), 0);
  assert.equal(sortedInsertIndex(apps, moved, "applied", "applied", now), 0);
  assert.equal(sortedInsertIndex(apps, { ...moved, applied_at: "2026-10-15" }, "applied", "applied", now), 1);
  assert.equal(sortedInsertIndex(apps, moved, "applied", "status", now), 1);
  assert.equal(sortedInsertIndex([], moved, "offer", "company", now), 0);
});

test("announcements are one based", () => {
  assert.equal(moveAnnouncement(2, 12), "Moved to position 3 of 12.");
  assert.match(liftAnnouncement(0, 4), /^Picked up, position 1 of 4\./);
});

const board = [
  { dedupe_key: "a", status: "applied", board_position: 1, application_updated_at: "2026-10-01" },
  { dedupe_key: "b", status: "applied", board_position: null, application_updated_at: "2026-10-20" },
  { dedupe_key: "c", status: "applied", board_position: 0, application_updated_at: "2026-09-01" },
  { dedupe_key: "d", status: "applied", board_position: null, application_updated_at: "2026-10-25" },
  { dedupe_key: "e", status: "offer", board_position: 0, application_updated_at: "2026-10-26" },
  { dedupe_key: "f", status: "applying", board_position: null, application_updated_at: "2026-08-01" },
];

test("manual order puts placed cards first, then newest unplaced", () => {
  assert.deepEqual(columnKeys(board, "applied", "manual"), ["c", "a", "d", "b"]);
  assert.deepEqual([...board].sort(manualCompare).map((app) => app.dedupe_key), ["f", "c", "a", "d", "b", "e"]);
  assert.deepEqual(columnKeys(board, "applied", "last_activity"), ["d", "b", "a", "c"]);
  assert.deepEqual(columnKeys(board, "offer", "manual"), ["e"]);
});

test("placeKey reorders within a column and inserts from another", () => {
  assert.deepEqual(placeKey(["c", "a", "d", "b"], ["c", "a", "d", "b"], "b", 0), ["b", "c", "a", "d"]);
  assert.deepEqual(placeKey(["c", "a", "d", "b"], ["c", "a", "d", "b"], "c", 3), ["a", "d", "b", "c"]);
  assert.deepEqual(placeKey(["c", "a"], ["c", "a"], "z", 1), ["c", "z", "a"]);
  assert.deepEqual(placeKey([], [], "z", 0), ["z"]);
});

test("placeKey keeps filtered out cards where they were", () => {
  // only c and b match
  assert.deepEqual(placeKey(["c", "a", "d", "b"], ["c", "b"], "b", 0), ["b", "c", "a", "d"]);
  assert.deepEqual(placeKey(["c", "a", "d", "b"], ["c", "b"], "c", 1), ["a", "d", "b", "c"]);
  assert.deepEqual(placeKey(["c", "a", "d"], ["a"], "z", 1), ["c", "a", "z", "d"]);
  assert.deepEqual(placeKey(["c", "a", "d"], [], "z", 0), ["c", "a", "d", "z"]);
});

test("positionedKeys lists only placed cards in order", () => {
  assert.deepEqual(positionedKeys(board, "applied"), ["c", "a"]);
  assert.deepEqual(positionedKeys(board, "applying"), []);
});

test("sortedInsertIndex drops the old column position", () => {
  const moved = { dedupe_key: "e", status: "offer", board_position: 0, application_updated_at: "2026-10-26" };
  assert.equal(sortedInsertIndex(board.filter((app) => app.status === "applied"), moved, "applied", "manual", "2026-10-27"), 2);
});

test("boardUndoOrders pins an unpositioned card to its old slot under manual", () => {
  // unplaced b showed last
  assert.deepEqual(boardUndoOrders(board, "b", "applied", "offer", "manual"), [
    { status: "applied", keys: ["c", "a", "d", "b"] },
    { status: "offer", keys: ["e"] },
  ]);
});

test("boardUndoOrders restores exact positions, nulls included, under other sorts", () => {
  assert.deepEqual(boardUndoOrders(board, "b", "applied", "offer", "company"), [
    { status: "applied", keys: ["c", "a"] },
    { status: "offer", keys: ["e"] },
  ]);
  assert.deepEqual(boardUndoOrders(board, "f", "applying", "applied", "last_activity"), [
    { status: "applying", keys: [] },
    { status: "applied", keys: ["c", "a"] },
  ]);
  assert.deepEqual(boardUndoOrders(board, "x", "queued", "offer", "manual"), [{ status: "offer", keys: ["e"] }]);
});

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("order saver sends the latest order after a save in flight", async () => {
  const sent = [];
  const gates = [];
  const saved = [];
  const saver = createOrderSaver({ confirmed: ["a", "b", "c"], send: (order) => { sent.push(order); const gate = deferred(); gates.push(gate); return gate.promise; }, onSaved: (order) => saved.push(order) });
  saver.save(["b", "a", "c"]);
  assert.equal(saver.busy, true);
  saver.save(["b", "c", "a"]);
  saver.save(["c", "b", "a"]);
  assert.deepEqual(sent, [["b", "a", "c"]]);
  gates[0].resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(sent, [["b", "a", "c"], ["c", "b", "a"]]);
  assert.deepEqual(saved, []); // a newer order was waiting
  gates[1].resolve();
  await saver.idle();
  assert.deepEqual(saved, [["c", "b", "a"]]);
  assert.deepEqual(saver.confirmed, ["c", "b", "a"]);
  assert.equal(saver.busy, false);
});

test("order saver reverts to the last confirmed order on failure", async () => {
  const reverted = [];
  let calls = 0;
  const saver = createOrderSaver({ confirmed: ["a", "b"], send: async () => { calls += 1; if (calls === 2) throw new Error("nope"); }, onRevert: (order, error) => reverted.push([order, error.message]) });
  await saver.save(["b", "a"]);
  assert.deepEqual(saver.confirmed, ["b", "a"]);
  const flight = saver.save(["a", "b"]);
  saver.save(["b", "a"]); // dropped with the failure
  await flight;
  assert.deepEqual(reverted, [[["b", "a"], "nope"]]);
  assert.equal(calls, 2);
  assert.deepEqual(saver.confirmed, ["b", "a"]);
});

test("order saver reports each save's outcome", async () => {
  const gates = [];
  const saver = createOrderSaver({ confirmed: ["a", "b"], send: () => { const gate = deferred(); gates.push(gate); return gate.promise; } });
  const first = saver.save(["b", "a"]);
  const second = saver.save(["a", "b"]);
  const third = saver.save(["b", "a"]);
  gates[0].resolve();
  assert.equal(await first, null);
  gates[1].resolve();
  assert.equal(await second, null); // carried by the latest order
  assert.equal(await third, null);
  assert.equal(gates.length, 2);
});

test("order saver fails the in-flight and queued saves together", async () => {
  const gates = [];
  const reverted = [];
  const saver = createOrderSaver({ confirmed: ["a", "b"], send: () => { const gate = deferred(); gates.push(gate); return gate.promise; }, onRevert: (order) => reverted.push(order) });
  const inFlight = saver.save(["b", "a"]);
  const queued = saver.save(["a", "b"]);
  gates[0].reject(new Error("400"));
  assert.equal((await inFlight).message, "400");
  assert.equal((await queued).message, "400");
  assert.deepEqual(reverted, [["a", "b"]]);
  assert.equal(gates.length, 1); // the queued order never went out
  assert.equal(saver.busy, false);
  const retry = saver.save(["b", "a"]);
  gates[1].resolve();
  assert.equal(await retry, null);
  assert.deepEqual(saver.confirmed, ["b", "a"]);
});

test("order saver exposes the order still being saved", async () => {
  const gates = [];
  const saver = createOrderSaver({ confirmed: ["a", "b"], send: () => { const gate = deferred(); gates.push(gate); return gate.promise; } });
  assert.equal(saver.unsaved, null);
  const first = saver.save(["b", "a"]);
  assert.deepEqual(saver.unsaved, ["b", "a"]);
  saver.save(["a", "b"]);
  assert.deepEqual(saver.unsaved, ["a", "b"]); // the newest wins
  gates[0].resolve();
  await first;
  assert.deepEqual(saver.unsaved, ["a", "b"]);
  gates[1].resolve();
  await saver.idle();
  assert.equal(saver.unsaved, null);
  const failed = saver.save(["b", "a"]);
  gates[2].reject(new Error("400"));
  await failed;
  assert.equal(saver.unsaved, null);
});

test("a landed save outdates a fetch that started before it", async () => {
  const gate = createLoadGate();
  let refetch = false;
  const saver = createOrderSaver({ send: async () => {}, onLanded: () => { refetch = gate.invalidate(); } });
  const stale = gate.start(); // read the old order
  await saver.save(["b", "a"]);
  assert.equal(gate.current(stale), false);
  assert.equal(refetch, true);
  gate.finish(stale);
  const fresh = gate.start();
  assert.equal(gate.current(fresh), true);
  gate.finish(fresh);
  assert.equal(gate.invalidate(), false); // nothing in flight
});

test("order saver prunes keys that left the column", async () => {
  const gates = [];
  const sent = [];
  const saver = createOrderSaver({ confirmed: ["a", "b"], send: (keys) => { sent.push(keys); const gate = deferred(); gates.push(gate); return gate.promise; } });
  saver.save(["b", "a"]);
  saver.save(["x", "a", "b"]);
  saver.prune((key) => key !== "x");
  assert.deepEqual(saver.unsaved, ["a", "b"]);
  gates[0].resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(sent[1], ["a", "b"]); // x never went out
  gates[1].resolve();
  await saver.idle();
});
