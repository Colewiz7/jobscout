"use strict";

const routeView = document.querySelector("#route-view");
const commandDialog = document.querySelector("#command-dialog");
const commandInput = document.querySelector("#command-input");
const commandResults = document.querySelector("#command-results");
const shortcutDialog = document.querySelector("#shortcut-dialog");
const filterDialog = document.querySelector("#filter-dialog");
const filterForm = document.querySelector("#filter-form");
const sourceOptions = document.querySelector("#source-options");
const remoteFilter = document.querySelector("#remote-filter");
const savedViewName = document.querySelector("#saved-view-name");
const snackbarRegion = document.querySelector("#snackbar-region");
const assertiveRegion = document.querySelector("#assertive-region");
const quickFillTrigger = document.querySelector("#quick-fill-trigger");
const quickFillSheet = document.querySelector("#quick-fill-sheet");

const ROW_HEIGHT = 72;
const DIVIDER_HEIGHT = 32;
const LAST_VISIT_KEY = "jobseer.inboxLastVisit";
const allowedStatuses = new Set(["all", "new", "saved", "queued", "applying", "applied", "interviewing", "offer", "rejected", "archived"]);
const allowedSorts = new Set(["score", "newest", "company"]);

const icons = {
  inbox: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4zM4 14h5l2 2h2l2-2h5"/></svg>',
  queue: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5h12M6 12h12M6 19h8"/></svg>',
  tracker: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19V9m7 10V4m7 15v-7"/></svg>',
  companies: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V8l8-4v16m0-9h8v9M8 9h1m-1 4h1m-1 4h1m8-2h1"/></svg>',
  profile: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M5 21a7 7 0 0 1 14 0"/></svg>',
  shortcuts: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.8 9a2.4 2.4 0 0 1 4.7.7c0 2.3-2.5 2.3-2.5 4.3m0 3h.01"/></svg>',
  search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m15.5 15.5 4 4"/></svg>',
  filter: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M7 12h10m-7 6h4"/></svg>',
  close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>',
  external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5m0-5-9 9M19 14v5H5V5h5"/></svg>',
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>',
};

const pages = {
  queue: ["Queue", "Apply with a clear desk.", "Queued roles will be ordered for focused, one-at-a-time application sessions.", "Find roles", "/inbox"],
  tracker: ["Tracker", "Keep the next step visible.", "Applications, follow-ups, interviews, and outcomes will stay together here.", "Open inbox", "/inbox"],
  companies: ["Companies", "Remember every conversation.", "Company pages will collect roles, applications, contacts, notes, and account details.", "Open inbox", "/inbox"],
  profile: ["Profile", "Write it once. Use it calmly.", "Your fields, documents, answer templates, rules, and notification settings will live here.", "Show shortcuts", "shortcuts"],
};

const commands = [
  { id: "inbox", label: "Go to Inbox", detail: "Triage new roles", route: "/inbox", shortcut: "G I" },
  { id: "queue", label: "Go to Queue", detail: "Apply one job at a time", route: "/queue", shortcut: "G Q" },
  { id: "tracker", label: "Go to Tracker", detail: "Follow up and learn", route: "/tracker", shortcut: "G T" },
  { id: "companies", label: "Go to Companies", detail: "Open company history", route: "/companies", shortcut: "G C" },
  { id: "profile", label: "Go to Profile", detail: "Manage reusable application data", route: "/profile", shortcut: "G P" },
  { id: "shortcuts", label: "Show keyboard shortcuts", detail: "Review the power-user map", action: "shortcuts", shortcut: "?" },
];

const state = {
  jobs: [], loaded: false, loading: false, error: "", csrf: "", refreshedAt: null,
  query: "", status: "new", sort: "score", source: "", remote: false, focus: false,
  selectedKey: "", selectedKeys: new Set(), rangeAnchor: -1, scrollTop: 0,
  entries: [], totalHeight: 0, lastVisit: readStoredDate(LAST_VISIT_KEY), savedViews: [],
  pending: new Set(), undo: null, notesTimer: null, skeletonAt: 0, descriptions: new Map(),
  quickFillEnabled: false, quickFillOpen: false, quickFillProfile: null,
  quickFillError: "", quickFillQuery: "", quickFillItems: [], atsOrdering: {},
  copiedByJob: new Map(), copiedKey: "", quickFillPopout: null,
  quickFillEdit: false, quickFillSaveState: "", quickFillSaveTimer: null,
  quickFillSaveVersion: 0, quickFillSavedProfile: null,
};

let commandSelection = 0;
let commandMatches = commands;
let dialogTrigger = null;
let goChordUntil = 0;
let loadingTimer = null;
let snackbarTimer = null;
let snackbarDeadline = 0;
let snackbarRemaining = 0;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function readStoredDate(key) {
  try {
    const raw = localStorage.getItem(key);
    const value = raw ? new Date(raw) : null;
    return value && !Number.isNaN(value.valueOf()) ? value : null;
  } catch { return null; }
}

function routeRoot(pathname = window.location.pathname) {
  return pathname.split("/").filter(Boolean)[0] || "inbox";
}

function selectedKeyFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts[0] !== "inbox" || parts.length < 2) return "";
  try { return decodeURIComponent(parts.slice(1).join("/")); } catch { return ""; }
}

function parseInboxUrl() {
  const params = new URLSearchParams(window.location.search);
  state.query = params.get("q") || "";
  state.status = allowedStatuses.has(params.get("status")) ? params.get("status") : "new";
  state.sort = allowedSorts.has(params.get("sort")) ? params.get("sort") : "score";
  state.source = params.get("source") || "";
  state.remote = params.get("remote") === "1";
  state.focus = params.get("focus") === "1";
  state.selectedKey = selectedKeyFromPath();
}

function inboxUrl(selectedKey = state.selectedKey) {
  const path = selectedKey ? `/inbox/${encodeURIComponent(selectedKey)}` : "/inbox";
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.status !== "new") params.set("status", state.status);
  if (state.sort !== "score") params.set("sort", state.sort);
  if (state.source) params.set("source", state.source);
  if (state.remote) params.set("remote", "1");
  if (state.focus) params.set("focus", "1");
  return `${path}${params.size ? `?${params}` : ""}`;
}

function syncInboxUrl({ replace = false } = {}) {
  history.replaceState({ ...(history.state || {}), inboxScroll: state.scrollTop }, "", window.location.href);
  history[replace ? "replaceState" : "pushState"]({ inboxScroll: state.scrollTop }, "", inboxUrl());
}

function navigate(url, { replace = false } = {}) {
  const destination = new URL(url, window.location.origin);
  if (destination.origin !== window.location.origin) return;
  if (routeRoot() === "inbox") history.replaceState({ ...(history.state || {}), inboxScroll: state.scrollTop }, "", window.location.href);
  history[replace ? "replaceState" : "pushState"]({}, "", `${destination.pathname}${destination.search}${destination.hash}`);
  renderRoute({ focus: true });
}

function fuzzyScore(candidate, query) {
  const text = candidate.toLowerCase();
  const needle = query.trim().toLowerCase();
  if (!needle) return 1;
  let score = 0;
  let position = -1;
  for (const character of needle) {
    const next = text.indexOf(character, position + 1);
    if (next === -1) return -1;
    score += next === position + 1 ? 3 : 1;
    position = next;
  }
  return score - position / 100;
}

function statusLabel(status) {
  return `${status.slice(0, 1).toUpperCase()}${status.slice(1)}`;
}

