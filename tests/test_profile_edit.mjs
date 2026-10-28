import assert from "node:assert/strict";
import test from "node:test";

import { adoptIds, applyOp, eligibilityWarning, fieldKey, limitReason, mergeDocuments, newId, opKeys, referenceWarning, renameOverrides, restoredMessage, withOrder } from "../src/jobscout/static/profile-edit.js";

const profile = () => ({
  fields: [{ key: "email", label: "Email", group: "Contact" }, { key: "degree", label: "Degree", group: "Education" }],
  documents: [{ id: 1, name: "General", default: true }, { id: 2, name: "Platform", default: false }],
  answer_templates: [{ id: 7, name: "Why", body: "a" }],
  stories: [{ id: "s1", title: "One" }, { id: "s2", title: "Two" }, { id: "s3", title: "Three" }],
});

// the inverse restores the start
const roundTrip = (op) => {
  const doc = profile();
  const { inverse } = applyOp(doc, op);
  assert.ok(inverse);
  assert.ok(applyOp(doc, inverse).inverse);
  assert.deepEqual(doc, profile());
};

test("every op undoes back to the start", () => {
  roundTrip({ type: "add", kind: "stories", item: { id: "s4", title: "Four" }, index: 1 });
  roundTrip({ type: "remove", kind: "stories", id: "s2" });
  roundTrip({ type: "remove", kind: "documents", id: 1 });
  roundTrip({ type: "move", kind: "stories", id: "s1", to: 2 });
  roundTrip({ type: "update", kind: "fields", id: "degree", patch: { label: "Highest degree", group: "School" } });
  roundTrip({ type: "update", kind: "answer_templates", id: 7, patch: { name: "Why us" } });
  roundTrip({ type: "default", id: 2 });
  roundTrip({ type: "default", id: null });
});

test("undo lands on the latest profile, not a snapshot", () => {
  const doc = profile();
  const { inverse } = applyOp(doc, { type: "remove", kind: "stories", id: "s1" });
  doc.stories[0].title = "Edited elsewhere";
  doc.stories.push({ id: "s9", title: "Added elsewhere" });
  applyOp(doc, inverse);
  assert.deepEqual(doc.stories.map((row) => row.id), ["s1", "s2", "s3", "s9"]);
  assert.equal(doc.stories[1].title, "Edited elsewhere");
});

test("moves clamp and a same place move is a no op", () => {
  const doc = profile();
  assert.deepEqual(applyOp(doc, { type: "move", kind: "stories", id: "s1", to: 99 }).inverse, { type: "move", kind: "stories", id: "s1", to: 0 });
  assert.deepEqual(doc.stories.map((row) => row.id), ["s2", "s3", "s1"]);
  assert.deepEqual(applyOp(doc, { type: "move", kind: "stories", id: "s1", to: 2 }), { noop: true });
});

test("adds respect limits and unique names", () => {
  const doc = profile();
  doc.stories = Array.from({ length: 10 }, (_, index) => ({ id: `s${index}`, title: `${index}` }));
  assert.match(applyOp(doc, { type: "add", kind: "stories", item: { id: "x", title: "x" } }).error, /up to 10 stories/);
  assert.match(applyOp(doc, { type: "add", kind: "answer_templates", item: { id: "t", name: " why " } }).error, /already exists/);
  assert.ok(applyOp(doc, { type: "update", kind: "answer_templates", id: 7, patch: { name: "WHY" } }).inverse); // its own name
  assert.match(applyOp(doc, { type: "add", kind: "fields", item: { key: "email", label: "Other" } }).error, /already exists/);
  assert.match(applyOp(doc, { type: "remove", kind: "stories", id: "gone" }).error, /already removed/);
});

test("one default at most, and a removed default comes back as default", () => {
  const doc = profile();
  applyOp(doc, { type: "add", kind: "documents", item: { id: "new-a", name: "New", default: true } });
  assert.deepEqual(doc.documents.map((row) => row.default), [false, false, true]);
  const { inverse } = applyOp(doc, { type: "remove", kind: "documents", id: "new-a" });
  assert.deepEqual(doc.documents.map((row) => row.default), [false, false]);
  applyOp(doc, inverse);
  assert.equal(doc.documents.find((row) => row.default).id, "new-a");
});

test("aliases follow an unsaved id to its saved one", () => {
  const doc = profile();
  const { inverse } = applyOp(doc, { type: "add", kind: "documents", item: { id: "new-a", name: "New" } });
  doc.documents[2].id = 40;
  assert.ok(applyOp(doc, inverse, { "new-a": 40 }).inverse);
  assert.equal(doc.documents.length, 2);
});

