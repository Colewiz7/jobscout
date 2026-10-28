// mirrors the server's clean_tag
export function cleanTag(value) {
  return String(value ?? "").split(/\s+/).filter(Boolean).join(" ");
}

const fold = (value) => value.toLocaleLowerCase();

export function hasTag(job, tag) {
  return Boolean(tag) && (job.tags || []).some((item) => fold(item) === fold(tag));
}

// one entry per tag, any case
export function tagOptions(jobs) {
  const seen = new Map();
  for (const job of jobs) for (const tag of job.tags || []) if (!seen.has(fold(tag))) seen.set(fold(tag), tag);
  return [...seen.values()].sort((left, right) => left.localeCompare(right));
}

// only hand tags can come off
export function removableTags(jobs) {
  const counts = new Map();
  for (const job of jobs) {
    for (const tag of job.manual_tags || []) {
      const entry = counts.get(fold(tag)) || { tag, count: 0 };
      entry.count += 1;
      counts.set(fold(tag), entry);
    }
  }
  return [...counts.values()].sort((left, right) => left.tag.localeCompare(right.tag));
}

// optimistic copy of the server merge
export function withManualTag(job, tag, remove) {
  const manual = (job.manual_tags || []).filter((item) => fold(item) !== fold(tag));
  if (!remove) manual.push(tag);
  const rules = job.rule_tags || [];
  const ruled = new Set(rules.map(fold));
  return { manual_tags: manual, tags: [...rules, ...manual.filter((item) => !ruled.has(fold(item)))] };
}

export function moveId(ids, id, to) {
  const order = ids.filter((item) => item !== id);
  order.splice(Math.max(0, Math.min(to, order.length)), 0, id);
  return order;
}

export const RULE_KINDS = [
  ["archive_title", "Archive title"],
  ["tag_title", "Tag title"],
  ["boost_company", "Boost company"],
];

// what the value field means
export function ruleValueHelp(kind) {
  if (kind === "tag_title") return "The tag to add, up to 40 characters.";
  if (kind === "boost_company") return "Ranking points to add, from 1 to 100.";
  return "Archive rules ignore this.";
}

// undo sends the old state back
export function reminderUndo(action, previous) {
  if (action === "done") return { action: "reopen" };
  if (action === "reopen") return { action: "done" };
  if (previous.status === "snoozed" && previous.snoozed_until) return { action: "snooze", until: previous.snoozed_until };
  return { action: "unsnooze" };
}

function at(now, days, hour = 9) {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + days, hour);
}

// snoozes land at 9 am
export function snoozeChoices(now = new Date()) {
  const toMonday = ((8 - now.getDay()) % 7) || 7;
  return [
    { key: "tomorrow", label: "Tomorrow", until: at(now, 1) },
    { key: "three-days", label: "In 3 days", until: at(now, 3) },
    { key: "next-week", label: "Next week", until: at(now, toMonday) },
  ];
}

export function dateInputValue(date) {
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// a picked day means 9 am
export function pickedDay(value, now = new Date()) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return { error: "Pick a date." };
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 9);
  if (Number.isNaN(date.valueOf()) || date.getDate() !== Number(match[3])) return { error: "Pick a real date." };
  if (date < at(now, 1, 0)) return { error: "Pick a day after today." };
  if (date - now > 365 * 864e5) return { error: "Pick a day within a year." };
  return { until: date };
}

// formatDate only looks back
export function upcomingDay(value, now = new Date(), locale = undefined) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  const days = Math.round((new Date(date.getFullYear(), date.getMonth(), date.getDate()) - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 864e5);
  if (days <= 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days < 7) return new Intl.DateTimeFormat(locale, { weekday: "long" }).format(date);
  return new Intl.DateTimeFormat(locale, { month: "short", day: "numeric", ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }) }).format(date);
}
