// one menu, three ways in
import { placeMenu, stepIndex, typeahead } from "./menu-logic.js";
import { ariaKeyshortcuts, formatShortcut, matchShortcut } from "./shortcuts.js";

const LONG_PRESS = 500;
const SLOP = 10;
const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

let menu = null;
let current = null;
let buffer = "";
let bufferTimer = 0;
let openedAt = 0;

function typing(target) {
  return target instanceof Element && Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
}

function buttons() {
  return [...menu.querySelectorAll("[data-menu-index]")];
}

function focusItem(index) {
  buttons()[index]?.focus({ preventScroll: false });
}

function activeIndex() {
  return buttons().indexOf(document.activeElement);
}

function itemNode(item, index) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `menu-item interactive${item.danger ? " is-danger" : ""}`;
  button.tabIndex = -1;
  button.dataset.menuIndex = String(index);
  button.setAttribute("role", item.checked === undefined ? "menuitem" : "menuitemradio");
  if (item.checked !== undefined) button.setAttribute("aria-checked", String(item.checked));
  if (item.disabled) button.setAttribute("aria-disabled", "true");
  if (item.shortcut) button.setAttribute("aria-keyshortcuts", ariaKeyshortcuts(item.shortcut));
  const check = document.createElement("span");
  check.className = "menu-check";
  check.setAttribute("aria-hidden", "true");
  if (item.checked) check.textContent = "✓";
  const label = document.createElement("span");
  label.className = "menu-label";
  label.textContent = item.label;
  button.append(check, label);
  if (item.hint) {
    const hint = document.createElement("span");
    hint.className = "menu-hint";
    hint.textContent = item.hint;
    button.append(hint);
  }
  if (item.shortcut) {
    const key = document.createElement("kbd");
    key.className = "menu-shortcut";
    key.setAttribute("aria-hidden", "true");
    key.textContent = formatShortcut([].concat(item.shortcut)[0], mac);
    button.append(key);
  }
  return button;
}

function onKeydown(event) {
  event.stopPropagation(); // app shortcuts wait for close
  const count = buttons().length;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); focusItem(stepIndex(count, activeIndex(), event.key === "ArrowDown" ? 1 : -1)); return; }
  if (event.key === "Home" || event.key === "End") { event.preventDefault(); focusItem(event.key === "Home" ? 0 : count - 1); return; }
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(activeIndex()); return; }
  if (event.key === "Escape") { event.preventDefault(); closeMenu(); return; }
  if (event.key === "Tab") { closeMenu(); return; }
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    clearTimeout(bufferTimer);
    buffer += event.key;
    bufferTimer = setTimeout(() => { buffer = ""; }, 500);
    const index = typeahead(current.items.map((item) => item.label), buffer, activeIndex());
    if (index >= 0) focusItem(index);
  }
}

function activate(index) {
  const item = current?.items[index];
  if (!item || item.disabled) return;
  const { refocus } = current;
  closeMenu();
  item.run();
  // actions often rerender the row
  requestAnimationFrame(() => {
    if ((!document.activeElement || document.activeElement === document.body) && refocus) refocus();
  });
}

function ensure() {
  if (menu) return menu;
  menu = document.createElement("div");
  menu.className = "action-menu";
  menu.popover = "manual";
  menu.setAttribute("role", "menu");
  menu.tabIndex = -1; // padding clicks keep focus inside
  menu.addEventListener("keydown", onKeydown);
  menu.addEventListener("focusout", (event) => {
    const to = event.relatedTarget;
    if (current && !menu.contains(to) && !current.trigger?.contains(to)) closeMenu({ restoreFocus: false }); // trigger clicks toggle instead
  });
  menu.addEventListener("click", (event) => {
    const button = event.target.closest("[data-menu-index]");
    if (button) activate(Number(button.dataset.menuIndex));
  });
  menu.addEventListener("pointermove", (event) => {
    const button = event.target.closest("[data-menu-index]");
    if (button && button !== document.activeElement) button.focus({ preventScroll: true });
  });
  menu.addEventListener("contextmenu", (event) => event.preventDefault());
  document.addEventListener("pointerdown", (event) => {
    if (!current || menu.contains(event.target)) return;
    if (current.trigger?.contains(event.target)) return; // the trigger toggles on click
    closeMenu();
  }, true);
  document.addEventListener("scroll", (event) => { if (current && !menu.contains(event.target)) closeMenu({ restoreFocus: false }); }, true);
  window.addEventListener("resize", () => closeMenu({ restoreFocus: false }));
  window.addEventListener("blur", () => closeMenu({ restoreFocus: false }));
  document.body.append(menu);
  return menu;
}