function sourceList(job) {
  return String(job.sources || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function isRemote(job) { return /\bremote\b/i.test(job.location || ""); }

function isNewSinceVisit(job) {
  if (!state.lastVisit || !job.first_seen) return false;
  const firstSeen = new Date(job.first_seen);
  return !Number.isNaN(firstSeen.valueOf()) && firstSeen > state.lastVisit;
}

function formatDate(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.valueOf())) return "";
  const now = new Date();
  const days = Math.floor((now - date) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${new Intl.NumberFormat().format(days)}d ago`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }) }).format(date);
}

function formatAbsolute(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function statusCounts() {
  const counts = { all: state.jobs.length };
  for (const job of state.jobs) counts[job.status] = (counts[job.status] || 0) + 1;
  return counts;
}

function filteredJobs() {
  const query = state.query.trim();
  const matches = state.jobs.map((job) => ({
    job,
    fuzzy: fuzzyScore([job.title, job.company, job.location, job.terms, job.sources].filter(Boolean).join(" "), query),
  })).filter(({ job, fuzzy }) => fuzzy >= 0
    && (state.status === "all" || job.status === state.status)
    && (!state.source || sourceList(job).includes(state.source))
    && (!state.remote || isRemote(job)));
  matches.sort((left, right) => {
    if (query && right.fuzzy !== left.fuzzy) return right.fuzzy - left.fuzzy;
    if (state.sort === "company") return `${left.job.company} ${left.job.title}`.localeCompare(`${right.job.company} ${right.job.title}`);
    if (state.sort === "newest") return new Date(right.job.first_seen || 0) - new Date(left.job.first_seen || 0);
    return (Number(right.job.score) || 0) - (Number(left.job.score) || 0)
      || (Number(left.job.age_days) || 0) - (Number(right.job.age_days) || 0);
  });
  const jobs = matches.map(({ job }) => job);
  const fresh = jobs.filter(isNewSinceVisit);
  return fresh.length ? [...fresh, ...jobs.filter((job) => !isNewSinceVisit(job))] : jobs;
}

function buildEntries(jobs) {
  const entries = [];
  let offset = 0;
  const freshCount = jobs.filter(isNewSinceVisit).length;
  if (freshCount) {
    entries.push({ type: "divider", offset, height: DIVIDER_HEIGHT, count: freshCount });
    offset += DIVIDER_HEIGHT;
  }
  jobs.forEach((job, index) => {
    if (freshCount && index === freshCount) {
      entries.push({ type: "seen-divider", offset, height: DIVIDER_HEIGHT });
      offset += DIVIDER_HEIGHT;
    }
    entries.push({ type: "job", job, jobIndex: index, offset, height: ROW_HEIGHT });
    offset += ROW_HEIGHT;
  });
  state.entries = entries;
  state.totalHeight = offset;
}

function visibleStatusTabs() {
  const counts = statusCounts();
  const tabs = [["new", "New"], ["saved", "Saved"], ["queued", "Queued"], ["applied", "Applied"], ["all", "All"]];
  return tabs.map(([value, label]) => `<button class="status-tab interactive" type="button" role="tab" data-status-tab="${value}" aria-selected="${state.status === value}" tabindex="${state.status === value ? "0" : "-1"}"><span>${label}</span><span class="count">${new Intl.NumberFormat().format(counts[value] || 0)}</span></button>`).join("");
}

function activeFilterChips() {
  const chips = [];
  if (state.query) chips.push(["query", `Search: ${state.query}`]);
  if (state.source) chips.push(["source", state.source]);
  if (state.remote) chips.push(["remote", "Remote"]);
  return chips.map(([key, label]) => `<button class="filter-chip interactive" type="button" data-clear-filter="${key}" aria-label="Remove ${escapeHtml(label)} filter"><span>${escapeHtml(label)}</span>${icons.close}</button>`).join("");
}

function savedViewsMarkup() {
  if (!state.savedViews.length) return "";
  return `<div class="saved-views" aria-label="Saved views"><span>Views</span>${state.savedViews.map((view, index) => `<span class="saved-view-wrap"><button class="saved-view interactive" type="button" data-saved-view="${index}">${escapeHtml(view.name)}</button><button class="saved-view-delete interactive" type="button" data-delete-saved-view="${view.id}" aria-label="Delete ${escapeHtml(view.name)} saved view">${icons.close}</button></span>`).join("")}</div>`;
}

function initials(company) {
  return String(company || "Job").split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function jobRowMarkup(entry) {
  const job = entry.job;
  const sources = sourceList(job);
  const selected = job.dedupe_key === state.selectedKey;
  const bulkSelected = state.selectedKeys.has(job.dedupe_key);
  const fresh = isNewSinceVisit(job);
  const chips = [];
  if (isRemote(job)) chips.push("Remote");
  if (sources.length > 1) chips.push(`${sources.length} sources`);
  const location = job.location ? `<span class="row-location">${escapeHtml(job.location)}</span>` : "";
  return `<button class="job-row interactive" type="button" role="option" style="transform:translateY(${entry.offset}px)" data-job-key="${escapeHtml(job.dedupe_key)}" data-job-index="${entry.jobIndex}" aria-selected="${selected || bulkSelected}" tabindex="${selected ? "0" : "-1"}">
    <span class="job-logo" aria-hidden="true">${escapeHtml(initials(job.company))}</span>
    <span class="job-row-copy"><span class="job-row-title">${fresh ? '<span class="unread-dot" aria-label="Unread"></span>' : ""}${escapeHtml(job.title)}</span><span class="job-row-meta"><span>${escapeHtml(job.company)}</span>${location}</span></span>
    <span class="job-row-end">${chips.slice(0, 2).map((chip) => `<span class="row-chip">${escapeHtml(chip)}</span>`).join("")}${job.first_seen ? `<time datetime="${escapeHtml(job.first_seen)}" title="${escapeHtml(formatAbsolute(job.first_seen))}">${escapeHtml(formatDate(job.first_seen))}</time>` : ""}</span>
    ${bulkSelected ? '<span class="selection-check" aria-label="Selected">✓</span>' : ""}
  </button>`;
}

function renderVirtualRows() {
  const viewport = document.querySelector("#job-viewport");
  const layer = document.querySelector("#job-list-layer");
  if (!viewport || !layer) return;
  const top = viewport.scrollTop;
  const bottom = top + viewport.clientHeight;
  const visible = state.entries.filter((entry) => entry.offset + entry.height >= top - ROW_HEIGHT * 4 && entry.offset <= bottom + ROW_HEIGHT * 4);
  layer.style.height = `${state.totalHeight}px`;
  layer.innerHTML = visible.map((entry) => {
    if (entry.type === "divider") return `<div class="new-divider" style="transform:translateY(${entry.offset}px)"><span>New since last visit</span><span>${entry.count}</span></div>`;
    if (entry.type === "seen-divider") return `<div class="new-divider seen" style="transform:translateY(${entry.offset}px)"><span>Seen earlier</span></div>`;
    return jobRowMarkup(entry);
  }).join("");
}

function emptyListMarkup() {
  const filtered = Boolean(state.query || state.source || state.remote || state.status !== "new");
  return `<div class="list-empty"><h2>${filtered ? "No roles match this view." : "Your inbox is clear."}</h2><p>${filtered ? "Change or clear the active filters." : "New roles will appear here when the scout finds them."}</p>${filtered ? '<button class="tonal-button interactive" type="button" data-clear-all-filters>Clear filters</button>' : ""}</div>`;
}

function factItem(label, value) {
  return value ? `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>` : "";
}

function deadlineLabel(value) {
  if (!value) return "";
  const date = new Date(`${value}T23:59:59`);
  if (Number.isNaN(date.valueOf())) return "";
  const formatted = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
  const days = Math.ceil((date - new Date()) / 86400000);
  return days >= 0 && days < 7 ? `${formatted} (${days === 0 ? "today" : `${days}d left`})` : formatted;
}

function descriptionMarkup(job) {
  const detail = state.descriptions.get(job.dedupe_key);
  if (!detail) return "";
  if (detail.loading && detail.showLoader) return '<section class="detail-section description-skeleton skeleton" aria-label="Loading posting description"></section>';
  if (detail.error) return `<section class="detail-section inline-error" role="alert"><h2>Couldn't load the posting.</h2><p>${escapeHtml(detail.error)} Retry when the source is available.</p><button class="outlined-button interactive" type="button" data-retry-description>Retry</button></section>`;
  const value = detail.value || {};
  const sections = Array.isArray(value.sections) ? value.sections.filter((section) => section?.text) : [];
  const labels = { about: "About", responsibilities: "Responsibilities", requirements: "Requirements", nice_to_have: "Nice to have", benefits: "Benefits" };
  const parsed = sections.map((section) => `<details class="parsed-section" ${section.key === "requirements" ? "open" : ""}><summary>${escapeHtml(labels[section.key] || section.key || "Details")}</summary><div>${escapeHtml(section.text).replace(/\n/g, "<br>")}</div></details>`).join("");
  const original = value.description_text ? `<details class="parsed-section original-posting"><summary>Original posting</summary><div>${escapeHtml(value.description_text).replace(/\n/g, "<br>")}</div></details>` : "";
  return parsed || original ? `<section class="detail-section posting-sections" aria-labelledby="description-heading"><h2 id="description-heading">About the role</h2>${parsed}${original}</section>` : "";
}

function detailMarkup(job) {
  if (!job) return '<div class="reading-empty"><h2>Choose a role to read.</h2><p>The list stays in place while you review each posting.</p></div>';
  const sources = sourceList(job);
  const sourceText = sources.length ? sources.join(", ") : "";
  const otherRoles = state.jobs.filter((candidate) => candidate.dedupe_key !== job.dedupe_key && candidate.company === job.company).slice(0, 5);
  const recentDate = formatDate(job.recent_company_application_at);
  const recentWhen = recentDate === "Today" ? "today" : recentDate === "Yesterday" ? "yesterday" : recentDate;
  const recentCompanyWarning = recentWhen
    ? `<p class="company-warning"><span aria-hidden="true">!</span>You applied to another ${escapeHtml(job.company)} role ${escapeHtml(recentWhen)}.</p>` : "";
  const pending = state.pending.has(job.dedupe_key);
  const description = state.descriptions.get(job.dedupe_key)?.value;
  return `<article class="job-detail" aria-labelledby="job-title">
    <header class="job-detail-header"><div class="detail-heading"><h1 id="job-title" tabindex="-1">${escapeHtml(job.title)}</h1><a href="/companies?company=${encodeURIComponent(job.company || "")}" data-route>${escapeHtml(job.company)}</a></div>
      <dl class="fact-strip">${factItem("Location", job.location)}${factItem("Term", job.terms)}${factItem("Deadline", deadlineLabel(description?.deadline))}${factItem("Posted", formatDate(job.first_seen))}${factItem("Source", sourceText)}</dl>
      <div class="detail-actions" aria-label="Job actions"><button class="filled-button interactive" type="button" data-status-action="queued" ${pending ? 'aria-disabled="true"' : ""}>Queue</button><button class="tonal-button interactive" type="button" data-status-action="saved" ${pending ? 'aria-disabled="true"' : ""}>Save</button><button class="outlined-button interactive" type="button" data-apply-now ${!job.url || pending ? 'aria-disabled="true"' : ""}>Apply now</button>${state.focus ? '<button class="text-button interactive" type="button" data-exit-focus>Show list</button>' : ""}</div>
    </header>
    <div class="job-detail-body">
      <section class="detail-section" aria-labelledby="posting-details-heading"><h2 id="posting-details-heading">Posting details</h2><dl class="detail-facts">${factItem("Status", statusLabel(job.status || "new"))}${factItem("First seen", formatAbsolute(job.first_seen))}${factItem("Last seen", formatAbsolute(job.last_seen))}${factItem("Sources", sourceText)}</dl>${job.url ? `<a class="original-link" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">Open original posting ${icons.external}</a>` : ""}</section>
      ${descriptionMarkup(job)}
      <section class="detail-section" aria-labelledby="activity-heading"><h2 id="activity-heading">Activity</h2><div class="status-line"><span aria-hidden="true"></span><strong>${escapeHtml(statusLabel(job.status || "new"))}</strong>${job.application_updated_at ? `<time datetime="${escapeHtml(job.application_updated_at)}" title="${escapeHtml(formatAbsolute(job.application_updated_at))}">${escapeHtml(formatDate(job.application_updated_at))}</time>` : ""}</div><label class="notes-field" for="job-notes"><span>Notes</span><textarea id="job-notes" rows="5" placeholder="Add context for your next step">${escapeHtml(job.notes || "")}</textarea><small id="notes-state">Saved automatically</small></label></section>
      ${recentCompanyWarning || otherRoles.length ? `<section class="detail-section" aria-labelledby="company-history-heading"><h2 id="company-history-heading">Company history</h2>${recentCompanyWarning}<div class="company-roles">${otherRoles.map((other) => `<button class="company-role interactive" type="button" data-job-key="${escapeHtml(other.dedupe_key)}"><span>${escapeHtml(other.title)}</span><span>${escapeHtml(statusLabel(other.status || "new"))}</span></button>`).join("")}</div></section>` : ""}
    </div>
  </article>`;
}

function bulkBarMarkup() {
  const count = state.selectedKeys.size;
  if (!count) return "";
  return `<div class="bulk-bar" aria-label="Bulk actions"><span><strong>${new Intl.NumberFormat().format(count)}</strong> selected</span><button class="text-button interactive" type="button" data-bulk-action="saved">Save</button><button class="text-button interactive" type="button" data-bulk-action="queued">Queue</button><button class="text-button interactive" type="button" data-bulk-action="archived">Dismiss</button><button class="text-button interactive" type="button" data-dismiss-company>Dismiss company</button><button class="icon-button interactive" type="button" data-clear-selection aria-label="Clear selection">${icons.close}</button></div>`;
}

function inboxMarkup() {
  const jobs = filteredJobs();
  buildEntries(jobs);
  if (!state.selectedKey || !state.jobs.some((job) => job.dedupe_key === state.selectedKey)) state.selectedKey = jobs[0]?.dedupe_key || "";
  const selectedJob = state.jobs.find((job) => job.dedupe_key === state.selectedKey);
  const chips = activeFilterChips();
  return `<section class="inbox-page${state.focus ? " is-focus" : ""}${selectedKeyFromPath() ? " has-route-selection" : ""}" aria-label="Inbox">
    <aside class="inbox-list" aria-label="Job inbox"><div class="list-header"><div class="list-title-row"><h1 tabindex="-1">Inbox</h1><span>${new Intl.NumberFormat().format(jobs.length)}</span></div>${savedViewsMarkup()}<label class="job-search" for="job-search">${icons.search}<input id="job-search" type="search" autocomplete="off" placeholder="Search jobs" value="${escapeHtml(state.query)}" aria-label="Search jobs" aria-keyshortcuts="/"></label><div class="list-tools"><div class="status-tabs" role="tablist" aria-label="Job status">${visibleStatusTabs()}</div><button class="icon-button interactive" type="button" data-open-filters aria-label="Filter jobs">${icons.filter}</button><label class="sort-field"><span class="visually-hidden">Sort jobs</span><select id="job-sort" aria-label="Sort jobs"><option value="score" ${state.sort === "score" ? "selected" : ""}>Best match</option><option value="newest" ${state.sort === "newest" ? "selected" : ""}>Newest</option><option value="company" ${state.sort === "company" ? "selected" : ""}>Company</option></select></label></div>${chips ? `<div class="active-filters">${chips}</div>` : ""}</div>
      <div class="job-viewport" id="job-viewport" role="listbox" aria-label="Jobs" aria-multiselectable="true">${jobs.length ? '<div class="job-list-layer" id="job-list-layer"></div>' : emptyListMarkup()}</div>${bulkBarMarkup()}</aside>
    <main class="reading-pane" id="inbox-reading-pane">${detailMarkup(selectedJob)}</main>
  </section>`;
}

function loadingMarkup() {
  return `<section class="inbox-page loading-view" aria-label="Loading inbox"><aside class="inbox-list"><div class="list-header skeleton-block"></div><div class="skeleton-rows">${Array.from({ length: 6 }, () => '<div class="skeleton-row skeleton"></div>').join("")}</div></aside><main class="reading-pane"><div class="skeleton-detail skeleton"></div></main></section>`;
}

function errorMarkup(message) {
  return `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load the inbox.</h1><p>${escapeHtml(message)} Open JobSeer through Authentik, then retry.</p><button class="tonal-button interactive" type="button" data-retry-jobs>Retry</button></div></section>`;
}

async function loadDescription(key) {
  if (!key || state.descriptions.has(key)) return;
  const loading = { loading: true, showLoader: false, value: null, error: "" };
  state.descriptions.set(key, loading);
  const indicator = setTimeout(() => {
    loading.showLoader = true;
    loading.shownAt = performance.now();
    if (state.selectedKey === key && routeRoot() === "inbox") renderInbox();
  }, 300);
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/description`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The source did not return a description.");
    const payload = await response.json();
    const value = payload.description || {};
    const hold = loading.shownAt ? Math.max(0, 500 - (performance.now() - loading.shownAt)) : 0;
    if (hold) await new Promise((resolve) => setTimeout(resolve, hold));
    state.descriptions.set(key, {
      loading: false,
      showLoader: false,
      value,
      error: value.description_error || "",
    });
  } catch (error) {
    const hold = loading.shownAt ? Math.max(0, 500 - (performance.now() - loading.shownAt)) : 0;
    if (hold) await new Promise((resolve) => setTimeout(resolve, hold));
    state.descriptions.set(key, { loading: false, showLoader: false, value: null, error: error instanceof Error ? error.message : "The source did not return a description." });
  } finally {
    clearTimeout(indicator);
    if (state.selectedKey === key && routeRoot() === "inbox") renderInbox();
  }
}

