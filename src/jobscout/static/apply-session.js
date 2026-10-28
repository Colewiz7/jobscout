// server time would skew the clock
export function sessionFromServer(row, now = Date.now()) {
  const elapsed = Math.max(0, Number(row.elapsed_seconds) || 0) * 1000;
  return {
    id: row.id, revision: row.revision, keys: [...row.keys],
    applied: [...(row.applied || [])], skipped: (row.skipped || []).map((item) => ({ ...item })),
    startedAt: now - elapsed, endedAt: row.ended_at ? now : null,
  };
}

export function sessionLists(session) {
  return { applied: [...session.applied], skipped: session.skipped.map((item) => ({ ...item })), ended: Boolean(session.endedAt) };
}

export function recordedAs(session, key) {
  if (session.applied.includes(key)) return "applied";
  return session.skipped.some((item) => item.dedupe_key === key) ? "skipped" : "";
}

// a job sits in one list
export function withRecord(lists, key, kind, reason = "") {
  const applied = lists.applied.filter((item) => item !== key);
  const skipped = lists.skipped.filter((item) => item.dedupe_key !== key);
  if (kind === "applied") applied.push(key);
  else skipped.push({ dedupe_key: key, reason });
  return { ...lists, applied, skipped };
}

// the last job ends the session
export function stepAfter(session, index) {
  return { index: index + 1, ended: index + 1 >= session.keys.length };
}

export function sessionPatch(lists) {
  return { applied: lists.applied, skipped: lists.skipped, ended: lists.ended };
}

export function routeIndex(session, key) {
  const index = session.keys.indexOf(key);
  return index < 0 ? 0 : index;
}

// skip never buries an application
export function keepsNewer(session, key, kind) {
  const recorded = recordedAs(session, key);
  return Boolean(recorded) && !(kind === "applied" && recorded === "skipped");
}

// a 409 adopts the newer session first
export async function saveStep({ session, key, kind, change, write, latest }) {
  let before = sessionLists(session);
  try {
    await write(session, change(before), session.revision);
    return { saved: true, before };
  } catch (error) {
    if (!error?.conflict) return { saved: false, before, error };
  }
  const newer = latest();
  // their end wins
  if (newer.endedAt) return { saved: false, before: sessionLists(newer), ended: true };
  if (keepsNewer(newer, key, kind)) return { saved: false, before: sessionLists(newer), kept: recordedAs(newer, key) };
  before = sessionLists(newer);
  try {
    await write(newer, retried(before, change(before), key), newer.revision);
    return { saved: true, before };
  } catch (error) {
    return { saved: false, before, error };
  }
}

// a retry only touches its own job
export function retried(before, wanted, key) {
  const mine = (item) => (typeof item === "string" ? item : item.dedupe_key) === key;
  return {
    applied: [...before.applied.filter((item) => !mine(item)), ...wanted.applied.filter(mine)],
    skipped: [...before.skipped.filter((item) => !mine(item)), ...wanted.skipped.filter(mine)],
    ended: before.ended || wanted.ended,
  };
}

// a saved job needs undo
export function stepUndo(outcome, jobChanged) {
  if (outcome.saved) return "session";
  if (jobChanged && !outcome.kept && !outcome.ended && !outcome.error?.conflict) return "job";
  return "";
}

// job is real, session best effort
export async function undoUnsaved({ revertJob, saveSession }) {
  try {
    await revertJob();
  } catch (error) {
    return { undone: false, error };
  }
  try {
    await saveSession();
    return { undone: true };
  } catch (error) {
    return { undone: true, sessionError: error };
  }
}

// snapshot revision, so newer wins
export async function restoreUnsaved({ session, before, revision, key, write }) {
  try {
    await write(session, before, revision);
  } catch (error) {
    // offline, so drop only ours
    if (!error?.conflict) {
      session.applied = session.applied.filter((item) => item !== key);
      session.skipped = session.skipped.filter((item) => item.dedupe_key !== key);
      if (!before.ended && session.revision === revision) session.endedAt = null;
    }
    throw error;
  }
}

// session first, so a conflict reverts nothing
export async function undoStep({ saveSession, revertJob = null }) {
  try {
    await saveSession();
  } catch (error) {
    return { undone: false, error };
  }
  if (!revertJob) return { undone: true };
  try {
    await revertJob();
    return { undone: true };
  } catch (error) {
    return { undone: true, jobError: error };
  }
}

// older reads lose to a save
export function staleRead(held, row) {
  return !row || row.id !== held.id || row.revision <= held.revision;
}
