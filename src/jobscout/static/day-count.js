// local days match how people count
const WAITING = new Set(["applied", "interviewing"]);
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

function localDay(value) {
  const only = typeof value === "string" ? DATE_ONLY.exec(value) : null;
  const date = only ? new Date(Number(only[1]), Number(only[2]) - 1, Number(only[3])) : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  // utc math skips dst hour shifts
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
}

export function dayCount(status, appliedAt, now = new Date()) {
  if (!WAITING.has(status) || !appliedAt) return null;
  const start = localDay(appliedAt);
  const today = localDay(now);
  if (start === null || today === null) return null;
  return Math.max(0, Math.round((today - start) / 86400000));
}

export function dayCountLabel(days) {
  if (days === 0) return "Applied today";
  return `Applied ${new Intl.NumberFormat().format(days)} ${days === 1 ? "day" : "days"} ago`;
}

export function dayCountMarkup(status, appliedAt, now = new Date()) {
  const days = dayCount(status, appliedAt, now);
  if (days === null) return "";
  return `<span class="day-count" aria-hidden="true">Day ${new Intl.NumberFormat().format(days)}</span><span class="visually-hidden">${dayCountLabel(days)}</span>`;
}
