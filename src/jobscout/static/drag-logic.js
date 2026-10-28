// dom free for node tests

export const LIFT_DISTANCE = 4;
export const LONG_PRESS = 400;
export const TOUCH_SLOP = 10;
export const EDGE = 48;
export const EDGE_SPEED = 1200;

// the dragged card would count itself
export function slotIndex(siblingRects, centreY) {
  let index = 0;
  for (const rect of siblingRects) if (centreY > rect.top + rect.height / 2) index += 1;
  return index;
}

export function moveKey(keys, key, to) {
  const rest = keys.filter((item) => item !== key);
  if (rest.length === keys.length) return [...keys];
  rest.splice(Math.max(0, Math.min(rest.length, to)), 0, key);
  return rest;
}

export function keyboardTarget(index, count, key) {
  if (key === "ArrowUp") return Math.max(0, index - 1);
  if (key === "ArrowDown") return Math.min(count - 1, index + 1);
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

// row lists step left and right
export function axisKey(key, horizontal) {
  if (!horizontal) return key;
  return { ArrowLeft: "ArrowUp", ArrowRight: "ArrowDown", Home: "Home", End: "End" }[key] || null;
}

// the queue may change before undo
export function restoreOrder(previous, current) {
  const live = new Set(current);
  const result = previous.filter((key) => live.has(key));
  const kept = new Set(result);
  current.forEach((key, index) => { if (!kept.has(key)) result.splice(Math.min(index, result.length), 0, key); });
  return result;
}

export function startGesture({ pointerType, dx, dy, elapsed }) {
  const distance = Math.hypot(dx, dy);
  if (pointerType !== "touch") return distance >= LIFT_DISTANCE ? "lift" : "pending";
  if (distance > TOUCH_SLOP) return "cancel"; // its a scroll
  return elapsed >= LONG_PRESS ? "lift" : "pending";
}

export function autoScrollSpeed(distance) {
  if (distance >= EDGE) return 0;
  return EDGE_SPEED * ((EDGE - Math.max(0, distance)) / EDGE) ** 2;
}

// carrel wants 60 and 120hz equal
export const autoScrollStep = (speed, dt) => speed * dt / 1000;

// full clamp overshoots carrel springs 1.1%
export function settleVelocity(v, distance, stiffness) {
  const max = 0.5 * Math.sqrt(stiffness) * Math.abs(distance);
  return Math.sign(v) * Math.min(Math.abs(v), max);
}

// column gaps shouldnt drop the target
export function columnAt(columnRects, x) {
  if (!columnRects.length || x < columnRects[0].left || x > columnRects.at(-1).right) return -1;
  let best = 0;
  let bestDistance = Infinity;
  columnRects.forEach((rect, index) => {
    const distance = x < rect.left ? rect.left - x : x > rect.right ? x - rect.right : 0;
    if (distance < bestDistance) { best = index; bestDistance = distance; }
  });
  return best;
}

export const BOARD_STATUSES = ["applying", "applied", "interviewing", "offer", "rejected"];

// status changes null the position
export function manualCompare(left, right) {
  const rank = (app) => { const index = BOARD_STATUSES.indexOf(app.status); return index < 0 ? BOARD_STATUSES.length : index; };
  const place = (app) => Number.isInteger(app.board_position) ? app.board_position : Infinity;
  return rank(left) - rank(right) || place(left) - place(right)
    || new Date(right.application_updated_at || 0) - new Date(left.application_updated_at || 0)
    || String(left.dedupe_key).localeCompare(String(right.dedupe_key));
}

export function trackerCompare(sort) {
  const time = (value) => new Date(value || 0).valueOf();
  if (sort === "manual") return manualCompare;
  if (sort === "company") return (left, right) => `${left.company} ${left.title}`.localeCompare(`${right.company} ${right.title}`);
  if (sort === "status") return (left, right) => `${left.status} ${left.company}`.localeCompare(`${right.status} ${right.company}`);
  if (sort === "applied") return (left, right) => time(right.applied_at) - time(left.applied_at);
  return (left, right) => time(right.application_updated_at) - time(left.application_updated_at);
}

// a status change bumps its timestamps
export function sortedInsertIndex(apps, moved, status, sort, now = new Date().toISOString()) {
  const landed = { ...moved, status, application_updated_at: now, applied_at: status === "applied" ? moved.applied_at || now : moved.applied_at };
  if (status !== moved.status) landed.board_position = null;
  const others = apps.filter((app) => app.dedupe_key !== moved.dedupe_key);
  return [...others, landed].sort(trackerCompare(sort)).indexOf(landed);
}

export const columnKeys = (apps, status, sort) => apps.filter((app) => app.status === status).sort(trackerCompare(sort)).map((app) => app.dedupe_key);

// search hides cards that still count
export function placeKey(keys, visible, key, index) {
  const rest = keys.filter((item) => item !== key);
  const shown = visible.filter((item) => item !== key && rest.includes(item));
  let at = rest.length;
  if (index < shown.length) at = rest.indexOf(shown[Math.max(0, index)]);
  else if (shown.length) at = rest.indexOf(shown.at(-1)) + 1;
  rest.splice(at, 0, key);
  return rest;
}

export const positionedKeys = (apps, status) => apps.filter((app) => app.status === status && Number.isInteger(app.board_position)).sort((left, right) => left.board_position - right.board_position).map((app) => app.dedupe_key);

// undo itself bumps activity time
export function boardUndoOrders(apps, key, from, to, sort) {
  const writes = [];
  if (BOARD_STATUSES.includes(from)) writes.push({ status: from, keys: sort === "manual" ? columnKeys(apps, from, "manual") : positionedKeys(apps, from) });
  if (BOARD_STATUSES.includes(to) && to !== from) writes.push({ status: to, keys: positionedKeys(apps, to).filter((item) => item !== key) });
  return writes;
}

export const liftAnnouncement = (index, count) => `Picked up, position ${index + 1} of ${count}. Use the arrow keys to move, Space to drop, Escape to cancel.`;
export const moveAnnouncement = (index, count) => `Moved to position ${index + 1} of ${count}.`;
export const cancelAnnouncement = (index, count) => `Reorder cancelled, back at position ${index + 1} of ${count}.`;

// a landed write outdates older reads
export function createLoadGate() {
  let latest = 0;
  const running = new Set();
  return {
    start() { latest += 1; running.add(latest); return latest; },
    current(token) { return token === latest; },
    finish(token) { running.delete(token); },
    // true when a read needs redoing
    invalidate() { latest += 1; return running.size > 0; },
  };
}

// parallel saves could land reversed
export function createOrderSaver({ send, confirmed = [], onSaved = () => {}, onLanded = () => {}, onRevert = () => {} }) {
  let last = [...confirmed];
  let pending = null;
  let wanted = null;
  let waiting = [];
  let flight = null;
  const settle = (list, error) => list.forEach((resolve) => resolve(error));
  const pump = async () => {
    while (pending) {
      const order = pending;
      const riders = waiting;
      pending = null;
      waiting = [];
      try {
        await send(order);
        last = order;
        onLanded(order);
        if (!pending) onSaved(order);
        settle(riders, null);
      } catch (error) {
        // moves after a failure are stale
        const dropped = waiting;
        pending = null;
        waiting = [];
        wanted = null;
        onRevert(last, error);
        settle([...riders, ...dropped], error);
      }
    }
    flight = null;
    wanted = null;
  };
  return {
    // resolves to the error or null
    save(order) {
      pending = [...order];
      wanted = pending;
      const result = new Promise((resolve) => waiting.push(resolve));
      if (!flight) flight = pump();
      return result;
    },
    reset(order) { if (!flight) last = [...order]; },
    get busy() { return Boolean(flight); },
    get confirmed() { return [...last]; },
    // reloads lay this over server data
    get unsaved() { return wanted && [...wanted]; },
    // cards leave the column mid save
    prune(keep) {
      if (pending) pending = pending.filter(keep);
      if (wanted) wanted = pending || wanted.filter(keep);
    },
    idle() { return flight || Promise.resolve(); },
  };
}
