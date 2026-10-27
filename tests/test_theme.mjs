import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/jobscout/static/theme.js", import.meta.url), "utf8");

// theme.js isnt a module
function load(reduce) {
  const query = { matches: reduce, listeners: [], types: [], addEventListener(type, fn) { this.types.push(type); this.listeners.push(fn); } };
  const context = { document: { documentElement: { dataset: {} } }, localStorage: { getItem: () => null }, matchMedia: (text) => { query.text = text; return query; } };
  vm.runInNewContext(source, context);
  return { context, query, dataset: context.document.documentElement.dataset };
}

test("motionMode maps the os setting", () => {
  const { context } = load(false);
  assert.equal(context.motionMode(true), "reduce");
  assert.equal(context.motionMode(false), "full");
});

test("theme.js watches the reduced motion query", () => {
  const { query } = load(false);
  assert.equal(query.text, "(prefers-reduced-motion: reduce)");
  assert.deepEqual(query.types, ["change"]);
});

test("data-motion follows the os setting at load", () => {
  assert.equal(load(true).dataset.motion, "reduce");
  assert.equal(load(false).dataset.motion, "full");
});

test("data-motion updates when the os setting changes", () => {
  const { query, dataset } = load(false);
  for (const fn of query.listeners) fn({ matches: true });
  assert.equal(dataset.motion, "reduce");
  for (const fn of query.listeners) fn({ matches: false });
  assert.equal(dataset.motion, "full");
});
