// mirrors the server's clean_tag
// an emptied list leaves no job to show
export function paneJob(jobs, visibleCount, key) {
  return visibleCount ? jobs.find((job) => job.dedupe_key === key) : undefined;
}

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

// empty list still skips closing
export function nextStepPayload(value, expected = null, reopen = null) {
  return { next_step: value, ...(expected === null ? {} : { expected_next_step: expected }), ...(reopen ? { reopen_follow_ups: reopen } : {}) };
}
