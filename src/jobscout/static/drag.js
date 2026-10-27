// native dnd cant carry release velocity
import { autoScrollSpeed, autoScrollStep, columnAt, EDGE, settleVelocity, slotIndex, startGesture } from "./drag-logic.js";
import { Spring, VelocityTracker, fromRatio, springTokens } from "./motion.js";

export const SPATIAL_DEFAULT = "cubic-bezier(0.21, 0.05, 0.28, 1)";
const SHIFT = 200;
const FLIP_CAP = 30;
const SETTLE = springTokens["spatial-default"];

export const reducedMotion = () => document.documentElement.dataset.motion === "reduce";

const onScreen = (rect) => rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;

// moveBefore keeps focus where supported
export function moveNode(parent, node, before) {
  const active = node.contains(document.activeElement) ? document.activeElement : null;
  if (parent.moveBefore) {
    try { parent.moveBefore(node, before); } catch { parent.insertBefore(node, before); }
  } else parent.insertBefore(node, before);
  if (active && document.activeElement !== active && active.isConnected) active.focus({ preventScroll: true });
}

// moving the focused node would blur
export function placeNodes(parent, nodes, anchor = null) {
  const members = new Set(nodes);
  const sibling = (node, step) => {
    let next = node[step];
    while (next && !members.has(next)) next = next[step];
    return next;
  };
  const at = anchor && nodes.includes(anchor) ? nodes.indexOf(anchor) : -1;
  if (at < 0) { nodes.forEach((node) => moveNode(parent, node, null)); return; }
  let before = anchor;
  for (const node of nodes.slice(0, at).reverse()) {
    if (sibling(node, "nextElementSibling") !== before || node.parentNode !== parent) moveNode(parent, node, before);
    before = node;
  }
  let after = anchor;
  for (const node of nodes.slice(at + 1)) {
    if (sibling(after, "nextElementSibling") !== node || node.parentNode !== parent) moveNode(parent, node, after.nextElementSibling);
    after = node;
  }
}

// retargets mid flight without a jump
export function flip(nodes, change) {
  if (reducedMotion()) { change(); return; }
  const first = new Map(nodes.map((node) => [node, node.getBoundingClientRect()]));
  change();
  const moves = [];
  for (const node of nodes) {
    if (!node.isConnected) continue;
    node.getAnimations().forEach((animation) => { if (animation.id === "flip") animation.cancel(); });
    const before = first.get(node);
    const after = node.getBoundingClientRect();
    const dx = before.left - after.left;
    const dy = before.top - after.top;
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
    if (!onScreen(before) && !onScreen(after)) continue; // content-visibility skips these
    moves.push([node, dx, dy]);
  }
  if (moves.length > FLIP_CAP) return;
  for (const [node, dx, dy] of moves) {
    const animation = node.animate([{ translate: `${dx}px ${dy}px` }, { translate: "0 0" }], { duration: SHIFT, easing: SPATIAL_DEFAULT });
    animation.id = "flip";
  }
}

// flip offsets would skew the midpoints
function layoutRect(node) {
  const rect = node.getBoundingClientRect();
  const [tx = 0, ty = 0] = (getComputedStyle(node).translate || "none").split(" ").map((part) => parseFloat(part) || 0);
  return { top: rect.top - ty, bottom: rect.bottom - ty, left: rect.left - tx, right: rect.right - tx, height: rect.height, width: rect.width };
}

function scrollers(node) {
  let x = null;
  let y = null;
  for (let el = node.parentElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    const style = getComputedStyle(el);
    if (!x && /auto|scroll/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1) x = el;
    if (!y && /auto|scroll/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1) y = el;
  }
  const page = document.scrollingElement;
  return { x: x || page, y: y || page };
}

function edges(el) {
  if (el === document.scrollingElement) return { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth };
  return el.getBoundingClientRect();
}

