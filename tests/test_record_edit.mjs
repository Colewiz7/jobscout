import assert from "node:assert/strict";
import test from "node:test";

import { changedFields, fromLocalInput, pickFields, splitInterviews, toLocalInput } from "../src/jobscout/static/record-edit.js";

test("changedFields keeps only trimmed changes", () => {
  const original = { company: "Acme", title: "Intern ", url: "https://acme.example/1", notes: null };
  const draft = { company: " Acme ", title: "Platform Intern", url: "https://acme.example/1", notes: "" };
  assert.deepEqual(changedFields(original, draft), { title: "Platform Intern" });
});

test("changedFields sends a cleared field as empty", () => {
  assert.deepEqual(changedFields({ email: "a@example.com" }, { email: "  " }), { email: "" });
});

test("changedFields passes non-string values through", () => {
  assert.deepEqual(changedFields({ ends_at: "2026-11-10T15:00:00Z" }, { ends_at: null }), { ends_at: null });
  assert.deepEqual(changedFields({ ends_at: null }, { ends_at: null }), {});
});

test("pickFields fills missing fields with null", () => {
  assert.deepEqual(pickFields({ name: "Ada", title: "" }, ["name", "title", "email"]), { name: "Ada", title: "", email: null });
});

test("local inputs round trip to the same minute", () => {
  const iso = "2026-11-10T14:30:00.000Z";
  const local = toLocalInput(iso);
  assert.match(local, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
  assert.equal(fromLocalInput(local), iso);
});

test("local inputs reject bad values", () => {
  assert.equal(toLocalInput(""), "");
  assert.equal(toLocalInput("soon"), "");
  assert.equal(fromLocalInput(""), null);
  assert.equal(fromLocalInput("soon"), undefined);
});

test("splitInterviews sorts upcoming soonest first and past latest first", () => {
  const now = new Date("2026-11-01T12:00:00Z");
  const items = [
    { id: 1, starts_at: "2026-10-01T12:00:00Z" },
    { id: 2, starts_at: "2026-11-05T12:00:00Z", cancelled_at: "2026-10-30T12:00:00Z" },
    { id: 3, starts_at: "2026-11-03T12:00:00Z" },
    { id: 4, starts_at: "2026-10-20T12:00:00Z", cancelled_at: "2026-10-19T12:00:00Z" },
  ];
  const { upcoming, past, scheduled } = splitInterviews(items, now);
  assert.deepEqual(upcoming.map((item) => item.id), [3, 2]);
  assert.deepEqual(past.map((item) => item.id), [4, 1]);
  assert.equal(scheduled, 1);
});

test("splitInterviews counts an interview starting now as upcoming", () => {
  const now = new Date("2026-11-01T12:00:00Z");
  assert.deepEqual(splitInterviews([{ id: 1, starts_at: "2026-11-01T12:00:00Z" }], now).upcoming.map((item) => item.id), [1]);
});
