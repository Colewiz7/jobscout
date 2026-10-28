import assert from "node:assert/strict";
import test from "node:test";

import { countLabel } from "../src/jobscout/static/counts.js";

test("one takes the singular", () => {
  assert.equal(countLabel(1, "posting"), "1 posting");
});

test("zero and many take the plural", () => {
  assert.equal(countLabel(0, "application"), "0 applications");
  assert.equal(countLabel(3, "application"), "3 applications");
});

test("irregular plurals pass through", () => {
  assert.equal(countLabel(2, "company", "companies"), "2 companies");
  assert.equal(countLabel(1, "company", "companies"), "1 company");
});

test("big counts are grouped", () => {
  assert.equal(countLabel(12000, "connection"), `${new Intl.NumberFormat().format(12000)} connections`);
});