export function bindDrag(config) {
  let pending = null;
  let drag = null;
  let settling = null;
  const itemsOf = (list, skip) => [...list.children].filter((el) => el !== skip && el.matches(config.item));

  const clearPending = () => { if (pending) clearTimeout(pending.timer); pending = null; };

  function finishSettle() {
    if (!settling) return;
    cancelAnimationFrame(settling.frame);
    settling.done();
    settling = null;
  }

  function lift() {
    const { handle, item, pointerId, x, y } = pending;
    clearPending();
    finishSettle();
    const list = item.parentElement;
    item.getAnimations().forEach((animation) => animation.cancel());
    const rect = item.getBoundingClientRect();
    const placeholder = document.createElement("div");
    placeholder.className = "drag-placeholder";
    placeholder.setAttribute("aria-hidden", "true");
    Object.assign(placeholder.style, { height: `${rect.height}px`, borderRadius: getComputedStyle(item).borderRadius });
    list.insertBefore(placeholder, item);
    Object.assign(item.style, { position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`, margin: "0", transformOrigin: `${x - rect.left}px ${y - rect.top}px` });
    item.classList.add("is-lifted");
    document.documentElement.classList.add("is-dragging");
    try { handle.setPointerCapture(pointerId); } catch { /* touch already captures */ }
    const origin = { list, index: itemsOf(list, item).filter((el) => el.compareDocumentPosition(placeholder) & Node.DOCUMENT_POSITION_FOLLOWING).length };
    drag = { handle, item, pointerId, rect, placeholder, origin, at: { ...origin }, x, y, dx: 0, dy: 0, vx: new VelocityTracker(), vy: new VelocityTracker(), last: 0, frame: 0, zone: null };
    drag.vx.add(x, performance.now());
    drag.vy.add(y, performance.now());
    config.onLift?.(item);
    drag.frame = requestAnimationFrame(tick);
  }

  function target(px, py) {
    const lists = config.lists();
    if (!lists.length) return null;
    const zones = lists.map((list) => (config.zone?.(list) || list).getBoundingClientRect());
    let index = 0;
    if (lists.length === 1) {
      const box = zones[0];
      if (px < box.left - EDGE || px > box.right + EDGE) return null;
    } else {
      index = columnAt(zones, px);
      const box = zones[index];
      if (index < 0 || py < box.top - EDGE || py > box.bottom + EDGE) return null;
    }
    const list = lists[index];
    const centre = py - drag.y + drag.rect.top + drag.rect.height / 2;
    const slot = slotIndex(itemsOf(list, drag.item).map(layoutRect), centre);
    return { list, index: config.slot ? config.slot({ list, slot, item: drag.item, origin: drag.origin }) : slot };
  }

  function placeAt(spot) {
    const { list, index } = spot || drag.origin;
    if (drag.at.list === list && drag.at.index === index && drag.placeholder.parentElement === list) return;
    const touched = [...new Set([drag.at.list, list])].flatMap((each) => itemsOf(each, drag.item));
    flip(touched, () => {
      const ref = itemsOf(list, drag.item)[index] || null;
      list.insertBefore(drag.placeholder, ref);
    });
    drag.at = { list, index };
  }

  function highlight(spot) {
    const zone = spot && spot.list !== drag.origin.list ? config.zone?.(spot.list) : null;
    if (zone === drag.zone) return;
    drag.zone?.classList.remove("is-drop-target");
    zone?.classList.add("is-drop-target");
    drag.zone = zone;
  }

  function tick(now) {
    if (!drag) return;
    if (!drag.item.isConnected) { abort(); return; }
    const dt = drag.last ? Math.min(50, now - drag.last) : 0;
    drag.last = now;
    const px = drag.x + drag.dx;
    const py = drag.y + drag.dy;
    const { x, y } = scrollers(drag.placeholder);
    const ey = edges(y);
    const up = autoScrollSpeed(py - ey.top);
    const down = autoScrollSpeed(ey.bottom - py);
    if (up || down) y.scrollTop += autoScrollStep(down - up, dt);
    if (x !== document.scrollingElement || document.scrollingElement.scrollWidth > window.innerWidth) {
      const ex = edges(x);
      const left = autoScrollSpeed(px - ex.left);
      const right = autoScrollSpeed(ex.right - px);
      if (left || right) x.scrollLeft += autoScrollStep(right - left, dt);
    }
    const spot = target(px, py);
    placeAt(spot);
    highlight(spot);
    drag.spot = spot;
    drag.frame = requestAnimationFrame(tick);
  }

  function teardown(session) {
    cancelAnimationFrame(session.frame);
    session.zone?.classList.remove("is-drop-target");
    document.documentElement.classList.remove("is-dragging");
    try { session.handle.releasePointerCapture(session.pointerId); } catch { /* already gone */ }
  }

  function cleanup(session) {
    session.placeholder.remove();
    for (const prop of ["position", "left", "top", "width", "height", "margin", "transformOrigin", "translate"]) session.item.style[prop] = "";
    session.item.classList.remove("is-lifted", "is-settling");
    config.onSettle?.(session.item);
  }

  function abort() {
    const session = drag;
    drag = null;
    teardown(session);
    cleanup(session);
  }

  // the slot moves while auto scrolling
  function settle(session, velocity) {
    session.item.classList.remove("is-lifted");
    if (reducedMotion() || !session.item.isConnected) { cleanup(session); return; }
    session.item.classList.add("is-settling");
    const phys = fromRatio(SETTLE.ratio, SETTLE.stiffness);
    const start = performance.now();
    const goal = () => { const rect = session.placeholder.getBoundingClientRect(); return { x: rect.left - session.rect.left, y: rect.top - session.rect.top }; };
    const end = goal();
    const sx = new Spring(session.dx, phys);
    const sy = new Spring(session.dy, phys);
    sx.setTarget(end.x, start, settleVelocity(velocity.x, end.x - session.dx, SETTLE.stiffness));
    sy.setTarget(end.y, start, settleVelocity(velocity.y, end.y - session.dy, SETTLE.stiffness));
    settling = { frame: 0, done: () => cleanup(session) };
    const step = (now) => {
      if (!session.item.isConnected) { settling = null; cleanup(session); return; }
      const next = goal();
      if (Math.abs(next.x - sx.target) > 0.5) sx.setTarget(next.x, now);
      if (Math.abs(next.y - sy.target) > 0.5) sy.setTarget(next.y, now);
      const { value: vx } = sx.sample(now);
      const { value: vy } = sy.sample(now);
      session.item.style.translate = `${vx}px ${vy}px`;
      if (sx.atRest(now) && sy.atRest(now)) { settling = null; cleanup(session); return; }
      settling.frame = requestAnimationFrame(step);
    };
    settling.frame = requestAnimationFrame(step);
  }

  function release() {
    const session = drag;
    drag = null;
    teardown(session);
    const now = performance.now();
    const velocity = { x: session.vx.velocity(now), y: session.vy.velocity(now) };
    const spot = session.spot;
    if (!spot) { placeAtOrigin(session); settle(session, { x: 0, y: 0 }); return; }
    const from = session.origin;
    if (spot.list !== from.list || spot.index !== from.index) {
      // state first, the spring only follows
      moveNode(spot.list, session.item, session.placeholder);
      spot.list.insertBefore(session.placeholder, session.item.nextSibling);
      config.drop({ key: config.key(session.item), item: session.item, from, to: spot });
    }
    settle(session, velocity);
  }

  function placeAtOrigin(session) {
    const touched = [...new Set([session.at.list, session.origin.list])].flatMap((each) => itemsOf(each, session.item));
    flip(touched, () => session.origin.list.insertBefore(session.placeholder, itemsOf(session.origin.list, session.item)[session.origin.index] || null));
  }

  function cancel() {
    const session = drag;
    drag = null;
    teardown(session);
    placeAtOrigin(session);
    settle(session, { x: 0, y: 0 });
  }

  document.addEventListener("pointerdown", (event) => {
    const handle = event.target instanceof Element ? event.target.closest(config.handle) : null;
    if (!handle || drag || !event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return;
    const item = handle.closest(config.item);
    if (!item || !item.parentElement) return;
    clearPending();
    pending = { handle, item, pointerId: event.pointerId, type: event.pointerType, x: event.clientX, y: event.clientY, t: performance.now(), timer: 0 };
    if (event.pointerType === "touch") pending.timer = setTimeout(() => { if (pending) lift(); }, 400);
  });

  document.addEventListener("pointermove", (event) => {
    if (pending && event.pointerId === pending.pointerId) {
      const verdict = startGesture({ pointerType: pending.type, dx: event.clientX - pending.x, dy: event.clientY - pending.y, elapsed: performance.now() - pending.t });
      if (verdict === "cancel") clearPending();
      else if (verdict === "lift" && pending.type !== "touch") lift();
    }
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.dx = event.clientX - drag.x;
    drag.dy = event.clientY - drag.y;
    drag.vx.add(event.clientX, event.timeStamp);
    drag.vy.add(event.clientY, event.timeStamp);
    drag.item.style.translate = `${drag.dx}px ${drag.dy}px`;
  });

  document.addEventListener("pointerup", (event) => {
    if (pending && event.pointerId === pending.pointerId) clearPending();
    if (drag && event.pointerId === drag.pointerId) release();
  });

  document.addEventListener("pointercancel", (event) => {
    if (pending && event.pointerId === pending.pointerId) clearPending();
    if (drag && event.pointerId === drag.pointerId) cancel();
  });

  // no pointerup will ever come
  document.addEventListener("lostpointercapture", (event) => { if (drag && event.pointerId === drag.pointerId) cancel(); }, true);
  window.addEventListener("blur", () => { clearPending(); if (drag) cancel(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) return; clearPending(); if (drag) cancel(); });

  document.addEventListener("keydown", (event) => {
    if (!drag || event.key !== "Escape") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    cancel();
  }, true);

  // android long-press fires contextmenu too
  document.addEventListener("contextmenu", (event) => {
    if (!drag && !(pending && pending.type === "touch")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  // page must not scroll mid drag
  document.addEventListener("touchmove", (event) => { if (drag) event.preventDefault(); }, { passive: false });

  return { get active() { return Boolean(drag || pending); } };
}
