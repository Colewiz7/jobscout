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