test("adoptIds pairs new rows by order and skips known ones", () => {
  const sent = withOrder({ documents: [{ id: 1, name: "General" }, { id: "new-a", name: "New" }], answer_templates: [{ id: "new-t", name: "Fit " }] });
  const saved = { documents: [{ id: 9, name: "New", sort_order: 10 }, { id: 1, name: "General", sort_order: 0 }], answer_templates: [{ id: 12, name: "Fit", sort_order: 0 }] };
  assert.deepEqual(adoptIds(sent, saved, new Set(["documents:1"])), { "new-a": 9, "new-t": 12 });
  assert.deepEqual(adoptIds(sent, { documents: [], answer_templates: [] }, new Set()), {});
});

test("withOrder numbers every list by position", () => {
  const sent = withOrder(profile());
  assert.deepEqual(sent.stories.map((row) => row.sort_order), [0, 10, 20]);
  assert.equal(profile().stories[0].sort_order, undefined);
});

test("opKeys cover the moved range", () => {
  const doc = profile();
  assert.deepEqual(opKeys(doc, { type: "move", kind: "stories", id: "s3", to: 1 }), ["profile:stories:s2", "profile:stories:s3"]);
  assert.deepEqual(opKeys(doc, { type: "add", kind: "fields", item: { key: "gpa" } }), ["profile:fields:gpa"]);
  assert.deepEqual(opKeys(doc, { type: "default", id: 2 }), ["profile:documents:default"]);
});

test("ids and keys never collide", () => {
  const values = [0.5, 0.25];
  const taken = newId("story", [], () => 0.5);
  assert.match(taken, /^story-[a-z0-9]+$/);
  assert.equal(newId("story", [taken], () => values.shift()), newId("story", [], () => 0.25));
  assert.equal(fieldKey("Work authorization", []), "work_authorization");
  assert.equal(fieldKey("Email", ["email", "email_2"]), "email_3");
  assert.equal(fieldKey("!!!", []), "field");
});

test("renameOverrides moves one key and keeps orphans", () => {
  assert.deepEqual(renameOverrides({ Why: "a", Old: "b" }, "Why", "Why us"), { "Why us": "a", Old: "b" });
  assert.deepEqual(renameOverrides({ Why: "a", "Why us": "stale" }, "Why", "Why us"), { "Why us": "a" });
  assert.deepEqual(renameOverrides({ Old: "b" }, "Why", "Why us"), { Old: "b" });
});

test("warnings name what a delete affects", () => {
  assert.equal(referenceWarning(0), "");
  assert.match(referenceWarning(1), /^1 application lists this.*It keeps/);
  assert.match(referenceWarning(3), /^3 applications list this.*They keep/);
  assert.equal(eligibilityWarning("email", { degree: ["Degree"] }), "");
  assert.match(eligibilityWarning("degree", { degree: ["Degree"] }), /use this field: Degree\./);
  assert.equal(limitReason("fields", 99), "");
  assert.match(limitReason("fields", 100), /up to 100 fields/);
});

test("restoredMessage owns up to lost links", () => {
  assert.equal(restoredMessage(0), "Document restored.");
  assert.equal(restoredMessage(1), "Document restored. Its link from 1 application wasn't restored.");
  assert.equal(restoredMessage(4), "Document restored. Its links from 4 applications weren't restored.");
});

test("mergeDocuments keeps ids, adds new ones and drops nothing", () => {
  const existing = [{ id: 7, name: "Platform resume", url: "a", default: true, application_count: 2 }, { id: 9, name: "Data resume", url: "b", default: false }];
  const merged = mergeDocuments(existing, [{ id: 99, name: " platform RESUME ", url: "new", default: false }, { id: 7, name: "Cover letter", url: "c", default: false }]);
  assert.deepEqual(merged.map((row) => [row.id, row.name, row.url, row.default]), [[7, " platform RESUME ", "new", true], [9, "Data resume", "b", false], [merged[2].id, "Cover letter", "c", false]]);
  assert.equal(merged[0].application_count, 2);
  assert.ok(![7, 9, "7", "9"].includes(merged[2].id));
  assert.deepEqual(existing[0].url, "a");
});

test("mergeDocuments moves the default only when the import names one", () => {
  const existing = [{ id: 1, name: "A", default: true }, { id: 2, name: "B", default: false }];
  assert.deepEqual(mergeDocuments(existing, [{ name: "B", default: true }]).map((row) => row.default), [false, true]);
  assert.deepEqual(mergeDocuments(existing, [{ name: "C", default: true }]).map((row) => row.default), [false, false, true]);
  assert.deepEqual(mergeDocuments(existing, []).map((row) => row.id), [1, 2]);
});
