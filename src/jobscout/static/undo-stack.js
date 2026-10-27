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
    const [entry] = this.entries.splice(index, 1);
    const keys = new Set(entry.keys || []);
    const dropped = this.entries.slice(index).filter((later) => (later.keys || []).some((key) => keys.has(key)));
    this.entries = this.entries.filter((later) => !dropped.includes(later));
    return { entry, dropped };
  }

  has(id) { return this.entries.some((entry) => entry.id === id); }
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