function renderInbox({ focus = false } = {}) {
  if (state.error) routeView.innerHTML = errorMarkup(state.error);
  else if (!state.loaded) routeView.innerHTML = state.skeletonAt ? loadingMarkup() : "";
  else routeView.innerHTML = inboxMarkup();
  document.title = "Inbox — JobSeer";
  const viewport = document.querySelector("#job-viewport");
  if (viewport && state.entries.length) {
    viewport.scrollTop = Math.min(state.scrollTop, Math.max(0, state.totalHeight - viewport.clientHeight));
    viewport.addEventListener("scroll", () => { state.scrollTop = viewport.scrollTop; renderVirtualRows(); }, { passive: true });
    renderVirtualRows();
  }
  if (state.loaded && state.selectedKey) loadDescription(state.selectedKey);
  if (state.quickFillEnabled && (state.quickFillOpen || state.quickFillPopout)) renderQuickFill();
  if (focus) document.querySelector("#job-title, .inbox-list h1")?.focus({ preventScroll: true });
}

function renderPlaceholder(root, { focus = false } = {}) {
  const page = pages[root] || pages.queue;
  const isSession = root === "queue" && window.location.pathname.startsWith("/queue/session/");
  const headline = isSession ? "Application sessions start here." : page[1];
  const description = isSession ? "Choose jobs from the queue before beginning a focused session." : page[2];
  const action = isSession ? ["Return to queue", "/queue"] : [page[3], page[4]];
  routeView.innerHTML = `<section class="page-shell" aria-labelledby="page-title"><div class="empty-state"><div class="empty-mark" aria-hidden="true">${icons[root] || icons.inbox}</div><h1 id="page-title" tabindex="-1">${escapeHtml(headline)}</h1><p>${escapeHtml(description)}</p>${action[1] === "shortcuts" ? `<button class="tonal-button interactive" type="button" data-open-shortcuts>${escapeHtml(action[0])}</button>` : `<a class="tonal-button interactive" href="${action[1]}" data-route>${escapeHtml(action[0])}</a>`}</div></section>`;
  document.title = `${isSession ? "Session" : page[0]} — JobSeer`;
  if (focus) document.querySelector("#page-title")?.focus({ preventScroll: true });
}

