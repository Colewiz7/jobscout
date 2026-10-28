// one 409 rolls back the batch
export function splitEligible(jobs, check) {
  const groups = { eligible: [], blocked: [], unknown: [] };
  for (const job of jobs) groups[check(job)].push(job);
  return groups;
}

// dont queue on a guess
export function eligibilityVerdict(entry, enabled) {
  if (!enabled) return "eligible"; // server skips the check too
  if (!entry || entry.loading || entry.error || !entry.value) return "unknown";
  return entry.value.verdict === "do_not_apply" ? "blocked" : "eligible";
}

// profile saves clear the cache mid-request
export function loadLatest(cache, key, fetch, force = false, hops = 2) {
  const entry = cache.get(key);
  if (entry && !force) return entry.pending || Promise.resolve(false);
  const request = { loading: true, value: null, error: "" };
  request.pending = fetch(key).then((result) => {
    if (cache.get(key) !== request) return hops > 0 && loadLatest(cache, key, fetch, false, hops - 1);
    cache.set(key, result);
    return true;
  });
  cache.set(key, request);
  return request.pending;
}

// applying counts as tracked
const DISMISSABLE = new Set(["new", "saved", "queued"]);

export function companyDismissTargets(jobs, selectedKeys) {
  const companies = new Set(jobs.filter((job) => selectedKeys.has(job.dedupe_key)).map((job) => job.company));
  return { companies: [...companies], targets: jobs.filter((job) => companies.has(job.company) && DISMISSABLE.has(job.status)) };
}

export function companyLabel(companies) {
  const names = companies.slice(0, 3);
  if (companies.length > 3) return `${names.join(", ")} and ${new Intl.NumberFormat().format(companies.length - 3)} more`;
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names.join("");
}

export function visibleSelection(selectedKeys, jobs) {
  return pruneKeys(selectedKeys, jobs.map((job) => job.dedupe_key));
}

export function pruneKeys(selectedKeys, keys) {
  const visible = new Set(keys);
  return new Set([...selectedKeys].filter((key) => visible.has(key)));
}

export function toggleKey(selectedKeys, key) {
  const next = new Set(selectedKeys);
  if (next.has(key)) next.delete(key); else next.add(key);
  return next;
}

// shrinks when the focus walks back
export function rangeSelection(keys, base, anchorKey, focusKey) {
  const from = keys.indexOf(anchorKey);
  const to = keys.indexOf(focusKey);
  const next = new Set(base);
  if (from < 0 || to < 0) return next;
  for (const key of keys.slice(Math.min(from, to), Math.max(from, to) + 1)) next.add(key);
  return next;
}

export function selectAllVisible(selectedKeys, keys) {
  return new Set([...selectedKeys, ...keys]);
}

export function stepKey(keys, key, delta) {
  if (!keys.length) return "";
  const index = keys.indexOf(key);
  if (index < 0) return keys[delta < 0 ? keys.length - 1 : 0];
  return keys[Math.max(0, Math.min(keys.length - 1, index + delta))];
}

// keeps the picked jobs in order
export function moveGroup(keys, picked, edge) {
  const chosen = keys.filter((key) => picked.has(key));
  const rest = keys.filter((key) => !picked.has(key));
  return edge === "top" ? [...chosen, ...rest] : [...rest, ...chosen];
}

// later actions fold first
export function fitActions(widths, available, moreWidth, gap = 0) {
  const total = (count) => widths.slice(0, count).reduce((sum, width) => sum + width, 0) + gap * Math.max(0, count - 1);
  if (total(widths.length) <= available) return widths.length;
  let count = widths.length - 1;
  while (count > 0 && total(count) + gap + moreWidth > available) count -= 1;
  return count;
}

// later moves keep their places
export function restoreSlots(previous, current, restored) {
  const order = current.filter((key) => !restored.has(key));
  previous.forEach((key, index) => {
    if (!restored.has(key)) return;
    const before = previous.slice(0, index).reverse().find((other) => order.includes(other));
    if (before) { order.splice(order.indexOf(before) + 1, 0, key); return; }
    const after = previous.slice(index + 1).find((other) => order.includes(other));
    order.splice(after ? order.indexOf(after) : order.length, 0, key);
  });
  return order;
}
