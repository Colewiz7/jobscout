import assert from "node:assert/strict";
import test from "node:test";
import { initialsTone, TONES } from "../src/jobscout/static/initials-tone.js";

test("the same company always gets the same tone", () => {
  assert.equal(initialsTone("acme"), initialsTone("acme"));
  assert.equal(initialsTone("the aerospace corporation"), initialsTone("the aerospace corporation"));
});

test("every tone comes from the fixed palette", () => {
  for (const key of ["", "a", "acme", "costar group", "日本", "x".repeat(500)]) {
    assert.ok(TONES.includes(initialsTone(key)));
  }
});

test("missing keys fall back without throwing", () => {
  assert.ok(TONES.includes(initialsTone(undefined)));
  assert.ok(TONES.includes(initialsTone(null)));
  assert.equal(initialsTone(undefined), initialsTone(""));
});

test("a spread of companies uses every tone", () => {
  const seen = new Set();
  for (const key of ["acme", "globex", "initech", "umbrella", "hooli", "stark", "wayne", "wonka", "cyberdyne", "tyrell"]) {
    seen.add(initialsTone(key));
  }
  assert.equal(seen.size, TONES.length);
});

// pinned so a hash change shows up as a test failure
test("known keys keep their tone across releases", () => {
  assert.deepEqual(["acme", "globex", "initech", "cyberdyne"].map(initialsTone), ["plum", "ink", "brass", "neutral"]);
});