function updateNav(root) {
  document.querySelectorAll("[data-nav]").forEach((item) => {
    if (item.dataset.nav === root) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
}

function renderRoute({ focus = false } = {}) {
  const root = routeRoot();
  updateNav(root);
  if (root === "inbox") {
    parseInboxUrl();
    renderInbox({ focus });
    if (!state.loaded && !state.loading) loadInbox();
  } else renderPlaceholder(root, { focus });
}

async function loadInbox() {
  state.loading = true;
  state.error = "";
  state.skeletonAt = 0;
  clearTimeout(loadingTimer);
  loadingTimer = setTimeout(() => { state.skeletonAt = performance.now(); if (routeRoot() === "inbox") renderInbox(); }, 300);
  try {
    const sessionResponse = await fetch("/api/v1/session", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!sessionResponse.ok) throw new Error((await sessionResponse.json().catch(() => ({}))).error || "The session could not be verified.");
    const session = await sessionResponse.json();
    state.csrf = session.csrf_token || "";
    state.quickFillEnabled = Boolean(session.features?.quick_fill);
    quickFillTrigger.hidden = !state.quickFillEnabled;
    quickFillSheet.hidden = !state.quickFillEnabled;
    if (state.quickFillEnabled && !state.quickFillProfile && !state.quickFillError) loadQuickFillData();
    if (!state.quickFillEnabled) closeQuickFill({ restoreFocus: false });
    const [jobsResponse, viewsResponse] = await Promise.all([
      fetch("/api/v1/jobs", { credentials: "same-origin", headers: { Accept: "application/json" } }),
      fetch("/api/v1/saved-views", { credentials: "same-origin", headers: { Accept: "application/json" } }),
    ]);
    if (!jobsResponse.ok) throw new Error((await jobsResponse.json().catch(() => ({}))).error || "The job list did not respond.");
    if (!viewsResponse.ok) throw new Error((await viewsResponse.json().catch(() => ({}))).error || "Saved views did not respond.");
    const [payload, viewsPayload] = await Promise.all([jobsResponse.json(), viewsResponse.json()]);
    const wait = state.skeletonAt ? Math.max(0, 500 - (performance.now() - state.skeletonAt)) : 0;
    if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
    state.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    state.savedViews = Array.isArray(viewsPayload.saved_views) ? viewsPayload.saved_views : [];
    state.refreshedAt = payload.refreshed_at || null;
    state.loaded = true;
    try { localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString()); } catch { /* Storage is optional. */ }
  } catch (error) {
    state.error = error instanceof Error ? error.message : "The job list did not respond.";
  } finally {
    clearTimeout(loadingTimer);
    state.loading = false;
    if (routeRoot() === "inbox") renderInbox();
  }
}

function currentJob() {
  return state.jobs.find((job) => job.dedupe_key === state.selectedKey) || null;
}

function detectedAts(job = currentJob()) {
  const known = new Set(["greenhouse", "lever", "ashby", "workday"]);
  try {
    const host = new URL(job?.url || "").hostname.toLowerCase();
    if (host.includes("greenhouse.io")) return "greenhouse";
    if (host.includes("lever.co")) return "lever";
    if (host.includes("ashbyhq.com")) return "ashby";
    if (host.includes("myworkdayjobs.com")) return "workday";
  } catch { /* Some imported jobs do not have a valid application URL yet. */ }
  return sourceList(job || {}).find((source) => known.has(source.toLowerCase()))?.toLowerCase() || "default";
}

function templateValue(body, job = currentJob()) {
  const values = {
    company: job?.company || "",
    role: job?.title || "",
    term: job?.terms || "",
    location: job?.location || "",
  };
  return String(body || "").replace(/\{(company|role|term|location)\}/g, (_, key) => values[key]);
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function quickFillGroups() {
  const profile = state.quickFillProfile || {};
  const query = state.quickFillQuery.trim().toLowerCase();
  const fields = (Array.isArray(profile.fields) ? profile.fields : [])
    .filter((field) => String(field.value || "").trim())
    .map((field) => ({
      key: `field:${field.key}`, label: field.label || field.key,
      value: String(field.value), group: field.group || "Identity", pinned: Boolean(field.pinned),
    }));
  const documents = (Array.isArray(profile.documents) ? profile.documents : [])
    .filter((document) => document?.name)
    .map((document) => ({
      key: `document:${document.id}`, label: document.name,
      value: String(document.url || document.name), group: "Documents",
      detail: document.date ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(`${document.date}T00:00:00`)) : "",
    }));
  const templates = (Array.isArray(profile.answer_templates) ? profile.answer_templates : [])
    .filter((template) => String(template?.body || "").trim())
    .map((template) => {
      const value = templateValue(template.body);
      return { key: `template:${template.id}`, label: template.name, value, group: "Short answers", detail: `${new Intl.NumberFormat().format(value.length)} characters` };
    });
  const stories = (Array.isArray(profile.stories) ? profile.stories : [])
    .filter((story) => story?.title)
    .map((story) => {
      const value = [
        ["Situation", story.situation], ["Task", story.task], ["Action", story.action],
        ["Result", story.result], ["Reflection", story.reflection],
      ].filter(([, text]) => String(text || "").trim()).map(([label, text]) => `${label}: ${text}`).join("\n");
      return {
        key: `story:${story.id}`, label: story.title, value, group: "Story bank",
        detail: (story.competencies || []).join(" · "),
      };
    }).filter((story) => story.value);
  const all = [...fields, ...documents, ...templates, ...stories];
  const order = state.atsOrdering[detectedAts()] || state.atsOrdering.default || [];
  const groups = [];
  const candidates = [
    ["Pinned", fields.filter((item) => item.pinned)],
    ...order.filter((name) => name !== "Pinned").map((name) => [name, all.filter((item) => item.group === name && !item.pinned)]),
  ];
  for (const [name, items] of candidates) {
    const visible = items.filter((item) => !query || `${item.label} ${item.value}`.toLowerCase().includes(query));
    if (visible.length) groups.push({ name, items: visible });
  }
  state.quickFillItems = groups.flatMap((group) => group.items);
  return groups;
}

function editorField(label, value, attributes, { multiline = false } = {}) {
  const control = multiline
    ? `<textarea rows="3" ${attributes}>${escapeHtml(value)}</textarea>`
    : `<input type="text" value="${escapeHtml(value)}" ${attributes}>`;
  return `<label class="quick-fill-edit-field"><span>${escapeHtml(label)}</span>${control}</label>`;
}

function quickFillEditorMarkup() {
  const profile = state.quickFillProfile || {};
  const fields = (profile.fields || []).map((field, index) => editorField(
    field.label || field.key, field.value || "", `data-profile-kind="fields" data-profile-index="${index}" data-profile-property="value"`,
  )).join("");
  const templates = (profile.answer_templates || []).map((template, index) => `<details class="quick-fill-edit-card"><summary>${escapeHtml(template.name)}</summary>${editorField("Template", template.body || "", `data-profile-kind="answer_templates" data-profile-index="${index}" data-profile-property="body"`, { multiline: true })}<small>${new Intl.NumberFormat().format(String(template.body || "").length)} characters before variables are replaced</small></details>`).join("");
  const documents = (profile.documents || []).map((document, index) => `<details class="quick-fill-edit-card"><summary>${escapeHtml(document.name)}</summary>${editorField("Name", document.name || "", `data-profile-kind="documents" data-profile-index="${index}" data-profile-property="name"`)}${editorField("Date", document.date || "", `data-profile-kind="documents" data-profile-index="${index}" data-profile-property="date"`)}${editorField("Path or URL", document.url || "", `data-profile-kind="documents" data-profile-index="${index}" data-profile-property="url"`)}</details>`).join("");
  const stories = (profile.stories || []).map((story, index) => `<details class="quick-fill-edit-card"><summary>${escapeHtml(story.title)}</summary>${editorField("Title", story.title || "", `data-profile-kind="stories" data-profile-index="${index}" data-profile-property="title"`)}${editorField("Competencies", (story.competencies || []).join(", "), `data-profile-kind="stories" data-profile-index="${index}" data-profile-property="competencies"`)}${["situation", "task", "action", "result", "reflection"].map((part) => editorField(statusLabel(part), story[part] || "", `data-profile-kind="stories" data-profile-index="${index}" data-profile-property="${part}"`, { multiline: true })).join("")}</details>`).join("");
  return `<div class="quick-fill-editor"><section><h3>Profile fields</h3>${fields}</section><section><h3>Documents</h3>${documents || '<p class="quick-fill-empty">No documents yet.</p>'}</section><section><h3>Short answers</h3>${templates || '<p class="quick-fill-empty">No templates yet.</p>'}</section><section><h3>Story bank</h3>${stories || '<p class="quick-fill-empty">No stories yet.</p>'}</section><div class="quick-fill-data-actions"><button class="outlined-button interactive" type="button" data-profile-export>Export JSON</button><button class="outlined-button interactive" type="button" data-profile-import-trigger>Import JSON</button><input class="visually-hidden" type="file" accept="application/json,.json" data-profile-import tabindex="-1"></div></div>`;
}

function quickFillInnerMarkup({ popout = false } = {}) {
  const groups = quickFillGroups();
  const ats = detectedAts();
  let itemIndex = 0;
  const jobKey = currentJob()?.dedupe_key || "none";
  const tracked = state.copiedByJob.get(jobKey) || new Set();
  const content = groups.map((group, groupIndex) => {
    const rows = group.items.map((item, index) => {
      const flatIndex = itemIndex;
      itemIndex += 1;
      const copied = state.copiedKey === item.key;
      const wasCopied = tracked.has(item.key);
      const shortcut = index < 9 ? `<span class="copy-shortcut" aria-hidden="true">${index + 1}</span>` : "";
      return `<button class="copy-row interactive${copied ? " is-copied" : ""}" type="button" data-copy-index="${flatIndex}" data-tracked="${wasCopied}" tabindex="${flatIndex === 0 ? "0" : "-1"}"><span class="copy-row-copy"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail || item.value)}</span></span><span class="copy-row-icon" aria-hidden="true">${copied || wasCopied ? icons.check : shortcut || icons.copy}</span></button>`;
    }).join("");
    return `<section class="quick-fill-group" aria-labelledby="quick-fill-group-${groupIndex}"><h3 id="quick-fill-group-${groupIndex}">${escapeHtml(group.name)}</h3>${rows}</section>`;
  }).join("");
  const status = ats === "default" ? "Standard order" : `${ats.slice(0, 1).toUpperCase()}${ats.slice(1)} order`;
  const guidance = state.atsOrdering._guidance?.[ats] || state.atsOrdering._guidance?.default || "";
  const body = state.quickFillEdit
    ? quickFillEditorMarkup()
    : `<label class="quick-fill-search" for="quick-fill-search-${popout ? "popout" : "docked"}">${icons.search}<input id="quick-fill-search-${popout ? "popout" : "docked"}" type="search" value="${escapeHtml(state.quickFillQuery)}" placeholder="Search fields" aria-label="Search Quick-fill fields"></label>${guidance ? `<p class="quick-fill-guidance">${escapeHtml(guidance)}</p>` : ""}<div class="quick-fill-groups">${state.quickFillError ? `<div class="quick-fill-empty" role="alert">${escapeHtml(state.quickFillError)}</div>` : content || '<p class="quick-fill-empty">No filled fields match this search.</p>'}</div>`;
  return `<div class="quick-fill-header"><div><h2>Quick-fill</h2><p>${state.quickFillEdit ? `<span class="quick-fill-save-state">${escapeHtml(state.quickFillSaveState || "Changes save automatically")}</span>` : escapeHtml(status)}</p></div><button class="text-button interactive quick-fill-edit-toggle" type="button" data-quick-fill-edit>${state.quickFillEdit ? "Done" : "Edit"}</button><button class="icon-button interactive" type="button" data-quick-fill-popout aria-label="Pop out Quick-fill" ${popout ? "hidden" : ""}>${icons.external}</button><button class="icon-button interactive" type="button" data-quick-fill-close aria-label="Close Quick-fill">${icons.close}</button></div>${body}`;
}

function bindQuickFillSurface(root, { popout = false } = {}) {
  root.addEventListener("click", (event) => {
    const copy = event.target.closest("[data-copy-index]");
    if (copy) { copyQuickFillItem(Number(copy.dataset.copyIndex)); return; }
    if (event.target.closest("[data-quick-fill-popout]")) { popOutQuickFill(); return; }
    if (event.target.closest("[data-quick-fill-edit]")) {
      state.quickFillEdit = !state.quickFillEdit; renderQuickFill();
      requestAnimationFrame(() => (state.quickFillEdit ? root.querySelector("[data-profile-kind]") : root.querySelector("[data-copy-index]"))?.focus());
      return;
    }
    if (event.target.closest("[data-profile-export]")) { exportQuickFillProfile(); return; }
    if (event.target.closest("[data-profile-import-trigger]")) { root.querySelector("[data-profile-import]")?.click(); return; }
    if (event.target.closest("[data-quick-fill-close]")) {
      if (popout) state.quickFillPopout?.close(); else closeQuickFill();
    }
  });
  root.addEventListener("input", (event) => {
    if (event.target.matches("[data-profile-kind]")) {
      updateQuickFillProfile(event.target);
      return;
    }
    if (!event.target.matches("[id^='quick-fill-search-']")) return;
    state.quickFillQuery = event.target.value;
    renderQuickFill();
    const search = (popout ? state.quickFillPopout?.document : document)?.querySelector(`#${event.target.id}`);
    search?.focus(); search?.setSelectionRange(state.quickFillQuery.length, state.quickFillQuery.length);
  });
  root.addEventListener("change", (event) => {
    if (event.target.matches("[data-profile-import]") && event.target.files?.[0]) {
      const [file] = event.target.files; event.target.value = ""; importQuickFillProfile(file);
    }
  });
  root.addEventListener("keydown", (event) => {
    const row = event.target.closest("[data-copy-index]");
    if (!row) return;
    const rows = [...root.querySelectorAll("[data-copy-index]")];
    const index = rows.indexOf(row);
    const next = event.key === "ArrowDown" ? Math.min(rows.length - 1, index + 1)
      : event.key === "ArrowUp" ? Math.max(0, index - 1)
      : event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : -1;
    if (next >= 0) {
      event.preventDefault(); rows.forEach((item, cursor) => { item.tabIndex = cursor === next ? 0 : -1; }); rows[next].focus();
    } else if (event.key === "Enter") { event.preventDefault(); copyQuickFillItem(Number(row.dataset.copyIndex)); }
  });
}

function setQuickFillSaveState(value) {
  state.quickFillSaveState = value;
  document.querySelectorAll(".quick-fill-save-state").forEach((element) => { element.textContent = value; });
  if (state.quickFillPopout && !state.quickFillPopout.closed) {
    state.quickFillPopout.document.querySelectorAll(".quick-fill-save-state").forEach((element) => { element.textContent = value; });
  }
}

function profileDocument(profile = state.quickFillProfile) {
  return {
    fields: cloneJson(profile?.fields || []),
    documents: cloneJson(profile?.documents || []),
    answer_templates: cloneJson(profile?.answer_templates || []),
    stories: cloneJson(profile?.stories || []),
  };
}

async function persistQuickFillProfile(profile) {
  const response = await fetch("/api/v1/profile", {
    method: "PUT", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
    body: JSON.stringify({ profile }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Quick-fill could not be saved.");
  return (await response.json()).profile;
}

function updateQuickFillProfile(control) {
  const collection = state.quickFillProfile?.[control.dataset.profileKind];
  const item = Array.isArray(collection) ? collection[Number(control.dataset.profileIndex)] : null;
  const property = control.dataset.profileProperty;
  if (!item || !property) return;
  item[property] = property === "competencies"
    ? control.value.split(",").map((value) => value.trim()).filter(Boolean)
    : control.value;
  const counter = control.closest(".quick-fill-edit-card")?.querySelector("small");
  if (counter && property === "body") counter.textContent = `${new Intl.NumberFormat().format(control.value.length)} characters before variables are replaced`;
  state.quickFillSaveVersion += 1;
  const version = state.quickFillSaveVersion;
  const snapshot = profileDocument();
  clearTimeout(state.quickFillSaveTimer);
  setQuickFillSaveState("Unsaved changes");
  state.quickFillSaveTimer = setTimeout(async () => {
    setQuickFillSaveState("Saving…");
    try {
      const saved = await persistQuickFillProfile(snapshot);
      state.quickFillSavedProfile = cloneJson(saved);
      if (version === state.quickFillSaveVersion) {
        state.quickFillProfile = saved;
        setQuickFillSaveState("Saved automatically");
      }
    } catch (error) {
      if (version === state.quickFillSaveVersion && state.quickFillSavedProfile) {
        state.quickFillProfile = cloneJson(state.quickFillSavedProfile);
        renderQuickFill();
      }
      assertiveRegion.textContent = `${error.message} Changes rolled back.`;
      showSnackbar(`${error.message} Changes rolled back.`);
    }
  }, 600);
}

function exportQuickFillProfile() {
  const blob = new Blob([`${JSON.stringify(profileDocument(), null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = "jobseer-profile.json"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  showSnackbar("Quick-fill JSON exported.");
}

async function importQuickFillProfile(file) {
  const previous = profileDocument();
  setQuickFillSaveState("Importing…");
  try {
    const parsed = JSON.parse(await file.text());
    const candidate = parsed.profile && typeof parsed.profile === "object" ? parsed.profile : parsed;
    const saved = await persistQuickFillProfile(candidate);
    state.quickFillProfile = saved;
    state.quickFillSavedProfile = cloneJson(saved);
    state.quickFillSaveVersion += 1;
    renderQuickFill();
    showSnackbar("Quick-fill JSON imported.", {
      action: "Undo",
      duration: 6000,
      onAction: async () => {
        try {
          const restored = await persistQuickFillProfile(previous);
          state.quickFillProfile = restored; state.quickFillSavedProfile = cloneJson(restored); renderQuickFill(); showSnackbar("Import undone.");
        } catch (error) { showSnackbar(error.message || "The import could not be undone."); }
      },
    });
  } catch (error) {
    setQuickFillSaveState("Import failed");
    showSnackbar(error instanceof Error ? error.message : "The profile file could not be imported.");
  }
}

function renderQuickFill() {
  if (!state.quickFillEnabled) return;
  quickFillSheet.innerHTML = quickFillInnerMarkup();
  quickFillSheet.setAttribute("aria-hidden", state.quickFillOpen ? "false" : "true");
  quickFillTrigger.setAttribute("aria-expanded", String(state.quickFillOpen));
  quickFillTrigger.setAttribute("aria-label", `${state.quickFillOpen ? "Close" : "Open"} Quick-fill`);
  if (state.quickFillPopout && !state.quickFillPopout.closed) {
    const popoutSheet = state.quickFillPopout.document.querySelector("#quick-fill-sheet");
    if (popoutSheet) popoutSheet.innerHTML = quickFillInnerMarkup({ popout: true });
  }
}

async function loadQuickFillData() {
  try {
    const [profileResponse, orderResponse] = await Promise.all([
      fetch("/api/v1/profile", { credentials: "same-origin", headers: { Accept: "application/json" } }),
      fetch("/static/ats-ordering.json", { credentials: "same-origin", headers: { Accept: "application/json" } }),
    ]);
    if (!profileResponse.ok) throw new Error((await profileResponse.json().catch(() => ({}))).error || "Quick-fill data did not respond.");
    if (!orderResponse.ok) throw new Error("ATS field ordering did not respond.");
    state.quickFillProfile = (await profileResponse.json()).profile || {};
    state.quickFillSavedProfile = cloneJson(state.quickFillProfile);
    state.atsOrdering = await orderResponse.json();
    state.quickFillError = "";
  } catch (error) {
    state.quickFillError = error instanceof Error ? error.message : "Quick-fill data did not respond.";
  }
  renderQuickFill();
}

function openQuickFill() {
  if (!state.quickFillEnabled) return;
  state.quickFillOpen = true;
  renderQuickFill();
  requestAnimationFrame(() => quickFillSheet.querySelector("input")?.focus());
}

function closeQuickFill({ restoreFocus = true } = {}) {
  state.quickFillOpen = false;
  quickFillSheet.setAttribute("aria-hidden", "true");
  quickFillTrigger.setAttribute("aria-expanded", "false");
  if (restoreFocus && !quickFillTrigger.hidden) quickFillTrigger.focus();
}

async function copyQuickFillItem(index) {
  const item = state.quickFillItems[index];
  if (!item) return;
  try {
    await navigator.clipboard.writeText(item.value);
  } catch {
    const fallback = document.createElement("textarea");
    fallback.className = "clipboard-fallback";
    fallback.value = item.value;
    fallback.setAttribute("aria-label", `${item.label}; press Control C to copy`);
    document.body.append(fallback);
    fallback.select();
    showSnackbar("Couldn't copy. Text selected.");
    setTimeout(() => fallback.remove(), 6000);
    return;
  }
  const jobKey = currentJob()?.dedupe_key || "none";
  if (!state.copiedByJob.has(jobKey)) state.copiedByJob.set(jobKey, new Set());
  state.copiedByJob.get(jobKey).add(item.key);
  state.copiedKey = item.key;
  renderQuickFill();
  showSnackbar(`Copied ${item.label.toLowerCase()}.`);
  setTimeout(() => { if (state.copiedKey === item.key) { state.copiedKey = ""; renderQuickFill(); } }, 1500);
}

async function popOutQuickFill() {
  if (!state.quickFillEnabled) return;
  let target;
  try {
    if (window.documentPictureInPicture?.requestWindow) target = await window.documentPictureInPicture.requestWindow({ width: 380, height: 640 });
    else target = window.open("", "jobseer-quick-fill", "popup,width=380,height=640");
  } catch { target = window.open("", "jobseer-quick-fill", "popup,width=380,height=640"); }
  if (!target) { showSnackbar("The browser blocked the Quick-fill window."); return; }
  target.document.head.innerHTML = `<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Quick-fill — JobSeer</title><link rel="stylesheet" href="/static/tokens.css"><link rel="stylesheet" href="/static/app.css">`;
  target.document.body.className = "quick-fill-popout";
  target.document.body.innerHTML = `<aside class="quick-fill-sheet" id="quick-fill-sheet" aria-label="Quick-fill" aria-hidden="false">${quickFillInnerMarkup({ popout: true })}</aside>`;
  state.quickFillPopout = target;
  bindQuickFillSurface(target.document.body, { popout: true });
  target.addEventListener("pagehide", () => { if (state.quickFillPopout === target) state.quickFillPopout = null; }, { once: true });
}

function renderCommands() {
  const query = commandInput.value;
  commandMatches = commands.map((command) => ({ command, score: fuzzyScore(`${command.label} ${command.detail}`, query) })).filter((entry) => entry.score >= 0).sort((left, right) => right.score - left.score).map((entry) => entry.command);
  commandSelection = Math.min(commandSelection, Math.max(0, commandMatches.length - 1));
  commandResults.innerHTML = commandMatches.length ? commandMatches.map((command, index) => `<button class="command-item interactive" type="button" role="option" aria-selected="${index === commandSelection}" data-command="${command.id}">${icons[command.id] || icons.shortcuts}<span><strong>${escapeHtml(command.label)}</strong><small>${escapeHtml(command.detail)}</small></span><kbd>${escapeHtml(command.shortcut)}</kbd></button>`).join("") : '<p class="command-empty">No matching commands</p>';
  commandResults.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function rememberDialogTrigger(trigger) { dialogTrigger = trigger instanceof HTMLElement ? trigger : document.activeElement; }
function restoreDialogTrigger() { if (dialogTrigger instanceof HTMLElement && dialogTrigger.isConnected) dialogTrigger.focus(); dialogTrigger = null; }

function openCommand(trigger) {
  if (commandDialog.open) return;
  if (shortcutDialog.open) shortcutDialog.close();
  if (filterDialog.open) filterDialog.close();
  rememberDialogTrigger(trigger); commandInput.value = ""; commandSelection = 0; renderCommands(); commandDialog.showModal(); commandInput.focus();
}

function openShortcuts(trigger) {
  if (shortcutDialog.open) return;
  if (commandDialog.open) commandDialog.close();
  if (filterDialog.open) filterDialog.close();
  rememberDialogTrigger(trigger); shortcutDialog.showModal(); shortcutDialog.querySelector("button")?.focus();
}

function openFilters(trigger) {
  rememberDialogTrigger(trigger);
  const sources = [...new Set(state.jobs.flatMap(sourceList))].sort((a, b) => a.localeCompare(b));
  sourceOptions.innerHTML = `<label class="check-row"><input type="radio" name="source" value="" ${state.source ? "" : "checked"}><span>Any source</span></label>${sources.map((source) => `<label class="check-row"><input type="radio" name="source" value="${escapeHtml(source)}" ${state.source === source ? "checked" : ""}><span>${escapeHtml(source)}</span></label>`).join("")}`;
  remoteFilter.checked = state.remote;
  savedViewName.value = "";
  filterDialog.showModal();
  sourceOptions.querySelector("input:checked")?.focus();
}

function runCommand(id) {
  const command = commands.find((item) => item.id === id);
  if (!command) return;
  commandDialog.close();
  if (command.route) navigate(command.route);
  if (command.action === "shortcuts") openShortcuts(document.querySelector("[data-open-shortcuts]"));
}

function selectJob(key, { push = true, focusDetail = false } = {}) {
  if (!state.jobs.some((job) => job.dedupe_key === key)) return;
  state.selectedKey = key;
  if (push) syncInboxUrl();
  renderInbox();
  if (focusDetail) document.querySelector("#job-title")?.focus({ preventScroll: true });
}

function handleRowSelection(row, event) {
  const jobs = filteredJobs();
  const index = Number(row.dataset.jobIndex);
  const key = row.dataset.jobKey;
  if (event.shiftKey && state.rangeAnchor >= 0) {
    const [start, end] = [state.rangeAnchor, index].sort((a, b) => a - b);
    for (let cursor = start; cursor <= end; cursor += 1) state.selectedKeys.add(jobs[cursor].dedupe_key);
    state.selectedKey = key; syncInboxUrl(); renderInbox(); return;
  }
  if (event.ctrlKey || event.metaKey) {
    if (state.selectedKeys.has(key)) state.selectedKeys.delete(key); else state.selectedKeys.add(key);
    state.rangeAnchor = index; state.selectedKey = key; syncInboxUrl(); renderInbox(); return;
  }
  state.selectedKeys.clear(); state.rangeAnchor = index; selectJob(key);
}

async function patchJob(job, status, notes = job.notes || "") {
  const response = await fetch(`/api/v1/jobs/${encodeURIComponent(job.dedupe_key)}`, {
    method: "PATCH", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
    body: JSON.stringify({ status, notes }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The change could not be saved.");
}

function nextKeyAfter(keys) {
  const before = filteredJobs();
  const current = Math.max(0, before.findIndex((job) => job.dedupe_key === state.selectedKey));
  const remaining = before.filter((job) => !keys.includes(job.dedupe_key));
  return remaining[Math.min(current, remaining.length - 1)]?.dedupe_key || "";
}

async function performDecision(status, label, targets = null) {
  const keys = targets || (state.selectedKeys.size ? [...state.selectedKeys] : [state.selectedKey]);
  const jobs = keys.map((key) => state.jobs.find((job) => job.dedupe_key === key)).filter(Boolean);
  if (!jobs.length || jobs.some((job) => state.pending.has(job.dedupe_key))) return;
  const previous = jobs.map((job) => ({ key: job.dedupe_key, status: job.status, notes: job.notes || "" }));
  const nextKey = nextKeyAfter(keys);
  jobs.forEach((job) => { state.pending.add(job.dedupe_key); job.status = status; });
  state.selectedKeys.clear(); state.selectedKey = nextKey || filteredJobs()[0]?.dedupe_key || ""; syncInboxUrl(); renderInbox();
  const results = await Promise.allSettled(jobs.map((job) => patchJob(job, status)));
  const failed = results.some((result) => result.status === "rejected");
  if (failed) {
    previous.forEach((snapshot) => { const job = state.jobs.find((candidate) => candidate.dedupe_key === snapshot.key); if (job) job.status = snapshot.status; });
    await Promise.allSettled(results.map((result, index) => result.status === "fulfilled" ? patchJob(jobs[index], previous[index].status, previous[index].notes) : Promise.resolve()));
    const reason = results.find((result) => result.status === "rejected")?.reason;
    assertiveRegion.textContent = `${reason?.message || "Couldn't save the change."} Change undone.`;
    showSnackbar(`${reason?.message || "Couldn't save the change."} Change undone.`);
  } else {
    state.undo = { previous, nextStatus: status };
    const noun = jobs.length === 1 ? "role" : `${new Intl.NumberFormat().format(jobs.length)} roles`;
    showSnackbar(`${label} ${noun}.`, { action: "Undo", onAction: undoLastDecision, duration: 6000 });
  }
  jobs.forEach((job) => state.pending.delete(job.dedupe_key));
  if (routeRoot() === "inbox") renderInbox();
}

async function undoLastDecision() {
  const undo = state.undo;
  if (!undo) return;
  state.undo = null;
  const snapshots = undo.previous;
  snapshots.forEach((snapshot) => { const job = state.jobs.find((candidate) => candidate.dedupe_key === snapshot.key); if (job) job.status = snapshot.status; });
  state.selectedKey = snapshots[0]?.key || state.selectedKey; syncInboxUrl(); renderInbox();
  const results = await Promise.allSettled(snapshots.map((snapshot) => { const job = state.jobs.find((candidate) => candidate.dedupe_key === snapshot.key); return job ? patchJob(job, snapshot.status, snapshot.notes) : Promise.resolve(); }));
  if (results.some((result) => result.status === "rejected")) {
    snapshots.forEach((snapshot) => { const job = state.jobs.find((candidate) => candidate.dedupe_key === snapshot.key); if (job) job.status = undo.nextStatus; });
    showSnackbar("Couldn't undo. The original change remains."); renderInbox();
  } else showSnackbar("Decision undone.");
}

async function saveNotes(key, value) {
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  if (!job) return;
  const oldNotes = job.notes || "";
  job.notes = value;
  const notesState = document.querySelector("#notes-state");
  if (notesState) notesState.textContent = "Saving…";
  try {
    await patchJob(job, job.status || "new", value);
    if (state.selectedKey === key && document.querySelector("#notes-state")) document.querySelector("#notes-state").textContent = "Saved automatically";
  } catch (error) {
    job.notes = oldNotes; assertiveRegion.textContent = `${error.message} Note restored.`; showSnackbar(`${error.message} Note restored.`);
    if (state.selectedKey === key) { renderInbox(); document.querySelector("#job-notes")?.focus(); }
  }
}

function moveSelection(delta) {
  const jobs = filteredJobs();
  if (!jobs.length) return;
  const current = jobs.findIndex((job) => job.dedupe_key === state.selectedKey);
  const next = Math.min(jobs.length - 1, Math.max(0, (current < 0 ? 0 : current) + delta));
  state.selectedKeys.clear(); selectJob(jobs[next].dedupe_key);
  document.querySelector(`[data-job-index="${next}"]`)?.scrollIntoView({ block: "nearest" });
}

function clearAllFilters() {
  state.query = ""; state.status = "all"; state.source = ""; state.remote = false; state.scrollTop = 0; syncInboxUrl(); renderInbox();
}

function applySavedView(index) {
  const view = state.savedViews[index];
  if (!view) return;
  const filters = view.filters || {};
  state.query = filters.query || ""; state.status = allowedStatuses.has(filters.status) ? filters.status : "all"; state.sort = allowedSorts.has(view.sort) ? view.sort : "score"; state.source = filters.source || ""; state.remote = Boolean(filters.remote); state.scrollTop = 0; syncInboxUrl(); renderInbox();
}

async function saveCurrentView() {
  const name = savedViewName.value.trim();
  if (!name) { savedViewName.focus(); savedViewName.setAttribute("aria-invalid", "true"); return; }
  savedViewName.removeAttribute("aria-invalid");
  try {
    const response = await fetch("/api/v1/saved-views", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ name, filters: { query: state.query, status: state.status, source: state.source, remote: state.remote }, sort: state.sort, pinned: true }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The view could not be saved.");
    const saved = (await response.json()).saved_view;
    const existing = state.savedViews.findIndex((candidate) => candidate.id === saved.id);
    if (existing >= 0) state.savedViews[existing] = saved; else state.savedViews.push(saved);
    filterDialog.close(); renderInbox(); showSnackbar(`Saved view “${name}”.`);
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "The view could not be saved.");
  }
}

async function deleteSavedView(viewId) {
  const index = state.savedViews.findIndex((view) => view.id === viewId);
  const removed = state.savedViews[index];
  const response = await fetch(`/api/v1/saved-views/${viewId}`, {
    method: "DELETE", credentials: "same-origin",
    headers: { "X-CSRF-Token": state.csrf, Accept: "application/json" },
  });
  if (!response.ok) { showSnackbar((await response.json().catch(() => ({}))).error || "The view could not be deleted."); return; }
  state.savedViews = state.savedViews.filter((view) => view.id !== viewId);
  renderInbox();
  requestAnimationFrame(() => {
    const target = document.querySelectorAll("[data-saved-view]")[Math.min(index, state.savedViews.length - 1)] || document.querySelector("[data-open-filters]");
    target?.focus();
  });
  showSnackbar("Saved view deleted.", { action: "Undo", onAction: () => restoreSavedView(removed), duration: 6000 });
}

async function restoreSavedView(view) {
  if (!view) return;
  try {
    const response = await fetch("/api/v1/saved-views", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ name: view.name, filters: view.filters, sort: view.sort, pinned: view.pinned }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The view could not be restored.");
    state.savedViews.push((await response.json()).saved_view);
    renderInbox(); showSnackbar("Saved view restored.");
  } catch (error) { showSnackbar(error instanceof Error ? error.message : "The view could not be restored."); }
}

function showSnackbar(message, { action = "", onAction = null, duration = 4000 } = {}) {
  clearTimeout(snackbarTimer);
  snackbarRemaining = duration;
  snackbarRegion.innerHTML = `<div class="snackbar" role="status"><span>${escapeHtml(message)}</span>${action ? `<button type="button" data-snackbar-action>${escapeHtml(action)}</button>` : ""}</div>`;
  const bar = snackbarRegion.firstElementChild;
  const dismiss = () => { snackbarRegion.innerHTML = ""; snackbarTimer = null; };
  const start = () => { snackbarDeadline = performance.now() + Math.max(2000, snackbarRemaining); snackbarTimer = setTimeout(dismiss, Math.max(2000, snackbarRemaining)); };
  const pause = () => { if (!snackbarTimer) return; clearTimeout(snackbarTimer); snackbarTimer = null; snackbarRemaining = Math.max(2000, snackbarDeadline - performance.now()); };
  bar?.addEventListener("mouseenter", pause); bar?.addEventListener("mouseleave", start); bar?.addEventListener("focusin", pause); bar?.addEventListener("focusout", start);
  bar?.querySelector("[data-snackbar-action]")?.addEventListener("click", () => { dismiss(); onAction?.(); });
  start();
}

function shortcutScopeAllows(event) {
  const target = event.target;
  return !(target instanceof HTMLElement && (target.matches("input, textarea, select, [contenteditable='true'], [role='textbox']") || target.closest("[contenteditable='true']")));
}

function handleGlobalKeydown(event) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openCommand(document.activeElement); return; }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && routeRoot() === "inbox" && shortcutScopeAllows(event)) { event.preventDefault(); undoLastDecision(); return; }
  if (commandDialog.open || shortcutDialog.open || filterDialog.open || !shortcutScopeAllows(event)) return;
  if (event.key === "?") { event.preventDefault(); openShortcuts(document.activeElement); return; }
  const now = performance.now();
  const key = event.key.toLowerCase();
  if (event.key === "Escape" && state.quickFillOpen) { event.preventDefault(); closeQuickFill(); return; }
  if (state.quickFillEnabled && now > goChordUntil && key === "c") { event.preventDefault(); state.quickFillOpen ? closeQuickFill() : openQuickFill(); return; }
  if (state.quickFillEnabled && now > goChordUntil && key === "p") { event.preventDefault(); popOutQuickFill(); return; }
  if (state.quickFillOpen && key === "/") { event.preventDefault(); quickFillSheet.querySelector("input")?.focus(); return; }
  if (state.quickFillOpen && /^[1-9]$/.test(key)) {
    const activeGroup = document.activeElement?.closest(".quick-fill-group") || quickFillSheet.querySelector(".quick-fill-group");
    const rows = [...(activeGroup?.querySelectorAll("[data-copy-index]") || [])];
    const row = rows[Number(key) - 1];
    if (row) { event.preventDefault(); copyQuickFillItem(Number(row.dataset.copyIndex)); }
    return;
  }
  if (routeRoot() === "inbox" && state.loaded) {
    if (key === "/") { event.preventDefault(); document.querySelector("#job-search")?.focus(); return; }
    if (key === "j" || key === "k") { event.preventDefault(); moveSelection(key === "j" ? 1 : -1); return; }
    if (event.key === "Enter") { event.preventDefault(); document.querySelector("#job-title")?.focus({ preventScroll: true }); return; }
    if (key === "f") { event.preventDefault(); state.focus = !state.focus; syncInboxUrl(); renderInbox({ focus: true }); return; }
    if (key === "s") { event.preventDefault(); performDecision("saved", "Saved"); return; }
    if (key === "x") { event.preventDefault(); performDecision("archived", "Dismissed"); return; }
    if (key === "q") { event.preventDefault(); performDecision("queued", "Queued"); return; }
    if (key === "a") {
      const job = state.jobs.find((candidate) => candidate.dedupe_key === state.selectedKey);
      if (job?.url) { event.preventDefault(); window.open(job.url, "_blank", "noopener"); performDecision("applying", "Opened"); }
      return;
    }
    if (key === "u") { event.preventDefault(); undoLastDecision(); return; }
  }
  if (key === "g") { goChordUntil = now + 1000; return; }
  if (now <= goChordUntil) {
    const routes = { i: "/inbox", q: "/queue", t: "/tracker", c: "/companies", p: "/profile" };
    goChordUntil = 0;
    if (routes[key]) { event.preventDefault(); navigate(routes[key]); }
  }
}

document.addEventListener("click", (event) => {
  const route = event.target.closest("[data-route]");
  if (route instanceof HTMLAnchorElement) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); navigate(route.href); return;
  }
  const row = event.target.closest(".job-row[data-job-key]");
  if (row) { handleRowSelection(row, event); return; }
  const companyRole = event.target.closest(".company-role[data-job-key]");
  if (companyRole) { selectJob(companyRole.dataset.jobKey); return; }
  const statusAction = event.target.closest("[data-status-action]");
  if (statusAction && statusAction.getAttribute("aria-disabled") !== "true") { performDecision(statusAction.dataset.statusAction, statusAction.dataset.statusAction === "saved" ? "Saved" : "Queued"); return; }
  const apply = event.target.closest("[data-apply-now]");
  if (apply && apply.getAttribute("aria-disabled") !== "true") {
    const job = state.jobs.find((candidate) => candidate.dedupe_key === state.selectedKey);
    if (job?.url) { window.open(job.url, "_blank", "noopener"); performDecision("applying", "Opened"); }
    return;
  }
  const bulk = event.target.closest("[data-bulk-action]");
  if (bulk) { const labels = { saved: "Saved", queued: "Queued", archived: "Dismissed" }; performDecision(bulk.dataset.bulkAction, labels[bulk.dataset.bulkAction]); return; }
  if (event.target.closest("[data-dismiss-company]")) {
    const selected = state.jobs.filter((job) => state.selectedKeys.has(job.dedupe_key));
    const companies = new Set(selected.map((job) => job.company));
    const targets = state.jobs.filter((job) => companies.has(job.company)).map((job) => job.dedupe_key);
    performDecision("archived", "Dismissed", targets); return;
  }
  if (event.target.closest("[data-clear-selection]")) { state.selectedKeys.clear(); renderInbox(); return; }
  const tab = event.target.closest("[data-status-tab]");
  if (tab) { state.status = tab.dataset.statusTab; state.scrollTop = 0; state.selectedKeys.clear(); syncInboxUrl(); renderInbox(); return; }
  if (event.target.closest("[data-open-filters]")) { openFilters(event.target.closest("[data-open-filters]")); return; }
  const chip = event.target.closest("[data-clear-filter]");
  if (chip) {
    if (chip.dataset.clearFilter === "query") state.query = "";
    if (chip.dataset.clearFilter === "source") state.source = "";
    if (chip.dataset.clearFilter === "remote") state.remote = false;
    syncInboxUrl(); renderInbox(); return;
  }
  if (event.target.closest("[data-clear-all-filters]")) { clearAllFilters(); return; }
  const savedView = event.target.closest("[data-saved-view]");
  if (savedView) { applySavedView(Number(savedView.dataset.savedView)); return; }
  const deleteView = event.target.closest("[data-delete-saved-view]");
  if (deleteView) { deleteSavedView(Number(deleteView.dataset.deleteSavedView)); return; }
  if (event.target.closest("[data-exit-focus]")) { state.focus = false; syncInboxUrl(); renderInbox({ focus: true }); return; }
  if (event.target.closest("[data-retry-jobs]")) { state.loaded = false; state.error = ""; renderInbox(); loadInbox(); return; }
  if (event.target.closest("[data-retry-description]")) { state.descriptions.delete(state.selectedKey); loadDescription(state.selectedKey); renderInbox(); return; }
  if (event.target.closest("[data-open-command]")) { openCommand(event.target.closest("[data-open-command]")); return; }
  if (event.target.closest("[data-open-shortcuts]")) { openShortcuts(event.target.closest("[data-open-shortcuts]")); return; }
  if (event.target.closest("[data-quick-fill-toggle]")) { state.quickFillOpen ? closeQuickFill() : openQuickFill(); return; }
  const close = event.target.closest("[data-close-dialog]");
  if (close) { close.closest("dialog")?.close(); return; }
  const command = event.target.closest("[data-command]");
  if (command) runCommand(command.dataset.command);
});

document.addEventListener("input", (event) => {
  if (event.target.id === "job-search") {
    state.query = event.target.value; state.scrollTop = 0; syncInboxUrl({ replace: true }); renderInbox();
    const search = document.querySelector("#job-search"); search?.focus(); search?.setSelectionRange(state.query.length, state.query.length);
  } else if (event.target.id === "job-notes") {
    const key = state.selectedKey;
    const value = event.target.value;
    clearTimeout(state.notesTimer); document.querySelector("#notes-state").textContent = "Unsaved changes"; state.notesTimer = setTimeout(() => saveNotes(key, value), 600);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "job-sort") { state.sort = event.target.value; state.scrollTop = 0; syncInboxUrl(); renderInbox(); }
});

filterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(filterForm);
  state.source = String(data.get("source") || ""); state.remote = remoteFilter.checked; state.scrollTop = 0; filterDialog.close(); syncInboxUrl(); renderInbox();
});

document.querySelector("#clear-filters").addEventListener("click", () => { state.source = ""; state.remote = false; sourceOptions.querySelector('input[value=""]')?.click(); remoteFilter.checked = false; });
document.querySelector("#save-view-button").addEventListener("click", saveCurrentView);
commandInput.addEventListener("input", () => { commandSelection = 0; renderCommands(); });
commandInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") { event.preventDefault(); commandSelection = Math.min(commandMatches.length - 1, commandSelection + 1); renderCommands(); }
  else if (event.key === "ArrowUp") { event.preventDefault(); commandSelection = Math.max(0, commandSelection - 1); renderCommands(); }
  else if (event.key === "Enter" && commandMatches[commandSelection]) { event.preventDefault(); runCommand(commandMatches[commandSelection].id); }
});

for (const dialog of [commandDialog, shortcutDialog, filterDialog]) {
  dialog.addEventListener("close", restoreDialogTrigger);
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
}

bindQuickFillSurface(quickFillSheet);

window.addEventListener("popstate", (event) => { state.scrollTop = Number(event.state?.inboxScroll) || 0; renderRoute({ focus: true }); });
window.addEventListener("resize", () => { if (filterDialog.open) filterDialog.close(); if (routeRoot() === "inbox") renderVirtualRows(); });
document.addEventListener("keydown", handleGlobalKeydown);
document.addEventListener("visibilitychange", () => {
  if (document.hidden && snackbarTimer) { clearTimeout(snackbarTimer); snackbarTimer = null; snackbarRemaining = Math.max(2000, snackbarDeadline - performance.now()); }
});

history.scrollRestoration = "manual";
if (window.location.pathname === "/" || window.location.pathname === "/index.html") navigate("/inbox", { replace: true }); else renderRoute();

const themeColor = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
document.querySelector('meta[name="theme-color"]')?.setAttribute("content", themeColor);
