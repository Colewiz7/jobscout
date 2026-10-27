import assert from "node:assert/strict";
import test from "node:test";

import { placeMenu, stepIndex, typeahead } from "../src/jobscout/static/menu-logic.js";

const viewport = { width: 1000, height: 800 };
const rect = (left, top, width = 40, height = 40) => ({ left, top, right: left + width, bottom: top + height });
const point = (x, y) => rect(x, y, 0, 0);

test("placeMenu opens below and from the start edge when it fits", () => {
  const place = placeMenu({ anchor: rect(100, 100), width: 200, height: 150, viewport });
  assert.deepEqual(place, { left: 100, top: 144, maxHeight: 150, origin: "left top" });
});

test("placeMenu end align lines up with the trigger's right edge", () => {
  const place = placeMenu({ anchor: rect(500, 100), width: 200, height: 150, viewport, align: "end" });
  assert.equal(place.left, 340);
  assert.equal(place.origin, "right top");
});

test("placeMenu flips above near the bottom edge", () => {
  const place = placeMenu({ anchor: rect(100, 700), width: 200, height: 150, viewport });
  assert.equal(place.top, 700 - 4 - 150);
  assert.equal(place.origin, "left bottom");
});

test("placeMenu flips to the end edge near the right edge", () => {
  const place = placeMenu({ anchor: point(950, 100), width: 200, height: 150, viewport, gap: 0 });
  assert.equal(place.left, 750);
  assert.equal(place.origin, "right top");
});

test("placeMenu end align flips to start near the left edge", () => {
  const place = placeMenu({ anchor: rect(20, 100), width: 200, height: 150, viewport, align: "end" });
  assert.equal(place.left, 20);
  assert.equal(place.origin, "left top");
});

test("placeMenu flips both ways in the bottom right corner", () => {
  const place = placeMenu({ anchor: point(990, 790), width: 200, height: 150, viewport, gap: 0 });
  assert.deepEqual(place, { left: 790, top: 640, maxHeight: 150, origin: "right bottom" });
});

test("placeMenu takes the roomier side when neither fits", () => {
  const narrow = { width: 412, height: 800 };
  assert.equal(placeMenu({ anchor: point(150, 100), width: 300, height: 100, viewport: narrow, gap: 0 }).origin, "left top");
  assert.equal(placeMenu({ anchor: point(260, 100), width: 300, height: 100, viewport: narrow, gap: 0, align: "start" }).origin, "right top");
});

test("placeMenu clamps a menu wider than either side", () => {
  const place = placeMenu({ anchor: point(300, 100), width: 900, height: 100, viewport: { width: 920, height: 800 }, gap: 0 });
  assert.equal(place.left, 12);
  const wider = placeMenu({ anchor: point(300, 100), width: 950, height: 100, viewport: { width: 920, height: 800 }, gap: 0 });
  assert.equal(wider.left, 8);
});

test("placeMenu caps height to the roomier side", () => {
  const place = placeMenu({ anchor: rect(100, 300), width: 200, height: 2000, viewport });
  assert.equal(place.maxHeight, 800 - 8 - 340 - 4);
  assert.equal(place.top, 344);
  const above = placeMenu({ anchor: rect(100, 500), width: 200, height: 2000, viewport });
  assert.equal(above.maxHeight, 500 - 8 - 4);
  assert.equal(above.top, 8);
  assert.equal(above.origin, "left bottom");
});

test("placeMenu stays on screen in a tiny viewport", () => {
  const place = placeMenu({ anchor: point(10, 10), width: 200, height: 300, viewport: { width: 150, height: 120 }, gap: 0 });
  assert.equal(place.left, 8);
  assert.ok(place.top >= 8 && place.top + place.maxHeight <= 112);
});

test("stepIndex wraps both ways and starts from the ends", () => {
  assert.equal(stepIndex(3, 2, 1), 0);
  assert.equal(stepIndex(3, 0, -1), 2);
  assert.equal(stepIndex(3, -1, 1), 0);
  assert.equal(stepIndex(3, -1, -1), 2);
  assert.equal(stepIndex(0, 0, 1), -1);
});

test("typeahead cycles on one letter and refines on more", () => {
  const labels = ["Save", "Queue", "Select", "Dismiss", "Save for later"];
  assert.equal(typeahead(labels, "s", 0), 2);
  assert.equal(typeahead(labels, "s", 2), 4);
  assert.equal(typeahead(labels, "s", 4), 0);
  assert.equal(typeahead(labels, "sa", 0), 0);
  assert.equal(typeahead(labels, "save f", 0), 4);
  assert.equal(typeahead(labels, "D", -1), 3);
  assert.equal(typeahead(labels, "z", 0), -1);
});
