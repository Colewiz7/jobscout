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
