// unbounded undo would leak closures
export class UndoStack {
  constructor(limit = 20) {
    this.limit = limit;
    this.entries = [];
    this.seq = 0;
  }

  get size() { return this.entries.length; }

  push(entry) {
    const item = { ...entry, id: ++this.seq };
    this.entries.push(item);
    if (this.entries.length > this.limit) this.entries.shift();
    return item.id;
  }

  pop() { return this.entries.length ? this.take(this.entries.at(-1).id) : null; }

  // newer edits would restore stale state
  take(id) {
    const index = this.entries.findIndex((entry) => entry.id === id);
    if (index < 0) return null;
    const entry = this.entries[index];
    // field edits undo newest first
    if (entry.latest && this.entries.slice(index + 1).some((later) => (later.keys || []).includes(entry.latest))) {
      return { entry, dropped: [], blocked: true };
    }
    this.entries.splice(index, 1);
    const keys = new Set(entry.keys || []);
    const dropped = this.entries.slice(index).filter((later) => (later.keys || []).some((key) => keys.has(key)));
    this.entries = this.entries.filter((later) => !dropped.includes(later));
    return { entry, dropped };
  }

  has(id) { return this.entries.some((entry) => entry.id === id); }

  // failed saves leave nothing to undo
  remove(id) {
    const before = this.entries.length;
    this.entries = this.entries.filter((entry) => entry.id !== id);
    return this.entries.length < before;
  }
}

// typing settles into one entry
export class EditSessions {
  constructor() { this.open = new Map(); }

  begin(scope, value) { if (!this.open.has(scope)) this.open.set(scope, value); }

  has(scope) { return this.open.has(scope); }

  // the value it started from
  end(scope) {
    if (!this.open.has(scope)) return undefined;
    const before = this.open.get(scope);
    this.open.delete(scope);
    return before;
  }
}

// arrays compare by content
export function textChange(before, after) {
  if (before === undefined || JSON.stringify(before) === JSON.stringify(after)) return null;
  return { before, after };
}

// merges restore their own action
export function mergeSnackbar(item, next) {
  const step = { message: next.message, action: next.action, onAction: next.onAction, undoId: next.undoId };
  return { ...step, history: [...(item.history || []), step] };
}

// the newest move that still undoes
export function withdrawSnackbar(item, undoId, undoable) {
  if (item.undoId !== undoId) return null;
  const fallback = (item.history || []).filter((step) => step.undoId !== undoId && undoable(step.undoId)).at(-1);
  return fallback ? { ...fallback } : { action: "", onAction: null, undoId: 0 };
}

export const SNACKBAR = { short: 4000, long: 6000, action: 10000, minResume: 2000, minShown: 2000 };

export function snackbarDuration(message, hasAction) {
  if (hasAction) return SNACKBAR.action;
  return message.length > 60 ? SNACKBAR.long : SNACKBAR.short;
}

// carrel lets a newcomer cut it short
export function exitDelay({ shownAt, resumedAt = -Infinity, deadline, queued, held }, now) {
  if (held) return -1;
  const natural = Math.max(0, deadline - now);
  if (!queued) return natural;
  const floor = Math.max(0, SNACKBAR.minShown - (now - shownAt), SNACKBAR.minResume - (now - resumedAt));
  return Math.min(natural, floor);
}

// hover, focus and hidden overlap
export class Countdown {
  constructor(duration) {
    this.remaining = duration;
    this.deadline = 0;
    this.holds = new Set();
    this.started = false;
    this.resumedAt = -Infinity;
  }

  get running() { return this.started && !this.holds.size; }

  start(now) {
    this.started = true;
    return this.holds.size ? -1 : this.arm(now);
  }

  hold(reason, now) {
    const wasRunning = this.running;
    this.holds.add(reason);
    if (wasRunning) this.remaining = Math.max(0, this.deadline - now);
    return wasRunning;
  }

  release(reason, now) {
    if (!this.holds.delete(reason) || !this.running) return -1;
    this.resumedAt = now;
    return this.arm(now);
  }

  arm(now) {
    const wait = Math.max(SNACKBAR.minResume, this.remaining);
    this.deadline = now + wait;
    return wait;
  }
}
