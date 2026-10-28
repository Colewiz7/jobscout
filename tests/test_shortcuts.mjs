import assert from "node:assert/strict";
import test from "node:test";

import { ariaKeyshortcuts, formatShortcut, isTextEntry, matchShortcut, parseShortcut } from "../src/jobscout/static/shortcuts.js";

const key = (value, mods = {}) => ({ key: value, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });

test("parseShortcut splits modifiers from the key", () => {
  assert.deepEqual(parseShortcut("Shift+F10"), { key: "F10", mod: false, ctrl: false, meta: false, shift: true, alt: false });
  assert.equal(parseShortcut("Mod+Z").mod, true);
});

test("Mod matches ctrl or cmd but not neither", () => {
  assert.ok(matchShortcut(key("z", { ctrlKey: true }), "Mod+Z"));
  assert.ok(matchShortcut(key("z", { metaKey: true }), "Mod+Z"));
  assert.ok(matchShortcut(key("Z", { ctrlKey: true }), "Mod+Z"));
  assert.ok(!matchShortcut(key("z"), "Mod+Z"));
  assert.ok(!matchShortcut(key("z", { ctrlKey: true, shiftKey: true }), "Mod+Z"));
  assert.ok(!matchShortcut(key("z", { ctrlKey: true, altKey: true }), "Mod+Z"));
});

test("single letters refuse stray modifiers", () => {
  assert.ok(matchShortcut(key("u"), "U"));
  assert.ok(!matchShortcut(key("u", { ctrlKey: true }), "U"));
  assert.ok(!matchShortcut(key("U", { shiftKey: true }), "U"));
  assert.ok(!matchShortcut(key("u", { altKey: true }), "U"));
});

test("symbols ignore the shift they need", () => {
  assert.ok(matchShortcut(key("?", { shiftKey: true }), "?"));
  assert.ok(matchShortcut(key("/"), "/"));
});

test("named keys need their exact modifiers", () => {
  assert.ok(matchShortcut(key("F10", { shiftKey: true }), "Shift+F10"));
  assert.ok(!matchShortcut(key("F10"), "Shift+F10"));
  assert.ok(matchShortcut(key("ContextMenu"), "ContextMenu"));
  assert.ok(!matchShortcut(key("ContextMenu", { shiftKey: true }), "ContextMenu"));
});

test("ariaKeyshortcuts spells Mod both ways", () => {
  assert.equal(ariaKeyshortcuts("Mod+Z"), "Control+Z Meta+Z");
  assert.equal(ariaKeyshortcuts(["Mod+Z", "u"]), "Control+Z Meta+Z U");
  assert.equal(ariaKeyshortcuts("Shift+F10"), "Shift+F10");
  assert.equal(ariaKeyshortcuts("?"), "?");
});

test("formatShortcut matches the shortcut sheet", () => {
  assert.equal(formatShortcut("Mod+Z"), "Ctrl Z");
  assert.equal(formatShortcut("Mod+Z", true), "⌘ Z");
  assert.equal(formatShortcut("s"), "S");
  assert.equal(formatShortcut("Shift+F10"), "Shift F10");
  assert.equal(formatShortcut("ContextMenu"), "Menu");
});

test("isTextEntry covers every way to type", () => {
  const el = (tagName, extra = {}) => ({ tagName, isContentEditable: false, getAttribute: (name) => extra[name] ?? null, ...extra });
  assert.equal(isTextEntry(el("INPUT", { type: "text" })), true);
  assert.equal(isTextEntry(el("INPUT", { type: "search" })), true);
  assert.equal(isTextEntry(el("INPUT", { type: "date" })), true);
  assert.equal(isTextEntry(el("TEXTAREA")), true);
  assert.equal(isTextEntry(el("SELECT")), true);
  assert.equal(isTextEntry(el("DIV", { isContentEditable: true })), true);
  assert.equal(isTextEntry(el("DIV", { role: "textbox" })), true);
  assert.equal(isTextEntry(el("INPUT", { type: "checkbox" })), false);
  assert.equal(isTextEntry(el("INPUT", { type: "radio" })), false);
  assert.equal(isTextEntry(el("BUTTON")), false);
  assert.equal(isTextEntry(null), false);
});
