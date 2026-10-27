// a resend of old values is noise
export function changedFields(original, draft) {
  const plain = (value) => value == null ? "" : typeof value === "string" ? value.trim() : value;
  const changes = {};
  for (const [name, value] of Object.entries(draft)) {
    if (plain(value) !== plain(original[name])) changes[name] = typeof value === "string" ? value.trim() : value;
  }
  return changes;
}

export function pickFields(record, names) {
  return Object.fromEntries(names.map((name) => [name, record[name] ?? null]));
}

// datetime-local has no zone
export function toLocalInput(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.valueOf())) return "";
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fromLocalInput(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? undefined : date.toISOString();
}

// cancelled ones keep their place
export function splitInterviews(interviews, now = new Date()) {
  const time = (item) => new Date(item.starts_at).valueOf();
  const upcoming = interviews.filter((item) => time(item) >= now.valueOf()).sort((a, b) => time(a) - time(b) || a.id - b.id);
  const past = interviews.filter((item) => time(item) < now.valueOf()).sort((a, b) => time(b) - time(a) || b.id - a.id);
  return { upcoming, past, scheduled: upcoming.filter((item) => !item.cancelled_at).length };
}