export function openMenu(items, { label, anchor = null, point = null, trigger = null, returnTo = null, refocus = null, align = "start" }) {
  if (!items.length) return;
  ensure();
  if (current) closeMenu({ restoreFocus: false });
  const previous = document.activeElement;
  menu.replaceChildren();
  items.forEach((item, index) => {
    if (item.divider && index) menu.append(Object.assign(document.createElement("div"), { className: "menu-divider", role: "separator" }));
    menu.append(itemNode(item, index));
  });
  menu.setAttribute("aria-label", label);
  menu.inert = false;
  menu.style.maxHeight = "";
  menu.style.left = "0px";
  menu.style.top = "0px";
  menu.showPopover();
  const box = point ? { left: point.x, right: point.x, top: point.y, bottom: point.y } : anchor.getBoundingClientRect();
  // offset sizes skip the entry scale
  const place = placeMenu({ anchor: box, width: menu.offsetWidth, height: menu.offsetHeight, viewport: { width: window.innerWidth, height: window.innerHeight }, align, gap: point ? 0 : 4 });
  Object.assign(menu.style, { left: `${place.left}px`, top: `${place.top}px`, maxHeight: `${place.maxHeight}px`, transformOrigin: place.origin });
  trigger?.setAttribute("aria-expanded", "true");
  current = { items, trigger, returnTo: returnTo || trigger || (previous instanceof HTMLElement ? previous : null), refocus };
  openedAt = performance.now();
  focusItem(0);
}

export function closeMenu({ restoreFocus = true } = {}) {
  if (!current) return;
  const { trigger, returnTo } = current;
  current = null;
  buffer = "";
  trigger?.setAttribute("aria-expanded", "false");
  menu.inert = true; // stays painted while it fades
  menu.hidePopover();
  if (restoreFocus && returnTo?.isConnected) returnTo.focus({ preventScroll: true });
  else if (menu.contains(document.activeElement)) document.activeElement.blur();
}

export function menuOpenFor(trigger) {
  return Boolean(current && current.trigger === trigger);
}

export function toggleMenu(trigger, items, options) {
  if (menuOpenFor(trigger)) { closeMenu(); return; }
  openMenu(items, { ...options, anchor: trigger, trigger, align: "end" });
}

export function bindObjectMenus(resolve) {
  let press = null;
  let swallow = null;
  const show = (target, point) => {
    const spec = resolve(target);
    if (!spec || !spec.items.length) return false;
    openMenu(spec.items, { ...spec, point, align: point ? "start" : spec.align });
    return true;
  };
  const cancelPress = () => { if (press) clearTimeout(press.timer); press = null; };

  document.addEventListener("contextmenu", (event) => {
    if (event.shiftKey || typing(event.target)) return; // shift keeps the browser menu
    if (String(window.getSelection?.() || "").trim()) return;
    if (current && performance.now() - openedAt < 700) { event.preventDefault(); return; } // dont open twice per gesture
    const point = event.clientX || event.clientY ? { x: event.clientX, y: event.clientY } : null;
    if (show(event.target, point)) event.preventDefault();
  });

  document.addEventListener("keydown", (event) => {
    if (!matchShortcut(event, "Shift+F10") && !matchShortcut(event, "ContextMenu")) return;
    if (typing(event.target)) return;
    if (show(document.activeElement, null)) event.preventDefault();
  });

  document.addEventListener("click", (event) => {
    // only the long-press tap itself
    const late = swallow && (performance.now() > swallow.until || menu?.contains(event.target) || !(swallow.target.contains(event.target) || event.target.contains(swallow.target)));
    if (swallow && !late) { swallow = null; event.preventDefault(); event.stopPropagation(); return; }
    swallow = null;
    const trigger = event.target.closest("[data-menu-trigger]");
    if (!trigger) return;
    event.preventDefault();
    event.stopPropagation(); // the row behind stays put
    if (menuOpenFor(trigger)) { closeMenu(); return; }
    const spec = resolve(trigger);
    if (spec?.items.length) openMenu(spec.items, { ...spec, anchor: trigger, trigger, returnTo: spec.returnTo || trigger, align: "end" });
  }, true);

  document.addEventListener("pointerdown", (event) => {
    swallow = null;
    cancelPress();
    if (event.pointerType !== "touch" || !event.isPrimary || typing(event.target)) return;
    if (event.target.closest("[data-drag-handle]")) return; // long-press there drags
    const { clientX: x, clientY: y, target } = event;
    press = { x, y, timer: setTimeout(() => { press = null; if (show(target, { x, y })) swallow = { target, until: performance.now() + 1500, lifting: true }; }, LONG_PRESS) };
  });
  document.addEventListener("pointermove", (event) => {
    if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > SLOP) cancelPress();
  });
  for (const type of ["pointerup", "pointercancel"]) document.addEventListener(type, cancelPress);
  // no ghost mousedown stealing menu focus
  document.addEventListener("touchend", (event) => { if (swallow?.lifting) { swallow.lifting = false; event.preventDefault(); } }, { passive: false });
  document.addEventListener("scroll", cancelPress, true);
}
