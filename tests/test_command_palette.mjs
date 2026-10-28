import assert from "node:assert/strict";
import test from "node:test";

import { fuzzyScore, menuCommands, rankCommands } from "../src/jobscout/static/command-palette.js";

const items = [
  { label: "Open", run: () => "open" },
  { label: "Apply now", shortcut: "a", disabled: true, run: () => {} },
  { label: "Save", shortcut: "s", run: () => "save" },
  { label: "Saved", checked: true, run: () => {} },
  { label: "Applied", checked: false, run: () => {} },
  { label: "Hide permanently…", danger: true, run: () => {} },
];

test("fuzzyScore matches in order and rewards runs", () => {
  assert.equal(fuzzyScore("Go to Inbox", ""), 1);
  assert.equal(fuzzyScore("Go to Inbox", "zq"), -1);
  assert.ok(fuzzyScore("Save", "sav") > fuzzyScore("Snooze a while", "sav"));
});

test("menuCommands keeps labels and shortcuts from the menu", () => {
  const entries = menuCommands(items, { prefix: "job", detail: "Intern, Acme", format: (spec) => spec.toUpperCase() });
  assert.deepEqual(entries.map((entry) => entry.label), ["Open", "Save", "Move to Applied", "Hide permanently…"]);
  assert.equal(entries[1].shortcut, "S");
  assert.equal(entries[0].shortcut, "");
  assert.equal(entries[1].run(), "save");
  assert.equal(new Set(entries.map((entry) => entry.id)).size, entries.length);
  assert.ok(entries.every((entry) => entry.detail === "Intern, Acme" && entry.icon === "more"));
});

test("rankCommands filters and keeps subject order on ties", () => {
  const entries = [
    { id: "job:0", label: "Save", detail: "Intern, Acme" },
    { id: "job:1", label: "Queue", detail: "Intern, Acme" },
    { id: "inbox", label: "Go to Inbox", detail: "Triage new roles" },
  ];
  assert.deepEqual(rankCommands(entries, "").map((entry) => entry.id), ["job:0", "job:1", "inbox"]);
  assert.deepEqual(rankCommands(entries, "inbox").map((entry) => entry.id), ["inbox"]);
  assert.equal(rankCommands(entries, "queue")[0].id, "job:1");
  assert.deepEqual(rankCommands(entries, "xyz"), []);
});
