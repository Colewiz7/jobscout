"use strict";

import { findPostingHighlights, HIGHLIGHT_LABELS } from "./highlight-terms.js";

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
const captureDialog = document.querySelector("#capture-dialog");
const captureForm = document.querySelector("#capture-form");
const captureError = document.querySelector("#capture-error");
const offlineState = document.querySelector("#offline-state");

const ROW_HEIGHT = 72;
const DIVIDER_HEIGHT = 32;
const LAST_VISIT_KEY = "jobseer.inboxLastVisit";
const allowedStatuses = new Set(["all", "new", "saved", "queued", "applying", "applied", "interviewing", "offer", "rejected", "archived"]);
const allowedSorts = new Set(["score", "newest", "company"]);
const blankQuickFillFields = [
  ["name", "Identity", "Full name", true],
  ["email", "Contact", "Email", true],
  ["phone", "Contact", "Phone"],
  ["location", "Contact", "Location"],
  ["linkedin", "Links", "LinkedIn"],
  ["github", "Links", "GitHub"],
  ["portfolio", "Links", "Portfolio"],
  ["school", "Education", "School"],
  ["degree", "Education", "Degree"],
  ["graduation", "Education", "Graduation date"],
  ["gpa", "Education", "GPA"],
  ["work_authorization", "Work authorization", "Work authorization"],
  ["availability", "Availability", "Availability"],
].map(([key, group, label, pinned], index) => ({ key, group, label, value: "", pinned: Boolean(pinned), sort_order: (index + 1) * 10 }));

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
  selectedKey: "", selectedKeys: new Set(), rangeAnchor: -1, copySelectionMode: false, scrollTop: 0,
  entries: [], totalHeight: 0, lastVisit: readStoredDate(LAST_VISIT_KEY), savedViews: [],
  pending: new Set(), undo: null, notesTimer: null, skeletonAt: 0, descriptions: new Map(),
  manualDescriptionDrafts: new Map(), manualDescriptionSaving: new Set(),
  aiOverviewEnabled: false, overviews: new Map(), overviewCacheAttempted: new Set(), overviewPollTimers: new Map(),
  eligibility: new Map(),
  quickFillEnabled: false, quickFillOpen: false, quickFillProfile: null,
  quickFillError: "", quickFillQuery: "", quickFillItems: [], atsOrdering: {},
  copiedByJob: new Map(), copiedKey: "", quickFillPopout: null,
  quickFillEdit: false, quickFillSaveState: "", quickFillSaveTimer: null,
  quickFillSetupSaving: false,
  quickFillSaveVersion: 0, quickFillSavedProfile: null,
  quickFillContextKey: "", quickFillContextPendingKey: "", quickFillContextLoadVersion: 0,
  quickFillContextTimer: null, quickFillContextVersion: 0, quickFillSavedContext: null,
  queueSaving: false, queueDraggedKey: "", sessionStarting: false, statusSaving: new Set(),
  applySession: null, sessionTimer: null,
  trackerData: null, trackerLoading: false, trackerError: "", trackerShowLoader: false,
  trackerView: "table", trackerStatus: "all", trackerSort: "last_activity",
  trackerQuery: "", trackerDraggedKey: "",
  companiesData: null, companyLoadedName: null, companiesLoading: false,
  companiesError: "", companiesShowLoader: false, companyNoteTimer: null,
  rules: null, rulesLoading: false, rulesError: "",
  companyLogos: {}, companyIconKeys: new Set(),
};

let commandSelection = 0;
let commandMatches = commands;
let dialogTrigger = null;
let goChordUntil = 0;
let loadingTimer = null;
let snackbarTimer = null;
let snackbarDeadline = 0;
let snackbarRemaining = 0;
let jobViewTransition = null;
let sessionCheckedAt = 0;
let sessionCheckPromise = null;
let inboxRetryTimer = null;
let sessionRetryTimer = null;
let lastNetworkNoticeAt = 0;

function authRecovery() {
  if (!navigator.onLine) return;
  // A full navigation lets the Authentik proxy renew its browser session.
  // Throttle it so a misconfigured proxy cannot trap the user in a reload loop.
  const key = "jobseer.lastAuthRecovery";
  const last = Number(sessionStorage.getItem(key) || 0);
  if (Date.now() - last < 30000) return;
  sessionStorage.setItem(key, String(Date.now()));
  window.location.reload();
}

async function refreshSession({ force = false } = {}) {
  if (!navigator.onLine) return null;
  if (!force && Date.now() - sessionCheckedAt < 60000) return null;
  if (sessionCheckPromise) return sessionCheckPromise;
  sessionCheckPromise = (async () => {
    let response;
    try {
      response = await fetch("/api/v1/session", {
        credentials: "same-origin", cache: "no-store", redirect: "manual",
        headers: { Accept: "application/json" },
      });
    } catch {
      throw new Error("Connection interrupted. Retrying shortly…");
    }
    if (response.type === "opaqueredirect" || response.status === 401 || response.status === 403
        || !response.headers.get("Content-Type")?.includes("application/json")) {
      authRecovery();
      throw new Error("Session expired. Reconnecting through Authentik…");
    }
    if (!response.ok) throw new Error("The server is temporarily unavailable. Retrying…");
    const session = await response.json();
    state.csrf = session.csrf_token || "";
    sessionCheckedAt = Date.now();
    return session;
  })().finally(() => { sessionCheckPromise = null; });
  return sessionCheckPromise;
}

async function secureWrite(url, init) {
  await refreshSession();
  const send = () => fetch(url, {
    ...init,
    credentials: "same-origin",
    headers: { ...init.headers, "X-CSRF-Token": state.csrf },
  });
  let response = await send();
  if (response.status === 403) {
    const error = (await response.clone().json().catch(() => ({}))).error || "";
    if (/security token|expired/i.test(error)) {
      await refreshSession({ force: true });
      response = await send();
    }
  }
  if (response.redirected || response.type === "opaqueredirect") authRecovery();
  return response;
}

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
  if (routeRoot() !== "inbox") routeView.scrollTop = 0;
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

function applicationUrl(job) {
  return job.public_apply_url || (job.nonpublic_site ? "" : job.url || "");
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
    return rankingScore(right.job) - rankingScore(left.job)
      || (Number(left.job.age_days) || 0) - (Number(right.job.age_days) || 0);
  });
  const jobs = matches.map(({ job }) => job);
  const fresh = jobs.filter(isNewSinceVisit);
  return fresh.length ? [...fresh, ...jobs.filter((job) => !isNewSinceVisit(job))] : jobs;
}

function rankingScore(job) {
  return (Number(job?.score) || 0) + (Number(job?.rule_boost) || 0);
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
  const tabs = [["new", "New"], ["saved", "Saved"], ["queued", "Queued"], ["all", "All"]];
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

const lightLogoTiles = new Set([
  "amd", "cesiumastro", "costar group", "dv trading", "ge aerospace",
  "micron", "rtx", "the aerospace corporation", "vertiv",
]);

function companyLogoMarkup(company, className = "job-logo") {
  const key = normalizeCompany(company);
  const filename = state.companyLogos[key];
  const source = filename ? `/static/company-logos/${encodeURIComponent(filename)}`
    : state.companyIconKeys.has(key) ? `/api/v1/company-icons/${encodeURIComponent(company)}` : "";
  const tile = lightLogoTiles.has(key) ? " data-logo-light" : "";
  const loading = className === "company-monogram" ? "lazy" : "eager";
  return `<span class="${className}"${source ? tile : ""} aria-hidden="true"><span class="logo-initials">${escapeHtml(initials(company))}</span>${source ? `<img src="${source}" width="64" height="64" loading="${loading}" decoding="async" alt="">` : ""}</span>`;
}

function jobRowMarkup(entry) {
  const job = entry.job;
  const sources = sourceList(job);
  const selected = job.dedupe_key === state.selectedKey;
  const bulkSelected = state.selectedKeys.has(job.dedupe_key);
  const fresh = isNewSinceVisit(job);
  const chips = [];
  if (isRemote(job)) chips.push("Remote");
  if (job.archived_by_rule) chips.push("Archived by rule");
  if (job.repost_count) chips.push("Reposted");
  for (const tag of job.tags || []) chips.push(tag);
  if (job.connections_count) chips.push(`${new Intl.NumberFormat().format(job.connections_count)} connections`);
  if (sources.length > 1) chips.push(`${sources.length} sources`);
  if (job.nonpublic_site) chips.unshift("Check apply site");
  const location = job.location ? `<span class="row-location">${escapeHtml(job.location)}</span>` : "";
  return `<button class="job-row interactive" type="button" role="option" data-offset="${entry.offset}" data-job-key="${escapeHtml(job.dedupe_key)}" data-job-index="${entry.jobIndex}" aria-selected="${selected || bulkSelected}" tabindex="${selected ? "0" : "-1"}">
    ${companyLogoMarkup(job.company)}
    ${fresh ? '<span class="unread-dot" aria-label="Unread"></span>' : ""}
    <span class="job-row-copy"><span class="job-row-title">${job.ghost_job ? '<span class="row-warning" aria-label="Repeated posting pattern" title="Repeated posting pattern">!</span>' : ""}${escapeHtml(job.title)}</span><span class="job-row-meta"><span>${escapeHtml(job.company)}</span>${location}</span></span>
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
  const offsets = new Set(visible.map((entry) => String(entry.offset)));
  for (const item of [...layer.children]) {
    if (!offsets.has(item.dataset.offset)) item.remove();
  }
  const mounted = new Map([...layer.children].map((item) => [item.dataset.offset, item]));
  let next = layer.firstElementChild;
  for (const entry of visible) {
    let item = mounted.get(String(entry.offset));
    if (!item) {
      const template = document.createElement("template");
      template.innerHTML = entry.type === "divider"
        ? `<div class="new-divider" data-offset="${entry.offset}"><span>New since last visit</span><span>${entry.count}</span></div>`
        : entry.type === "seen-divider"
          ? `<div class="new-divider seen" data-offset="${entry.offset}"><span>Seen earlier</span></div>`
          : jobRowMarkup(entry);
      item = template.content.firstElementChild;
      item.style.transform = `translateY(${entry.offset}px)`;
    }
    if (item !== next) layer.insertBefore(item, next);
    next = item.nextElementSibling;
  }
}

function emptyListMarkup() {
  const filtered = Boolean(state.query || state.source || state.remote || state.status !== "new");
  return `<div class="list-empty"><h2>${filtered ? "No roles match this view." : "Your inbox is clear."}</h2><p>${filtered ? "Change or clear the active filters." : "New roles will appear here when the scout finds them."}</p>${filtered ? '<button class="tonal-button interactive" type="button" data-clear-all-filters>Clear filters</button>' : ""}</div>`;
}

async function copyInboxJobs(jobs, { finishSelection = false } = {}) {
  if (!jobs.length) return;
  const contents = jobs.map((job) => [job.company, job.title, job.location, job.url]
    .filter(Boolean).map((value) => String(value).replace(/\s+/g, " ").trim()).join(" — ")).join("\n");
  try {
    await navigator.clipboard.writeText(contents);
    if (finishSelection) {
      state.copySelectionMode = false;
      state.selectedKeys.clear();
      renderInbox();
    }
    showSnackbar(`Copied ${new Intl.NumberFormat().format(jobs.length)} ${jobs.length === 1 ? "job" : "jobs"}.`);
  } catch {
    showSnackbar("Couldn't copy the list. Check clipboard permission and retry.");
  }
}

function copyInboxScope(scope) {
  const jobs = filteredJobs();
  if (scope === "multiple") {
    state.copySelectionMode = true;
    state.rangeAnchor = -1;
    renderInbox();
    document.querySelector("#job-viewport")?.focus({ preventScroll: true });
    return;
  }
  const targets = scope === "new" ? jobs.filter((job) => job.status === "new")
    : scope === "single" ? jobs.filter((job) => job.dedupe_key === state.selectedKey)
      : jobs;
  copyInboxJobs(targets);
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

function highlightPostingText(value, seen, used) {
  const text = String(value || "");
  let html = "";
  let previous = 0;
  for (const match of findPostingHighlights(text, { seen })) {
    used.add(match.category);
    html += escapeHtml(text.slice(previous, match.start));
    html += `<mark class="posting-highlight posting-highlight--${match.category}" aria-label="${escapeHtml(HIGHLIGHT_LABELS[match.category])}: ${escapeHtml(match.text)}">${escapeHtml(match.text)}</mark>`;
    previous = match.end;
  }
  return html + escapeHtml(text.slice(previous));
}

function descriptionMarkup(job, { compact = false } = {}) {
  const detail = state.descriptions.get(job.dedupe_key);
  if (!detail) return "";
  if (detail.loading && detail.showLoader) return '<section class="detail-section description-skeleton skeleton" aria-label="Loading posting description"></section>';
  if (detail.error) return `<section class="detail-section inline-error" role="alert"><div><h2>Description unavailable</h2><p>${escapeHtml(detail.error)}</p><details class="manual-description" ${state.manualDescriptionDrafts.has(job.dedupe_key) ? "open" : ""}><summary>Paste the description from the original posting</summary><label for="manual-description-text">Posting text</label><textarea id="manual-description-text" rows="8" placeholder="Paste the job description here">${escapeHtml(state.manualDescriptionDrafts.get(job.dedupe_key) || "")}</textarea><button class="tonal-button interactive" type="button" data-save-manual-description ${state.manualDescriptionSaving.has(job.dedupe_key) ? "disabled" : ""}>Save description</button></details></div><button class="text-button interactive" type="button" data-retry-description>Retry</button></section>`;
  const value = detail.value || {};
  const sections = Array.isArray(value.sections) ? value.sections.filter((section) => section?.text) : [];
  const labels = { about: "About the company", role: "The role", responsibilities: "What you'll do", requirements: "What you'll need", nice_to_have: "Nice to have", benefits: "Benefits", logistics: "Practical details" };
  const order = { role: 0, responsibilities: 1, requirements: 2, logistics: 3, nice_to_have: 4, benefits: 5, about: 6 };
  const sorted = [...sections].sort((left, right) => (order[left.key] ?? 7) - (order[right.key] ?? 7));
  const usedHighlights = new Set();
  const parsed = sorted.map((section) => {
    const seen = new Set();
    const lines = String(section.text).split(/\n+/).map((line) => line.trim()).filter(Boolean);
    const content = lines.map((line) => `<p${line.includes(":") ? ' class="posting-colon-line"' : ""}>${highlightPostingText(line.replace(/^[-*•]\s*/, ""), seen, usedHighlights)}</p>`).join("");
    const title = section.title && section.title !== "Overview" ? section.title : labels[section.key] || "Details";
    return `<details class="parsed-section" data-section-key="${escapeHtml(section.key)}" ${!compact && ["role", "responsibilities", "requirements"].includes(section.key) ? "open" : ""}><summary>${escapeHtml(title)}</summary><div>${content}</div></details>`;
  }).join("");
  const legend = [...usedHighlights].map((kind) => `<span class="highlight-key-item highlight-key-item--${kind}">${escapeHtml(HIGHLIGHT_LABELS[kind])}</span>`).join("");
  const original = value.description_text ? `<details class="parsed-section original-posting"><summary>Original posting</summary><div class="original-text">${escapeHtml(value.description_text)}</div></details>` : "";
  return parsed || original ? `<section class="detail-section posting-sections" aria-labelledby="description-heading"><h2 id="description-heading">About the role</h2>${legend ? `<div class="highlight-key" aria-label="Highlight key">${legend}</div>` : ""}${parsed}${original}</section>` : "";
}

function overviewMarkup(job) {
  if (!state.aiOverviewEnabled) return "";
  const entry = state.overviews.get(job.dedupe_key);
  if (!entry) return `<section class="detail-section ai-overview overview-resolved"><div class="overview-heading"><h2>AI overview</h2><span>Selected from the posting</span></div><button class="tonal-button interactive" type="button" data-generate-overview>Generate overview</button></section>`;
  if (entry.checking) return `<section class="detail-section ai-overview overview-checking" aria-label="Checking for a saved AI overview"><div class="overview-heading"><h2>AI overview</h2><span>Checking saved overview</span></div><div class="overview-placeholder${entry.showSkeleton ? " skeleton" : ""}" aria-hidden="true"></div></section>`;
  if (entry.loading || entry.status === "queued" || entry.status === "running") return `<section class="detail-section ai-overview overview-checking" aria-live="polite"><div class="overview-heading"><h2>AI overview</h2><span>${entry.status === "queued" ? "Queued for the next run" : "Reading the posting"}</span></div><div class="overview-placeholder skeleton" aria-hidden="true"></div></section>`;
  if (entry.error) return `<section class="detail-section overview-error"><h2>AI overview</h2><p>${escapeHtml(entry.error)}</p><button class="text-button interactive" type="button" data-retry-overview>Retry overview</button></section>`;
  if (!entry.items?.length) return `<section class="detail-section overview-error"><h2>AI overview</h2><p>There isn't enough readable posting detail for an overview. Open the original posting or paste its description, then retry.</p><button class="text-button interactive" type="button" data-retry-overview>Retry overview</button></section>`;
  const labels = { work: "Responsibilities", skills: "Skills", required: "Required", preferred: "Preferred", pay: "Pay", location: "Location & work mode", dates: "Dates & duration" };
  const usedHighlights = new Set();
  const groups = Object.entries(labels).map(([kind, label]) => {
    const items = entry.items.filter((item) => item.kind === kind);
    const terms = [...new Set(items.flatMap((item) => Array.isArray(item.terms) && item.terms.length ? item.terms : [item.text]).filter(Boolean))];
    const listed = ["work", "required", "preferred", "dates"].includes(kind) && terms.length > 1;
    const highlighted = (term) => highlightPostingText(term, new Set(), usedHighlights);
    const value = listed
      ? `<ul class="overview-terms">${terms.map((term) => `<li>${highlighted(term)}</li>`).join("")}</ul>`
      : terms.map(highlighted).join(", ");
    return terms.length ? `<div class="overview-cell" data-overview-kind="${kind}"><dt>${label}</dt><dd>${value}</dd></div>` : "";
  }).join("");
  const fallback = entry.source === "posting";
  const legend = [...usedHighlights].map((kind) => `<span class="highlight-key-item highlight-key-item--${kind}">${escapeHtml(HIGHLIGHT_LABELS[kind])}</span>`).join("");
  return `<section class="detail-section ai-overview overview-resolved" aria-labelledby="ai-overview-heading"><div class="overview-heading"><h2 id="ai-overview-heading">${fallback ? "Posting overview" : "AI overview"}</h2><span>${fallback ? "Model response incomplete; showing source-checked facts" : "Only facts stated in the posting"}</span></div>${legend ? `<div class="highlight-key overview-highlight-key" aria-label="Highlight key">${legend}</div>` : ""}<dl class="overview-grid">${groups}</dl></section>`;
}

function atGlanceMarkup(job) {
  if (state.aiOverviewEnabled && state.overviews.get(job.dedupe_key)?.items?.length) return "";
  const value = state.descriptions.get(job.dedupe_key)?.value || {};
  const sections = Array.isArray(value.sections) ? value.sections : [];
  const bullets = [];
  for (const key of ["role", "responsibilities", "requirements", "logistics", "about"]) {
    if (key === "about" && bullets.length >= 3) break;
    for (const section of sections.filter((item) => item?.key === key)) {
      for (const line of String(section.text).split(/\n+|(?<=[.!?])\s+/)) {
      const clean = line.trim().replace(/^[-*•]\s*/, "");
      if (clean.length >= 5 && clean.length <= 240 && !bullets.includes(clean)) bullets.push(clean);
      if (bullets.length >= 5) break;
      }
      if (bullets.length >= 5) break;
    }
    if (bullets.length >= 5) break;
  }
  if (bullets.length < 2) return "";
  return `<section class="detail-section at-glance" aria-labelledby="at-glance-heading"><h2 id="at-glance-heading">At a glance</h2><ul>${bullets.slice(0, 5).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>`;
}

function eligibilityMarkup(job) {
  if (!state.quickFillEnabled) return "";
  const entry = state.eligibility.get(job.dedupe_key);
  if (!entry || entry.loading || entry.error) return "";
  const result = entry.value;
  if (!result) return "";
  const active = (result.blockers || []).filter((item) => !item.overridden);
  const findings = active.length ? active : result.warnings || [];
  if (!findings.length && !result.overridden) return "";
  const heading = active.length ? "Do not apply" : result.overridden ? "Override recorded" : "Check eligibility";
  const intro = active.length
    ? "Profile conflicts with an explicit requirement in this posting."
    : result.overridden ? "The original blocker stays in the activity record." : "Confirm these details before applying.";
  const evidence = (active.length ? active : result.overridden ? result.blockers : findings).map((item) => `
    <li><span aria-hidden="true">${item.overridden ? "✓" : "!"}</span><div><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(item.evidence)}</p><small>${escapeHtml(item.comparison)} · ${escapeHtml(item.provenance)}</small></div></li>`).join("");
  return `<section class="eligibility-banner${active.length ? " is-blocked" : ""}" aria-labelledby="eligibility-heading"><div><p class="eyebrow">Eligibility</p><h2 id="eligibility-heading"><span aria-hidden="true">${active.length ? "!" : "✓"}</span>${heading}</h2><p>${intro}</p></div><ul>${evidence}</ul>${active.length ? '<button class="outlined-button interactive" type="button" data-override-eligibility>Override verdict</button>' : ""}</section>`;
}

function jobHasActiveBlocker(job) {
  return state.eligibility.get(job?.dedupe_key)?.value?.verdict === "do_not_apply";
}

function skillMatchMarkup(job) {
  if (!state.quickFillEnabled) return "";
  const result = state.eligibility.get(job.dedupe_key)?.value;
  const requirements = result?.requirements || [];
  if (!requirements.length) return "";
  const matched = requirements.filter((item) => item.matched);
  const missing = requirements.filter((item) => item.required && !item.matched);
  return `<section class="detail-section skill-match" aria-labelledby="skill-match-heading"><div class="section-heading"><div><h2 id="skill-match-heading">Skill match</h2><p>${escapeHtml(result.match_band)}</p></div></div>${matched.length ? `<div class="skill-chips" aria-label="Skills found in Profile">${matched.map((item) => `<span><span aria-hidden="true">✓</span>${escapeHtml(item.skill)}</span>`).join("")}</div>` : ""}${missing.length ? `<div class="missing-skills"><h3>Missing required skills</h3><ul>${missing.map((item) => `<li><strong>${escapeHtml(item.skill)}</strong><span>${escapeHtml(item.evidence)}</span></li>`).join("")}</ul></div>` : ""}<details class="requirement-evidence"><summary>Requirement evidence</summary><ul>${requirements.map((item) => `<li><span>${escapeHtml(item.skill)}</span><small>${escapeHtml(item.provenance)}</small></li>`).join("")}</ul></details></section>`;
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
  const eligibilityBlocked = jobHasActiveBlocker(job);
  const description = state.descriptions.get(job.dedupe_key)?.value;
  const visibleStatus = job.liveness_status === "closed" ? "Posting closed" : statusLabel(job.status || "new");
  const history = (state.trackerData?.history || []).filter((item) => item.dedupe_key === job.dedupe_key).slice(-6).reverse();
  const interviews = (state.trackerData?.interviews || []).filter((item) => item.dedupe_key === job.dedupe_key);
  const activityDetails = history.length ? `<div class="activity-timeline">${history.map((item) => `<div>${statusMarkup(item.to_status)}<time datetime="${escapeHtml(item.changed_at)}">${escapeHtml(formatAbsolute(item.changed_at))}</time></div>`).join("")}</div>` : "";
  const interviewDetails = interviews.length ? `<div class="activity-interviews">${interviews.map((item) => `<div><strong>Interview</strong><time datetime="${escapeHtml(item.starts_at)}">${escapeHtml(formatAbsolute(item.starts_at))}</time>${item.location ? `<span>${escapeHtml(item.location)}</span>` : ""}</div>`).join("")}</div>` : "";
  const repostWarning = job.ghost_job ? `<p class="company-warning repost-warning"><span aria-hidden="true">!</span>This title has appeared ${new Intl.NumberFormat().format(job.repost_count)} times in 90 days: ${job.repost_dates.map((value) => escapeHtml(value)).join(", ")}. This is an observation, not a claim about the employer.</p>` : "";
  const ruleNotice = job.archived_by_rule ? `<div class="rule-notice"><div><strong><span aria-hidden="true">—</span> Archived by rule</strong><p>${escapeHtml(job.archived_by_rule)}</p></div><button class="text-button interactive" type="button" data-undo-rule-action="${job.rule_action_id}">Undo</button></div>` : "";
  const siteWarning = job.nonpublic_site ? `<p class="company-warning"><span aria-hidden="true">!</span>This link came from a non-public Workday site. ${job.public_apply_url ? "Apply opens the verified public RTX posting." : "No exact public posting was verified. Find it on the employer’s public careers site before applying."}</p>` : "";
  const applyingActions = job.status === "applying"
    ? `<button class="filled-button interactive" type="button" data-mark-applied="${escapeHtml(job.dedupe_key)}" ${pending || state.statusSaving.has(job.dedupe_key) ? "disabled" : ""}>Mark applied</button><button class="tonal-button interactive" type="button" data-move-to-queue="${escapeHtml(job.dedupe_key)}" ${pending || state.statusSaving.has(job.dedupe_key) ? "disabled" : ""}>Back to queue</button>`
    : "";
  const normalActions = job.status === "applying" ? applyingActions
    : `<button class="filled-button interactive" type="button" ${job.status === "applied" ? `data-move-to-queue="${escapeHtml(job.dedupe_key)}"` : 'data-status-action="queued"'} ${pending || eligibilityBlocked || job.status === "queued" ? 'aria-disabled="true"' : ""}>${job.status === "queued" ? "Queued" : job.status === "applied" ? "Back to queue" : "Queue"}</button><button class="tonal-button interactive" type="button" data-status-action="saved" ${pending || job.status === "saved" ? 'aria-disabled="true"' : ""}>${job.status === "saved" ? "Saved" : "Save"}</button><button class="outlined-button interactive" type="button" ${job.status === "applied" ? 'data-open-posting' : 'data-apply-now'} ${!applicationUrl(job) || pending || eligibilityBlocked ? 'aria-disabled="true"' : ""}>${job.status === "applied" ? "Open posting" : "Apply now"}</button>`;
  return `<article class="job-detail" aria-labelledby="job-title">
    <header class="job-detail-header"><button class="detail-back text-button interactive" type="button" data-back-to-list>← Inbox</button><div class="detail-identity">${companyLogoMarkup(job.company, "detail-company-logo")}<div class="detail-heading"><h1 id="job-title" tabindex="-1">${escapeHtml(job.title)}</h1><a href="/companies/${encodeURIComponent(job.company || "")}" data-route>${escapeHtml(job.company)}</a>${job.location ? `<p class="detail-location">${escapeHtml(job.location)}</p>` : ""}</div></div>
      <dl class="fact-strip">${factItem("Term", job.terms)}${factItem("Deadline", deadlineLabel(description?.deadline))}${factItem("Posted", formatDate(job.first_seen))}${factItem("Source", sourceText)}</dl>
      <div class="detail-actions" aria-label="Job actions">${normalActions}${state.focus ? '<button class="text-button interactive" type="button" data-exit-focus>Show list</button>' : ""}</div>
    </header>
    <div class="job-detail-body">
      ${eligibilityMarkup(job)}
      ${ruleNotice}
      ${siteWarning}
      ${repostWarning}
      ${overviewMarkup(job)}
      ${atGlanceMarkup(job)}
      ${skillMatchMarkup(job)}
      ${descriptionMarkup(job)}
      <section class="detail-section" aria-labelledby="posting-details-heading"><h2 id="posting-details-heading">Posting details</h2><dl class="detail-facts">${factItem("Status", visibleStatus)}${factItem("First seen", formatAbsolute(job.first_seen))}${factItem("Last seen", formatAbsolute(job.last_seen))}${factItem("Sources", sourceText)}${factItem("Tags", (job.tags || []).join(", "))}${factItem("Ranking boost", job.rule_boost ? `+${job.rule_boost} from rules` : "")}${factItem("Connections", job.connections_count ? `${new Intl.NumberFormat().format(job.connections_count)} at company` : "")}</dl>${job.url ? `<a class="original-link" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">Open original posting ${icons.external}</a>` : ""}</section>
      <section class="detail-section" aria-labelledby="activity-heading"><h2 id="activity-heading">Activity</h2><div class="status-line"><span aria-hidden="true"></span><strong>${escapeHtml(visibleStatus)}</strong>${job.application_updated_at ? `<time datetime="${escapeHtml(job.application_updated_at)}" title="${escapeHtml(formatAbsolute(job.application_updated_at))}">${escapeHtml(formatDate(job.application_updated_at))}</time>` : ""}</div><div class="activity-status-control"><span>Correct status</span>${statusSelectMarkup(job, "data-job-status-select")}</div>${activityDetails}${interviewDetails}${job.resume_name ? `<p class="resume-sent"><span>Resume sent</span><strong>${escapeHtml(job.resume_name)}</strong></p>` : ""}<label class="notes-field" for="job-notes"><span>Notes</span><textarea id="job-notes" rows="5" placeholder="Add context for your next step">${escapeHtml(job.notes || "")}</textarea><small id="notes-state">Saved automatically</small></label></section>
      ${recentCompanyWarning || otherRoles.length ? `<section class="detail-section" aria-labelledby="company-history-heading"><h2 id="company-history-heading">Company history</h2>${recentCompanyWarning}<div class="company-roles">${otherRoles.map((other) => `<button class="company-role interactive" type="button" data-job-key="${escapeHtml(other.dedupe_key)}"><span>${escapeHtml(other.title)}</span><span>${escapeHtml(statusLabel(other.status || "new"))}</span></button>`).join("")}</div></section>` : ""}
    </div>
  </article>`;
}

function bulkBarMarkup() {
  const count = state.selectedKeys.size;
  if (state.copySelectionMode) return `<div class="bulk-bar copy-selection-bar" aria-label="Copy selection"><span><strong>${new Intl.NumberFormat().format(count)}</strong> selected · Click jobs, or Shift-click for a range</span><button class="tonal-button interactive" type="button" data-copy-selected ${count ? "" : "disabled"}>Copy selected</button><button class="text-button interactive" type="button" data-cancel-copy-selection>Cancel</button></div>`;
  if (!count) return "";
  return `<div class="bulk-bar" aria-label="Bulk actions"><span><strong>${new Intl.NumberFormat().format(count)}</strong> selected</span><button class="text-button interactive" type="button" data-bulk-action="saved">Save</button><button class="text-button interactive" type="button" data-bulk-action="queued">Queue</button><button class="text-button interactive" type="button" data-bulk-action="archived">Dismiss</button><button class="text-button interactive" type="button" data-dismiss-company>Dismiss company</button><button class="icon-button interactive" type="button" data-clear-selection aria-label="Clear selection">${icons.close}</button></div>`;
}

function copyMenuMarkup(jobs) {
  const newCount = jobs.filter((job) => job.status === "new").length;
  const single = jobs.some((job) => job.dedupe_key === state.selectedKey);
  return `<details class="copy-menu"><summary class="icon-button interactive" aria-label="Copy jobs" title="Copy jobs">${icons.copy}<span class="copy-menu-caret" aria-hidden="true">⌄</span></summary><div class="copy-menu-panel" aria-label="Copy options"><button type="button" data-copy-scope="all">All in view <span>${new Intl.NumberFormat().format(jobs.length)}</span></button><button type="button" data-copy-scope="new" ${newCount ? "" : "disabled"}>New in view <span>${new Intl.NumberFormat().format(newCount)}</span></button><button type="button" data-copy-scope="single" ${single ? "" : "disabled"}>Current job</button><button type="button" data-copy-scope="multiple">Select multiple…</button></div></details>`;
}

function inboxMarkup() {
  const jobs = filteredJobs();
  buildEntries(jobs);
  if (!state.selectedKey || !state.jobs.some((job) => job.dedupe_key === state.selectedKey)) state.selectedKey = jobs[0]?.dedupe_key || "";
  const selectedJob = state.jobs.find((job) => job.dedupe_key === state.selectedKey);
  const chips = activeFilterChips();
  return `<section class="inbox-page${state.focus ? " is-focus" : ""}${selectedKeyFromPath() ? " has-route-selection" : ""}" aria-label="Inbox">
    <aside class="inbox-list" aria-label="Job inbox"><div class="list-header"><div class="list-title-row"><h1 tabindex="-1">Inbox</h1><span>${new Intl.NumberFormat().format(jobs.length)}</span></div>${savedViewsMarkup()}<label class="job-search" for="job-search">${icons.search}<input id="job-search" type="search" autocomplete="off" placeholder="Search jobs" value="${escapeHtml(state.query)}" aria-label="Search jobs" aria-keyshortcuts="/"></label><div class="list-tools"><div class="status-tabs" role="tablist" aria-label="Job status">${visibleStatusTabs()}</div><button class="icon-button interactive" type="button" data-open-filters aria-label="Filter jobs" title="Filter jobs">${icons.filter}</button>${copyMenuMarkup(jobs)}<label class="sort-field"><span class="visually-hidden">Sort jobs</span><select id="job-sort" aria-label="Sort jobs"><option value="score" ${state.sort === "score" ? "selected" : ""}>Best match</option><option value="newest" ${state.sort === "newest" ? "selected" : ""}>Newest</option><option value="company" ${state.sort === "company" ? "selected" : ""}>Company</option></select></label></div>${chips ? `<div class="active-filters">${chips}</div>` : ""}</div>
      <div class="job-viewport" id="job-viewport" role="listbox" aria-label="Jobs" aria-multiselectable="true" ${state.copySelectionMode ? 'tabindex="-1"' : ""}>${jobs.length ? '<div class="job-list-layer" id="job-list-layer"></div>' : emptyListMarkup()}</div>${bulkBarMarkup()}</aside>
    <main class="reading-pane" id="inbox-reading-pane">${detailMarkup(selectedJob)}</main>
  </section>`;
}

function loadingMarkup() {
  return `<section class="inbox-page loading-view" aria-label="Loading inbox"><aside class="inbox-list"><div class="list-header skeleton-block"></div><div class="skeleton-rows">${Array.from({ length: 6 }, () => '<div class="skeleton-row skeleton"></div>').join("")}</div></aside><main class="reading-pane"><div class="skeleton-detail skeleton"></div></main></section>`;
}

function errorMarkup(message) {
  return `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load the inbox.</h1><p>${escapeHtml(message)} Open JobSeer through Authentik, then retry.</p><button class="tonal-button interactive" type="button" data-retry-jobs>Retry</button></div></section>`;
}

async function loadDescription(key, { force = false } = {}) {
  if (!key || state.descriptions.has(key)) return;
  const loading = { loading: true, showLoader: false, value: null, error: "" };
  state.descriptions.set(key, loading);
  const indicator = setTimeout(() => {
    loading.showLoader = true;
    loading.shownAt = performance.now();
    if (state.selectedKey === key && routeRoot() === "inbox") renderSelectedJob();
    if (state.selectedKey === key && window.location.pathname.startsWith("/queue/session/")) renderApplySession();
  }, 300);
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/description${force ? "?retry=1" : ""}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
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
    if (value.description_text) showCachedOverview(key);
  } catch (error) {
    const hold = loading.shownAt ? Math.max(0, 500 - (performance.now() - loading.shownAt)) : 0;
    if (hold) await new Promise((resolve) => setTimeout(resolve, hold));
    state.descriptions.set(key, { loading: false, showLoader: false, value: null, error: error instanceof Error ? error.message : "The source did not return a description." });
  } finally {
    clearTimeout(indicator);
    if (state.quickFillEnabled) loadEligibility(key);
    if (state.selectedKey === key && routeRoot() === "inbox") renderSelectedJob();
    if (state.selectedKey === key && window.location.pathname.startsWith("/queue/session/")) renderApplySession();
  }
}

async function loadOverview(key, { force = false } = {}) {
  if (!state.aiOverviewEnabled || !key || (!force && state.overviews.get(key)?.items?.length)) return;
  state.overviews.set(key, { loading: true, status: "running", items: [], error: "" });
  updateOverviewUI(key);
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/overview`, {
      method: "POST", credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRF-Token": state.csrf },
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The overview could not be queued.");
    const payload = await response.json();
    applyOverviewPayload(key, payload);
  } catch (error) {
    state.overviews.set(key, { loading: false, items: [], error: error instanceof Error ? error.message : "The overview could not be queued." });
  }
  updateOverviewUI(key);
}

function updateOverviewUI(key) {
  if (state.selectedKey !== key) return;
  if (routeRoot() === "inbox") renderSelectedJob();
  if (window.location.pathname.startsWith("/queue/session/")) renderApplySession();
}

function scheduleOverviewPoll(key) {
  if (state.overviewPollTimers.has(key)) return;
  const timer = setTimeout(async () => {
    state.overviewPollTimers.delete(key);
    if (state.selectedKey !== key) return;
    try {
      const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/overview?cached=1`, {
        credentials: "same-origin", headers: { Accept: "application/json" },
      });
      if (response.ok) applyOverviewPayload(key, await response.json());
    } catch { /* Keep the previous state; a later view can retry. */ }
    updateOverviewUI(key);
  }, 5000);
  state.overviewPollTimers.set(key, timer);
}

function applyOverviewPayload(key, payload) {
  if (payload?.items?.length) {
    state.overviews.set(key, { loading: false, items: payload.items, source: payload.source || "ai", error: "" });
    const timer = state.overviewPollTimers.get(key);
    if (timer) clearTimeout(timer);
    state.overviewPollTimers.delete(key);
  } else if (["queued", "running"].includes(payload?.status)) {
    state.overviews.set(key, { loading: false, status: payload.status, items: [], error: "" });
    scheduleOverviewPoll(key);
  } else if (payload?.status === "failed") {
    state.overviews.set(key, { loading: false, items: [], error: payload.error || "This overview could not be completed." });
  } else {
    state.overviews.delete(key);
  }
}

async function showCachedOverview(key) {
  if (!state.aiOverviewEnabled || state.overviews.has(key) || state.overviewCacheAttempted.has(key)) return;
  state.overviewCacheAttempted.add(key);
  const checking = { checking: true, showSkeleton: false, skeletonAt: 0 };
  state.overviews.set(key, checking);
  const update = () => {
    if (routeRoot() === "inbox" && state.selectedKey === key) renderSelectedJob();
    if (window.location.pathname.startsWith("/queue/session/") && state.selectedKey === key) renderApplySession();
  };
  update();
  const indicator = setTimeout(() => {
    if (state.overviews.get(key) !== checking) return;
    checking.showSkeleton = true;
    checking.skeletonAt = performance.now();
    update();
  }, 300);
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/overview?cached=1`, {
      credentials: "same-origin", headers: { Accept: "application/json" },
    });
    const payload = response.ok ? await response.json() : null;
    const hold = checking.skeletonAt ? Math.max(0, 500 - (performance.now() - checking.skeletonAt)) : 0;
    if (hold) await new Promise((resolve) => setTimeout(resolve, hold));
    if (state.overviews.get(key) !== checking) return;
    applyOverviewPayload(key, payload);
  } catch {
    if (state.overviews.get(key) === checking) state.overviews.delete(key);
  } finally {
    clearTimeout(indicator);
    update();
  }
}

async function saveManualDescription() {
  const key = state.selectedKey;
  const text = document.querySelector("#manual-description-text")?.value || "";
  if (!key || state.manualDescriptionSaving.has(key)) return;
  state.manualDescriptionDrafts.set(key, text);
  state.manualDescriptionSaving.add(key);
  const button = document.querySelector("[data-save-manual-description]");
  if (button) button.disabled = true;
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/description`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't save that description.");
    const value = (await response.json()).description || {};
    state.descriptions.set(key, { loading: false, showLoader: false, value, error: "" });
    state.manualDescriptionDrafts.delete(key);
    state.overviews.delete(key);
    state.overviewCacheAttempted.delete(key);
    showCachedOverview(key);
    if (state.selectedKey === key && routeRoot() === "inbox") renderSelectedJob();
    showSnackbar("Description saved from the original posting.");
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "Couldn't save that description.");
  } finally {
    state.manualDescriptionSaving.delete(key);
    if (button?.isConnected) button.disabled = false;
  }
}

async function loadEligibility(key, { force = false } = {}) {
  if (!state.quickFillEnabled || !key || (!force && state.eligibility.has(key))) return;
  state.eligibility.set(key, { loading: true, value: null, error: "" });
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/eligibility`, {
      credentials: "same-origin", headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Eligibility did not respond.");
    state.eligibility.set(key, { loading: false, value: (await response.json()).eligibility, error: "" });
  } catch (error) {
    state.eligibility.set(key, { loading: false, value: null, error: error instanceof Error ? error.message : "Eligibility did not respond." });
  }
  if (routeRoot() === "inbox" && state.selectedKey === key) renderSelectedJob();
}

async function overrideEligibility() {
  const key = state.selectedKey;
  if (!key) return;
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/eligibility/override`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ note: "Overridden from the reading pane" }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The override could not be recorded.");
    state.eligibility.set(key, { loading: false, value: (await response.json()).eligibility, error: "" });
    renderInbox({ focus: true });
    showSnackbar("Eligibility override recorded.");
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "The override could not be recorded.");
  }
}

function renderInbox({ focus = false } = {}) {
  if (state.error) routeView.innerHTML = errorMarkup(state.error);
  else if (!state.loaded) routeView.innerHTML = state.skeletonAt ? loadingMarkup() : "";
  else routeView.innerHTML = inboxMarkup();
  document.title = "Inbox — JobSeer";
  const pane = document.querySelector("#inbox-reading-pane");
  if (pane) pane.dataset.jobKey = state.selectedKey;
  const viewport = document.querySelector("#job-viewport");
  if (viewport && state.entries.length) {
    viewport.scrollTop = Math.min(state.scrollTop, Math.max(0, state.totalHeight - viewport.clientHeight));
    viewport.addEventListener("scroll", () => {
      if (!viewport.isConnected || document.querySelector("#job-viewport") !== viewport) return;
      state.scrollTop = viewport.scrollTop;
      renderVirtualRows();
    }, { passive: true });
    renderVirtualRows();
  }
  if (state.loaded && state.selectedKey) loadDescription(state.selectedKey);
  if (state.loaded && state.selectedKey) showCachedOverview(state.selectedKey);
  if (state.quickFillEnabled && (state.quickFillOpen || state.quickFillPopout)) {
    const job = currentJob();
    const contextKey = job ? `${job.dedupe_key}\u0000${job.company}` : "";
    if (contextKey && contextKey !== state.quickFillContextKey) loadQuickFillContext(); else renderQuickFill();
  }
  if (focus) document.querySelector("#job-title, .inbox-list h1")?.focus({ preventScroll: true });
}

function syncVisibleJobSelection() {
  for (const row of document.querySelectorAll("#job-list-layer .job-row")) {
    const selected = row.dataset.jobKey === state.selectedKey;
    const bulkSelected = state.selectedKeys.has(row.dataset.jobKey);
    row.setAttribute("aria-selected", String(selected || bulkSelected));
    row.tabIndex = selected ? 0 : -1;
    const check = row.querySelector(".selection-check");
    if (bulkSelected && !check) row.insertAdjacentHTML("beforeend", '<span class="selection-check" aria-label="Selected">✓</span>');
    if (!bulkSelected) check?.remove();
  }
  const list = document.querySelector(".inbox-list");
  const oldBar = list?.querySelector(".bulk-bar");
  const nextBar = bulkBarMarkup();
  if (oldBar) oldBar.outerHTML = nextBar;
  else if (nextBar) list?.insertAdjacentHTML("beforeend", nextBar);
}

function renderSelectedJob() {
  const pane = document.querySelector("#inbox-reading-pane");
  if (!pane) { renderInbox(); return; }
  const sameJob = pane.dataset.jobKey === state.selectedKey;
  const note = pane.querySelector("#job-notes");
  const noteDraft = sameJob ? note?.value : null;
  const noteFocused = note === document.activeElement;
  const noteStart = noteFocused ? note.selectionStart : null;
  const noteEnd = noteFocused ? note.selectionEnd : null;
  const previousScroll = pane.scrollTop;
  const job = state.jobs.find((item) => item.dedupe_key === state.selectedKey);
  pane.innerHTML = detailMarkup(job);
  document.querySelector(".inbox-page")?.classList.toggle("has-route-selection", Boolean(selectedKeyFromPath()));
  pane.dataset.jobKey = state.selectedKey;
  pane.scrollTop = sameJob ? previousScroll : 0;
  if (noteDraft !== null) pane.querySelector("#job-notes").value = noteDraft;
  if (noteFocused) {
    const replacement = pane.querySelector("#job-notes");
    replacement?.focus({ preventScroll: true });
    replacement?.setSelectionRange(noteStart, noteEnd);
  }
  syncVisibleJobSelection();
  if (state.loaded && state.selectedKey) loadDescription(state.selectedKey);
  if (state.loaded && state.selectedKey) showCachedOverview(state.selectedKey);
  if (state.quickFillEnabled && (state.quickFillOpen || state.quickFillPopout)) renderQuickFill();
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

function queueJobs() {
  return state.jobs.filter((job) => job.status === "queued").sort((left, right) => {
    const leftPosition = Number.isInteger(left.queue_position) ? left.queue_position : null;
    const rightPosition = Number.isInteger(right.queue_position) ? right.queue_position : null;
    if (leftPosition !== null || rightPosition !== null) {
      if (leftPosition === null) return 1;
      if (rightPosition === null) return -1;
      if (leftPosition !== rightPosition) return leftPosition - rightPosition;
    }
    const leftDeadline = left.deadline || "9999-12-31";
    const rightDeadline = right.deadline || "9999-12-31";
    return leftDeadline.localeCompare(rightDeadline) || rankingScore(right) - rankingScore(left)
      || String(left.company).localeCompare(String(right.company));
  });
}

function queueCard(job, index, count) {
  const deadline = deadlineLabel(job.deadline);
  const checked = job.liveness_status === "live"
    ? '<span class="queue-liveness is-live"><span aria-hidden="true">✓</span>Posting live</span>'
    : job.liveness_status === "closed"
      ? '<span class="queue-liveness is-closed"><span aria-hidden="true">!</span>Posting closed</span>'
      : job.liveness_status === "unknown"
        ? '<span class="queue-liveness"><span aria-hidden="true">?</span>Couldn’t verify</span>'
      : '<span class="queue-liveness"><span aria-hidden="true">?</span>Not checked</span>';
  return `<article class="queue-card" data-queue-key="${escapeHtml(job.dedupe_key)}">
    <button class="queue-drag-handle interactive" type="button" draggable="true" aria-label="Reorder ${escapeHtml(job.title)}" title="Drag to reorder">${icons.queue}</button>
    <div class="queue-card-copy"><h2>${escapeHtml(job.title)}</h2><p>${escapeHtml(job.company)}</p><dl>${factItem("Location", job.location)}${factItem("Deadline", deadline)}${factItem("Score", job.score == null ? "" : new Intl.NumberFormat().format(job.score))}</dl>${checked}</div>
    <div class="queue-card-actions"><button class="icon-button interactive" type="button" data-queue-move="up" data-queue-key="${escapeHtml(job.dedupe_key)}" aria-label="Move ${escapeHtml(job.title)} earlier" ${index === 0 ? "disabled" : ""}>↑</button><button class="icon-button interactive" type="button" data-queue-move="down" data-queue-key="${escapeHtml(job.dedupe_key)}" aria-label="Move ${escapeHtml(job.title)} later" ${index === count - 1 ? "disabled" : ""}>↓</button><button class="text-button interactive" type="button" data-queue-remove="${escapeHtml(job.dedupe_key)}">Save for later</button></div>
  </article>`;
}

function renderQueue({ focus = false } = {}) {
  document.title = "Queue — JobSeer";
  if (state.error) {
    routeView.innerHTML = errorMarkup(state.error);
  } else if (!state.loaded) {
    routeView.innerHTML = state.skeletonAt ? '<section class="page-shell"><div class="queue-skeleton skeleton"></div></section>' : "";
  } else {
    const jobs = queueJobs();
    routeView.innerHTML = `<section class="queue-page" aria-labelledby="queue-title"><header class="queue-heading"><div><h1 id="queue-title" tabindex="-1">Apply queue</h1><p>${jobs.length ? `${new Intl.NumberFormat().format(jobs.length)} ${jobs.length === 1 ? "role" : "roles"}, ordered for a focused pass.` : "Your next application session starts here."}</p></div>${jobs.length ? `<button class="filled-button interactive" type="button" data-start-session ${state.sessionStarting ? 'aria-disabled="true" aria-busy="true"' : ""}>${state.sessionStarting ? "Checking postings…" : "Start session"}</button>` : ""}</header>${jobs.length ? `<div class="queue-list" aria-label="Queued jobs">${jobs.map((job, index) => queueCard(job, index, jobs.length)).join("")}</div>` : `<div class="empty-state"><h2>Queue a role worth your time.</h2><p>Jobs you queue from Inbox will wait here in deadline order.</p><a class="tonal-button interactive" href="/inbox" data-route>Find roles</a></div>`}</section>`;
  }
  if (focus) document.querySelector("#queue-title, .empty-state h2")?.focus({ preventScroll: true });
}

function statusMarkup(status) {
  const symbols = {
    applying: "↗", applied: "✓", interviewing: "◉", offer: "★", rejected: "×",
    queued: "→", saved: "+", archived: "—", new: "•",
  };
  return `<span class="status-word status-${escapeHtml(status)}"><span aria-hidden="true">${symbols[status] || "•"}</span>${escapeHtml(statusLabel(status))}</span>`;
}

function statusSelectMarkup(job, attribute) {
  const options = ["new", "saved", "queued", "applying", "applied", "interviewing", "offer", "rejected", "archived"];
  return `<label class="status-editor"><span class="visually-hidden">Change status for ${escapeHtml(job.title)}</span><select ${attribute}="${escapeHtml(job.dedupe_key)}" aria-label="Change status for ${escapeHtml(job.title)}" ${state.statusSaving.has(job.dedupe_key) ? "disabled" : ""}>${options.map((status) => `<option value="${status}" ${status === job.status ? "selected" : ""}>${escapeHtml(statusLabel(status))}</option>`).join("")}</select></label>`;
}

function parseTrackerUrl() {
  const params = new URLSearchParams(window.location.search);
  state.trackerView = params.get("view") === "board" ? "board" : params.get("view") === "calibration" ? "calibration" : "table";
  state.trackerStatus = allowedStatuses.has(params.get("status")) ? params.get("status") : "all";
  state.trackerSort = ["company", "status", "applied", "last_activity"].includes(params.get("sort")) ? params.get("sort") : "last_activity";
  state.trackerQuery = params.get("q") || "";
}

function syncTrackerUrl({ replace = false } = {}) {
  const params = new URLSearchParams();
  if (state.trackerView !== "table") params.set("view", state.trackerView);
  if (state.trackerStatus !== "all") params.set("status", state.trackerStatus);
  if (state.trackerSort !== "last_activity") params.set("sort", state.trackerSort);
  if (state.trackerQuery) params.set("q", state.trackerQuery);
  history[replace ? "replaceState" : "pushState"]({}, "", `/tracker${params.size ? `?${params}` : ""}`);
}

function trackerApplications() {
  const query = state.trackerQuery.trim().toLowerCase();
  const applications = [...(state.trackerData?.applications || [])].filter((job) =>
    (state.trackerStatus === "all" || job.status === state.trackerStatus)
    && (!query || `${job.company} ${job.title} ${job.next_step || ""}`.toLowerCase().includes(query))
  );
  applications.sort((left, right) => {
    if (state.trackerSort === "company") return `${left.company} ${left.title}`.localeCompare(`${right.company} ${right.title}`);
    if (state.trackerSort === "status") return `${left.status} ${left.company}`.localeCompare(`${right.status} ${right.company}`);
    if (state.trackerSort === "applied") return new Date(right.applied_at || 0) - new Date(left.applied_at || 0);
    return new Date(right.application_updated_at || 0) - new Date(left.application_updated_at || 0);
  });
  return applications;
}

function trackerInsights(applications) {
  const now = Date.now();
  const appliedWeek = applications.filter((job) => job.applied_at && now - new Date(job.applied_at) <= 7 * 86400000).length;
  const withDate = applications.filter((job) => job.applied_at);
  const responses = withDate.filter((job) => ["interviewing", "offer", "rejected"].includes(job.status)).length;
  const responseRate = withDate.length ? `${Math.round((responses / withDate.length) * 100)}%` : "—";
  const resumes = new Map();
  for (const job of withDate) {
    const name = job.resume_name || "Not recorded";
    const value = resumes.get(name) || { total: 0, responses: 0 };
    value.total += 1;
    if (["interviewing", "offer", "rejected"].includes(job.status)) value.responses += 1;
    resumes.set(name, value);
  }
  const bestResume = [...resumes].sort((a, b) => b[1].responses - a[1].responses || b[1].total - a[1].total)[0];
  const sources = new Map();
  for (const job of applications) for (const source of sourceList(job)) sources.set(source, (sources.get(source) || 0) + 1);
  const topSource = [...sources].sort((a, b) => b[1] - a[1])[0];
  return `<div class="tracker-insights" aria-label="Application insights">
    <div><strong>${new Intl.NumberFormat().format(appliedWeek)}</strong><span>Applied this week</span></div>
    <div><strong>${responseRate}</strong><span>Response rate</span></div>
    <div><strong>${escapeHtml(bestResume?.[0] || "No resume data")}</strong><span>${bestResume ? `${bestResume[1].responses} of ${bestResume[1].total} responses` : "By resume version"}</span></div>
    <div><strong>${escapeHtml(topSource?.[0] || "No source data")}</strong><span>${topSource ? `${topSource[1]} applications` : "Funnel by source"}</span></div>
  </div>`;
}

function reminderMarkup() {
  const now = new Date();
  const due = (state.trackerData?.reminders || []).filter((reminder) => {
    const effective = new Date(reminder.snoozed_until || reminder.due_at);
    return !Number.isNaN(effective.valueOf()) && effective <= now;
  });
  if (!due.length) return "";
  return `<section class="tracker-section" aria-labelledby="followups-title"><div class="section-heading"><div><h2 id="followups-title">Follow-ups due</h2><p>${due.length} ${due.length === 1 ? "application needs" : "applications need"} a next step.</p></div></div><div class="reminder-list">${due.map((reminder) => `<article class="reminder-row"><div><strong>${escapeHtml(reminder.company)}</strong><span>${escapeHtml(reminder.title)}</span></div><time datetime="${escapeHtml(reminder.snoozed_until || reminder.due_at)}">${escapeHtml(formatDate(reminder.snoozed_until || reminder.due_at))}</time><div><button class="text-button interactive" type="button" data-reminder-action="snooze" data-reminder-id="${reminder.id}">Snooze 3d</button><button class="tonal-button interactive" type="button" data-reminder-action="done" data-reminder-id="${reminder.id}">Done</button></div></article>`).join("")}</div></section>`;
}

function interviewsMarkup(applications) {
  const upcoming = (state.trackerData?.interviews || []).filter((item) => new Date(item.starts_at) >= new Date());
  const options = applications.map((job) => `<option value="${escapeHtml(job.dedupe_key)}">${escapeHtml(job.company)} — ${escapeHtml(job.title)}</option>`).join("");
  return `<section class="tracker-section" aria-labelledby="interviews-title"><div class="section-heading"><div><h2 id="interviews-title">Interviews</h2><p>${upcoming.length ? `${upcoming.length} upcoming.` : "Keep interview details with the application."}</p></div>${upcoming.length ? '<a class="outlined-button interactive" href="/api/v1/interviews.ics">Export .ics</a>' : ""}</div>${upcoming.length ? `<div class="interview-list">${upcoming.map((item) => `<article><time datetime="${escapeHtml(item.starts_at)}">${escapeHtml(formatAbsolute(item.starts_at))}</time><div><strong>${escapeHtml(item.company)}</strong><span>${escapeHtml(item.title)}${item.location ? ` · ${escapeHtml(item.location)}` : ""}</span></div><a class="text-button interactive" href="/api/v1/interviews.ics?id=${item.id}">.ics</a></article>`).join("")}</div>` : ""}<details class="interview-add"><summary>Add interview</summary><form id="interview-form"><label>Application<select name="dedupe_key" required><option value="">Choose application</option>${options}</select></label><label>Starts<input name="starts_at" type="datetime-local" required></label><label>Ends <span>(optional)</span><input name="ends_at" type="datetime-local"></label><label>Location <span>(optional)</span><input name="location" type="text" maxlength="500" autocomplete="off"></label><label>Notes <span>(optional)</span><textarea name="notes" rows="3" maxlength="20000"></textarea></label><button class="outlined-button interactive" type="submit">Save interview</button></form></details></section>`;
}

function trackerTableMarkup(applications) {
  if (!applications.length) return '<div class="empty-state"><h2>No applications match.</h2><p>Change the Tracker filters or apply to a role from Queue.</p><a class="tonal-button interactive" href="/queue" data-route>Open queue</a></div>';
  return `<div class="tracker-table-wrap"><table class="tracker-table"><thead><tr><th><button type="button" data-tracker-sort="company">Company</button></th><th>Role</th><th><button type="button" data-tracker-sort="status">Status</button></th><th><button type="button" data-tracker-sort="applied">Applied</button></th><th><button type="button" data-tracker-sort="last_activity">Last activity</button></th><th>Next step</th><th>Resume</th><th>Source</th></tr></thead><tbody>${applications.map((job) => `<tr><td><a href="/companies/${encodeURIComponent(job.company)}" data-route>${escapeHtml(job.company)}</a>${job.connections_count ? `<span class="connections-count">${new Intl.NumberFormat().format(job.connections_count)} connections</span>` : ""}</td><td>${escapeHtml(job.title)}</td><td><div class="tracker-status-cell">${statusMarkup(job.status)}${statusSelectMarkup(job, "data-tracker-status-select")}</div></td><td>${escapeHtml(job.applied_at ? formatDate(job.applied_at) : "")}</td><td>${escapeHtml(job.application_updated_at ? formatDate(job.application_updated_at) : "")}</td><td><input class="next-step-input" type="text" maxlength="1000" value="${escapeHtml(job.next_step || "")}" placeholder="Add next step" aria-label="Next step for ${escapeHtml(job.title)}" data-next-step="${escapeHtml(job.dedupe_key)}"></td><td>${escapeHtml(job.resume_name || "")}</td><td>${escapeHtml(sourceList(job).join(", "))}</td></tr>`).join("")}</tbody></table></div>`;
}

function trackerBoardMarkup(applications) {
  const statuses = ["applying", "applied", "interviewing", "offer", "rejected"];
  return `<div class="tracker-board" aria-label="Applications by status">${statuses.map((status) => {
    const jobs = applications.filter((job) => job.status === status);
    return `<section class="board-column" data-board-status="${status}"><header><h2>${escapeHtml(statusLabel(status))}</h2><span>${jobs.length}</span></header><div>${jobs.map((job) => `<article class="board-card" draggable="true" data-tracker-key="${escapeHtml(job.dedupe_key)}"><a href="/companies/${encodeURIComponent(job.company)}" data-route>${escapeHtml(job.company)}</a><strong>${escapeHtml(job.title)}</strong>${job.connections_count ? `<span>${new Intl.NumberFormat().format(job.connections_count)} connections</span>` : ""}${statusSelectMarkup(job, "data-board-status-select")}</article>`).join("")}</div></section>`;
  }).join("")}</div>`;
}

function calibrationMarkup(applications) {
  const bands = [
    { label: "150 and above", test: (score) => score >= 150 },
    { label: "100–149", test: (score) => score >= 100 && score < 150 },
    { label: "Below 100", test: (score) => score < 100 },
  ];
  return `<section class="calibration" aria-labelledby="calibration-title"><div class="calibration-intro"><h2 id="calibration-title">Score calibration</h2><p>Discovery scores beside actual outcomes. This view is read-only and never changes scout scoring.</p></div><div class="calibration-grid">${bands.map((band) => {
    const rows = applications.filter((job) => job.score != null && band.test(Number(job.score)));
    const responses = rows.filter((job) => ["interviewing", "offer"].includes(job.status)).length;
    const offers = rows.filter((job) => job.status === "offer").length;
    return `<article><h3>${band.label}</h3><dl>${factItem("Applications", String(rows.length))}${factItem("Interview or offer", String(responses))}${factItem("Offers", String(offers))}</dl></article>`;
  }).join("")}</div><div class="calibration-table"><table><thead><tr><th>Score</th><th>Company</th><th>Role</th><th>Outcome</th></tr></thead><tbody>${applications.filter((job) => job.score != null).sort((a, b) => b.score - a.score).map((job) => `<tr><td>${new Intl.NumberFormat().format(job.score)}</td><td>${escapeHtml(job.company)}</td><td>${escapeHtml(job.title)}</td><td>${statusMarkup(job.status)}</td></tr>`).join("")}</tbody></table></div></section>`;
}

function trackerMarkup() {
  const all = state.trackerData?.applications || [];
  const applications = trackerApplications();
  const statuses = ["all", "applying", "applied", "interviewing", "offer", "rejected"];
  return `<section class="tracker-page" aria-labelledby="tracker-title"><header class="tracker-heading"><div><h1 id="tracker-title" tabindex="-1">Application tracker</h1><p>Keep the next action visible, then learn from the outcome.</p></div><div class="view-switch" aria-label="Tracker view">${[["table", "Table"], ["board", "Board"], ["calibration", "Calibration"]].map(([value, label]) => `<button class="interactive" type="button" data-tracker-view="${value}" aria-pressed="${state.trackerView === value}">${label}</button>`).join("")}</div></header>${trackerInsights(all)}${reminderMarkup()}${interviewsMarkup(all)}<section class="tracker-section applications-section" aria-labelledby="applications-title"><div class="section-heading"><div><h2 id="applications-title">Applications</h2><p>${applications.length} ${applications.length === 1 ? "application" : "applications"} in this view.</p></div><button class="outlined-button interactive" type="button" data-export-tracker>Export CSV</button></div><div class="tracker-tools"><label>${icons.search}<input id="tracker-search" type="search" placeholder="Search applications" value="${escapeHtml(state.trackerQuery)}" aria-label="Search applications"></label><label><span class="visually-hidden">Filter by status</span><select id="tracker-status">${statuses.map((status) => `<option value="${status}" ${state.trackerStatus === status ? "selected" : ""}>${status === "all" ? "All statuses" : statusLabel(status)}</option>`).join("")}</select></label></div>${state.trackerView === "board" ? trackerBoardMarkup(applications) : state.trackerView === "calibration" ? calibrationMarkup(applications) : trackerTableMarkup(applications)}</section></section>`;
}

function renderTracker({ focus = false } = {}) {
  document.title = "Tracker — JobSeer";
  parseTrackerUrl();
  if (state.trackerError) routeView.innerHTML = `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load the tracker.</h1><p>${escapeHtml(state.trackerError)}</p><button class="tonal-button interactive" type="button" data-retry-tracker>Retry</button></div></section>`;
  else if (!state.trackerData) routeView.innerHTML = state.trackerShowLoader ? '<section class="page-shell"><div class="tracker-skeleton skeleton"></div></section>' : "";
  else routeView.innerHTML = trackerMarkup();
  if (focus) document.querySelector("#tracker-title, .error-state h1")?.focus({ preventScroll: true });
}

async function loadTrackerData({ background = false } = {}) {
  if (state.trackerLoading) return;
  state.trackerLoading = true; state.trackerError = "";
  let timer;
  if (!background) timer = setTimeout(() => { state.trackerShowLoader = true; if (routeRoot() === "tracker") renderTracker(); }, 300);
  try {
    const response = await fetch("/api/v1/tracker", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (response.redirected || !response.headers.get("Content-Type")?.includes("application/json")) {
      authRecovery();
      throw new Error("Session expired. Reconnecting through Authentik…");
    }
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The tracker did not respond.");
    state.trackerData = await response.json();
  } catch (error) {
    const message = error instanceof Error ? error.message : "The tracker did not respond.";
    if (!state.trackerData) state.trackerError = message;
    else if (Date.now() - lastNetworkNoticeAt > 60000) {
      showSnackbar(`${message} Keeping the last loaded tracker; retrying shortly.`);
      lastNetworkNoticeAt = Date.now();
    }
  } finally {
    clearTimeout(timer); state.trackerLoading = false; state.trackerShowLoader = false;
    updateTodayStrip();
    if (routeRoot() === "tracker") renderTracker();
    else if (routeRoot() === "inbox" && state.loaded) renderInbox();
  }
}

function updateTodayStrip() {
  const today = document.querySelector("#today-strip");
  const now = new Date();
  const week = new Date(now.getTime() + 7 * 86400000);
  const deadlines = state.jobs.filter((job) => {
    const date = job.deadline ? new Date(`${job.deadline}T23:59:59`) : null;
    return date && date >= now && date <= week;
  }).length;
  const followups = (state.trackerData?.reminders || []).filter((item) => new Date(item.snoozed_until || item.due_at) <= now).length;
  const interviews = (state.trackerData?.interviews || []).filter((item) => {
    const date = new Date(item.starts_at); return date >= now && date <= week;
  }).length;
  const items = [
    deadlines ? `${deadlines} ${deadlines === 1 ? "deadline" : "deadlines"} this week` : "",
    followups ? `${followups} ${followups === 1 ? "follow-up" : "follow-ups"} due` : "",
    interviews ? `${interviews} upcoming ${interviews === 1 ? "interview" : "interviews"}` : "",
  ].filter(Boolean);
  today.hidden = !items.length;
  today.innerHTML = items.length ? `<strong>Today</strong>${items.map((item) => `<a href="/tracker" data-route>${escapeHtml(item)}</a>`).join("")}` : "";
}

function companyNameFromRoute() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts[0] === "companies" && parts[1]) {
    try { return decodeURIComponent(parts.slice(1).join("/")); } catch { return ""; }
  }
  return new URLSearchParams(window.location.search).get("company") || "";
}

function normalizeCompany(value) {
  const suffixes = new Set(["co", "company", "corp", "corporation", "inc", "incorporated", "llc", "ltd", "limited"]);
  const parts = String(value || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").trim().split(/\s+/).filter(Boolean);
  if (parts[0] === "the") parts.shift();
  while (parts.length > 1 && suffixes.has(parts.at(-1))) parts.pop();
  return parts.join(" ");
}

function companyListMarkup(companies) {
  if (!companies.length) return '<div class="empty-state"><h2>No companies yet.</h2><p>Companies appear after the scout finds a posting.</p><a class="tonal-button interactive" href="/inbox" data-route>Open inbox</a></div>';
  return `<section class="companies-page" aria-labelledby="companies-title"><header class="companies-heading"><div><h1 id="companies-title" tabindex="-1">Companies</h1><p>Roles, applications, contacts, and context in one place.</p></div><label class="linkedin-import interactive"><span>Import LinkedIn CSV</span><input id="linkedin-csv" type="file" accept=".csv,text/csv"><small>Only connections matching these companies are saved.</small></label></header><div class="company-grid">${companies.map((company) => `<a class="company-card interactive" href="/companies/${encodeURIComponent(company.name)}" data-route>${companyLogoMarkup(company.name, "company-monogram")}<div><h2>${escapeHtml(company.name)}</h2><p>${company.postings.length} ${company.postings.length === 1 ? "posting" : "postings"} · ${company.applications.length} ${company.applications.length === 1 ? "application" : "applications"}</p>${company.connections_count ? `<span>${new Intl.NumberFormat().format(company.connections_count)} connections</span>` : ""}</div></a>`).join("")}</div></section>`;
}

function contactMarkup(contact) {
  const detail = [contact.title, contact.source === "linkedin_csv" ? "LinkedIn connection" : "Manual contact"].filter(Boolean).join(" · ");
  return `<article class="contact-row"><div><strong>${escapeHtml(contact.name)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}${contact.email ? `<a href="mailto:${encodeURIComponent(contact.email)}">${escapeHtml(contact.email)}</a>` : ""}</div>${contact.linkedin_url ? `<a class="text-button interactive" href="${escapeHtml(contact.linkedin_url)}" target="_blank" rel="noopener noreferrer">LinkedIn ${icons.external}</a>` : ""}</article>`;
}

function companyDetailMarkup(company) {
  const account = company.account;
  const accountMarkup = company.account_protected
    ? '<p class="protected-copy">ATS account details stay unavailable until protected profile access is enabled.</p>'
    : account ? `<dl class="company-facts">${factItem("Account", account.account_exists === true ? "Exists" : account.account_exists === false ? "Does not exist" : "Unknown")}${factItem("Sign-in email", account.sign_in_email)}${account.password_manager_url ? `<div><dt>Password manager</dt><dd><a href="${escapeHtml(account.password_manager_url)}" target="_blank" rel="noopener noreferrer">Open entry ${icons.external}</a></dd></div>` : ""}</dl>` : '<p class="muted-copy">No ATS account status recorded.</p>';
  return `<article class="company-detail" aria-labelledby="company-title"><header class="company-detail-heading"><div><a href="/companies" data-route>Companies</a><div class="company-title-row">${companyLogoMarkup(company.name, "company-monogram")}<h1 id="company-title" tabindex="-1">${escapeHtml(company.name)}</h1></div><p>${company.postings.length} postings · ${company.applications.length} applications${company.connections_count ? ` · ${new Intl.NumberFormat().format(company.connections_count)} connections` : ""}</p></div></header><div class="company-detail-grid"><main>
    <section class="company-section" aria-labelledby="company-applications-title"><h2 id="company-applications-title">Applications</h2>${company.applications.length ? `<div class="company-application-list">${company.applications.map((job) => `<article><div><strong>${escapeHtml(job.title)}</strong><span>${job.applied_at ? `Applied ${escapeHtml(formatDate(job.applied_at))}` : escapeHtml(formatDate(job.application_updated_at))}</span></div>${statusMarkup(job.status)}</article>`).join("")}</div>` : '<p class="muted-copy">No applications at this company yet.</p>'}</section>
    <section class="company-section" aria-labelledby="company-postings-title"><h2 id="company-postings-title">Postings</h2><div class="company-posting-list">${company.postings.map((job) => `<article><div><strong>${escapeHtml(job.title)}</strong><span>${escapeHtml(job.location || "Location not listed")}</span></div><div>${statusMarkup(job.status)}${job.url ? `<a class="icon-button interactive" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(job.title)} posting">${icons.external}</a>` : ""}</div></article>`).join("")}</div></section>
    <section class="company-section" aria-labelledby="company-contacts-title"><div class="section-heading"><div><h2 id="company-contacts-title">Contacts</h2><p>${company.contacts.length ? `${company.contacts.length} people connected to this company.` : "Keep useful people with the company."}</p></div></div>${company.contacts.length ? `<div class="contact-list">${company.contacts.map(contactMarkup).join("")}</div>` : ""}<details class="contact-add"><summary>Add contact</summary><form id="contact-form"><input type="hidden" name="company" value="${escapeHtml(company.name)}"><label>Name<input name="name" type="text" required maxlength="500" autocomplete="name"></label><label>Role <span>(optional)</span><input name="title" type="text" maxlength="500" autocomplete="organization-title"></label><label>Email <span>(optional)</span><input name="email" type="email" maxlength="500" autocomplete="email"></label><label>LinkedIn URL <span>(optional)</span><input name="linkedin_url" type="url" maxlength="2000" inputmode="url" placeholder="https://"></label><button class="outlined-button interactive" type="submit">Save contact</button></form></details></section>
  </main><aside>
    <section class="company-section"><h2>Notes</h2><label class="company-notes"><span class="visually-hidden">Notes about ${escapeHtml(company.name)}</span><textarea id="company-note" rows="8" maxlength="20000" placeholder="Interview context, referrals, or follow-up notes">${escapeHtml(company.note?.body || "")}</textarea><small id="company-note-state">Saved automatically</small></label></section>
    <section class="company-section"><h2>ATS account</h2>${accountMarkup}</section>
    <section class="company-section"><h2>Links</h2><div class="company-links">${company.links.slice(0, 12).map((link) => `<a href="${escapeHtml(link.url)}" target="_blank" rel="noopener noreferrer"><span>${escapeHtml(link.label)}</span>${icons.external}</a>`).join("")}</div></section>
  </aside></div></article>`;
}

function renderCompanies({ focus = false } = {}) {
  document.title = `${companyNameFromRoute() || "Companies"} — JobSeer`;
  if (state.companiesError) routeView.innerHTML = `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load companies.</h1><p>${escapeHtml(state.companiesError)}</p><button class="tonal-button interactive" type="button" data-retry-companies>Retry</button></div></section>`;
  else if (!state.companiesData) routeView.innerHTML = state.companiesShowLoader ? '<section class="page-shell"><div class="companies-skeleton skeleton"></div></section>' : "";
  else if (state.companiesData.company === null) routeView.innerHTML = '<section class="page-shell"><div class="empty-state"><h1>Company not found.</h1><p>This company is not in the current posting history.</p><a class="tonal-button interactive" href="/companies" data-route>All companies</a></div></section>';
  else if (state.companiesData.company) routeView.innerHTML = companyDetailMarkup(state.companiesData.company);
  else routeView.innerHTML = companyListMarkup(state.companiesData.companies || []);
  if (focus) document.querySelector("#companies-title, #company-title, .empty-state h1")?.focus({ preventScroll: true });
}

function ruleDescription(rule) {
  if (rule.kind === "archive_title") return `Archive new titles containing “${rule.pattern}”`;
  if (rule.kind === "tag_title") return `Tag titles containing “${rule.pattern}” as “${rule.value}”`;
  return `Add ${rule.value} ranking points to companies containing “${rule.pattern}”`;
}

function profileMarkup() {
  if (state.rulesError) return `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load rules.</h1><p>${escapeHtml(state.rulesError)}</p><button class="tonal-button interactive" type="button" data-retry-rules>Retry</button></div></section>`;
  const rules = state.rules || [];
  return `<section class="profile-page" aria-labelledby="profile-title"><header class="profile-heading"><div><h1 id="profile-title" tabindex="-1">Profile</h1><p>Keep reusable facts and quiet automation in one place.</p></div>${state.quickFillEnabled ? '<button class="tonal-button interactive" type="button" data-quick-fill-toggle>Open Quick-fill</button>' : ""}</header><section class="profile-section" aria-labelledby="rules-title"><div class="section-heading"><div><h2 id="rules-title">Inbox rules</h2><p>Rules use literal, case-insensitive matches. Disabling one does not rewrite past actions.</p></div></div><form class="rule-form" id="rule-form"><label><span>Name</span><input name="name" maxlength="120" required placeholder="Archive senior roles"></label><label><span>Action</span><select name="kind"><option value="archive_title">Archive title</option><option value="tag_title">Tag title</option><option value="boost_company">Boost company</option></select></label><label><span>Contains</span><input name="pattern" maxlength="120" required placeholder="Senior"></label><label><span>Tag or boost</span><input name="value" maxlength="40" placeholder="Systems or 25"><small>Leave empty for archive rules.</small></label><button class="filled-button interactive" type="submit">Add rule</button></form>${rules.length ? `<div class="rule-list">${rules.map((rule) => `<article class="rule-card"><div><h3>${escapeHtml(rule.name)}</h3><p>${escapeHtml(ruleDescription(rule))}</p></div><label class="rule-toggle"><input type="checkbox" data-rule-toggle="${rule.id}" ${rule.enabled ? "checked" : ""}><span><span aria-hidden="true">${rule.enabled ? "✓" : "—"}</span>${rule.enabled ? "Enabled" : "Paused"}</span></label></article>`).join("")}</div>` : '<div class="profile-empty"><h3>Quick-fill is unavailable in this tab.</h3><p>Recheck access after signing in, or reload while online.</p><button class="tonal-button interactive" type="button" data-refresh-quick-fill-access>Recheck access</button></div>'}</section>`;
}

function renderProfile({ focus = false } = {}) {
  document.title = "Profile — JobSeer";
  routeView.innerHTML = profileMarkup();
  if (focus) document.querySelector("#profile-title")?.focus({ preventScroll: true });
}

async function loadRules() {
  if (state.rulesLoading) return;
  state.rulesLoading = true; state.rulesError = "";
  try {
    const response = await fetch("/api/v1/rules", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Rules did not respond.");
    state.rules = (await response.json()).rules || [];
  } catch (error) {
    state.rulesError = error instanceof Error ? error.message : "Rules did not respond.";
  } finally {
    state.rulesLoading = false;
    if (routeRoot() === "profile") renderProfile();
  }
}

async function submitRule(form) {
  const payload = Object.fromEntries(new FormData(form));
  try {
    const response = await fetch("/api/v1/rules", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The rule could not be saved.");
    state.rules = [...(state.rules || []), (await response.json()).rule];
    form.reset(); renderProfile();
    state.loaded = false; await loadInbox();
    showSnackbar("Rule added.");
  } catch (error) { showSnackbar(error instanceof Error ? error.message : "The rule could not be saved."); }
}

async function toggleRule(id, enabled) {
  try {
    const response = await fetch(`/api/v1/rules/${id}`, {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The rule could not be updated.");
    const saved = (await response.json()).rule;
    state.rules = (state.rules || []).map((rule) => rule.id === saved.id ? saved : rule);
    renderProfile(); state.loaded = false; await loadInbox();
    showSnackbar(enabled ? "Rule enabled." : "Rule paused.");
  } catch (error) { renderProfile(); showSnackbar(error instanceof Error ? error.message : "The rule could not be updated."); }
}

async function undoRuleAction(id) {
  try {
    const response = await fetch(`/api/v1/rule-actions/${id}/undo`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The rule action could not be undone.");
    const job = state.jobs.find((item) => item.rule_action_id === Number(id));
    if (job) Object.assign(job, { status: "new", archived_by_rule: null, rule_action_id: null });
    renderInbox({ focus: true }); showSnackbar("Rule archive undone.");
  } catch (error) { showSnackbar(error instanceof Error ? error.message : "The rule action could not be undone."); }
}

async function loadCompanies(name = companyNameFromRoute()) {
  if (state.companiesLoading) return;
  state.companiesLoading = true; state.companiesError = ""; state.companyLoadedName = name;
  const timer = setTimeout(() => { state.companiesShowLoader = true; if (routeRoot() === "companies") renderCompanies(); }, 300);
  try {
    const params = name ? `?${new URLSearchParams({ name })}` : "";
    const response = await fetch(`/api/v1/companies${params}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Companies did not respond.");
    state.companiesData = await response.json();
  } catch (error) {
    state.companiesError = error instanceof Error ? error.message : "Companies did not respond.";
  } finally {
    clearTimeout(timer); state.companiesLoading = false; state.companiesShowLoader = false;
    if (routeRoot() === "companies") renderCompanies();
  }
}

function parseCsv(text) {
  const rows = []; let row = []; let field = ""; let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted && character === '"' && text[index + 1] === '"') { field += '"'; index += 1; }
    else if (character === '"') quoted = !quoted;
    else if (character === "," && !quoted) { row.push(field); field = ""; }
    else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      row.push(field); if (row.some((value) => value.trim())) rows.push(row); row = []; field = "";
    } else field += character;
  }
  row.push(field); if (row.some((value) => value.trim())) rows.push(row);
  return rows;
}

async function importLinkedInCsv(file) {
  if (!file || !state.companiesData?.companies) return;
  try {
    const rows = parseCsv(await file.text());
    if (rows.length < 2) throw new Error("The CSV has no connection rows.");
    const headerRow = rows.findIndex((values) => {
      const headers = values.map((value) => value.trim().toLowerCase());
      return (headers.includes("company") || headers.includes("company name"))
        && (headers.includes("name") || headers.includes("first name"));
    });
    if (headerRow < 0) throw new Error("The CSV needs Company and Name columns.");
    const headers = rows[headerRow].map((value) => value.trim().toLowerCase());
    const indexOf = (...names) => names.map((name) => headers.indexOf(name)).find((index) => index >= 0) ?? -1;
    const companyIndex = indexOf("company", "company name");
    const firstIndex = indexOf("first name"); const lastIndex = indexOf("last name");
    const nameIndex = indexOf("name"); const titleIndex = indexOf("position", "title");
    const urlIndex = indexOf("url", "linkedin url"); const emailIndex = indexOf("email address", "email");
    if (companyIndex < 0 || (nameIndex < 0 && firstIndex < 0)) throw new Error("The CSV needs Company and Name columns.");
    const companies = new Map(state.companiesData.companies.map((company) => [company.key, company.name]));
    const dataRows = rows.slice(headerRow + 1);
    const contacts = dataRows.map((values) => {
      const company = companies.get(normalizeCompany(values[companyIndex] || ""));
      const name = nameIndex >= 0 ? values[nameIndex] : `${values[firstIndex] || ""} ${values[lastIndex] || ""}`.trim();
      return company && name ? { company, name, title: values[titleIndex] || "", linkedin_url: values[urlIndex] || "", email: values[emailIndex] || "" } : null;
    }).filter(Boolean);
    let imported = 0;
    for (let index = 0; index < contacts.length; index += 100) {
      const response = await fetch("/api/v1/connections/import", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
        body: JSON.stringify({ contacts: contacts.slice(index, index + 100) }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Connections could not be imported.");
      imported += (await response.json()).imported || 0;
    }
    const unmatched = dataRows.length - contacts.length;
    state.companiesData = null; await loadCompanies("");
    state.loaded = false; await loadInbox();
    showSnackbar(`Imported ${imported} matched ${imported === 1 ? "connection" : "connections"}${unmatched ? `; ${unmatched} unmatched kept out` : ""}.`);
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "Connections could not be imported.");
  }
}

async function updateReminder(id, action) {
  try {
    const response = await fetch(`/api/v1/reminders/${id}`, {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ action }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The reminder could not be updated.");
    await loadTrackerData({ background: true });
    showSnackbar(action === "done" ? "Follow-up completed." : "Follow-up snoozed for 3 days.");
  } catch (error) { showSnackbar(error instanceof Error ? error.message : "The reminder could not be updated."); }
}

async function saveTrackerNextStep(key, value, input) {
  const job = state.trackerData?.applications?.find((item) => item.dedupe_key === key);
  const previous = job?.next_step || "";
  if (value === previous) return;
  if (job) job.next_step = value;
  input?.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/v1/applications/${encodeURIComponent(key)}/next-step`, {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ next_step: value }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The next step could not be saved.");
    showSnackbar("Next step saved.");
    await loadTrackerData({ background: true });
  } catch (error) {
    if (job) job.next_step = previous;
    if (input) input.value = previous;
    showSnackbar(`${error.message} Previous value restored.`);
  } finally { input?.removeAttribute("aria-busy"); }
}

async function setTrackerStatus(key, status, { offerUndo = true } = {}) {
  const tracked = state.trackerData?.applications?.find((item) => item.dedupe_key === key);
  const job = state.jobs.find((item) => item.dedupe_key === key) || tracked;
  if (!job || job.status === status || state.statusSaving.has(key)) return;
  const previous = job.status;
  state.statusSaving.add(key);
  job.status = status;
  if (tracked) tracked.status = status;
  if (routeRoot() === "tracker") renderTracker();
  if (routeRoot() === "inbox") renderSelectedJob();
  try {
    if (status === "applied") {
      const response = await secureWrite(`/api/v1/applications/${encodeURIComponent(key)}/applied`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ document_id: null }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The status could not be saved.");
      const snapshot = (await response.json()).snapshot;
      Object.assign(job, { applied_at: snapshot.captured_at, resume_document_id: snapshot.resume_document_id, resume_name: snapshot.resume_name, queue_position: null });
    } else {
      const result = await patchJob(job, status);
      job.status = result.status;
      if (tracked) tracked.status = result.status;
      if (result.liveness) Object.assign(job, { liveness_status: result.liveness.status, liveness_evidence: result.liveness.evidence });
    }
    await loadTrackerData({ background: true });
    if (routeRoot() === "inbox") renderSelectedJob();
    showSnackbar(job.status === "archived" && status === "queued" ? "Posting closed; it cannot be queued." : `Moved to ${statusLabel(job.status)}.`, offerUndo ? {
      action: "Undo", duration: 6000,
      onAction: () => setTrackerStatus(key, previous, { offerUndo: false }),
    } : {});
  } catch (error) {
    job.status = previous; if (tracked) tracked.status = previous;
    if (routeRoot() === "tracker") renderTracker();
    if (routeRoot() === "inbox") renderSelectedJob();
    showSnackbar(`${error.message} Status restored.`);
  } finally {
    state.statusSaving.delete(key);
    if (routeRoot() === "tracker") renderTracker();
    if (routeRoot() === "inbox") renderSelectedJob();
  }
}

async function submitInterview(form) {
  const data = new FormData(form);
  const start = new Date(String(data.get("starts_at") || ""));
  const endValue = String(data.get("ends_at") || "");
  const end = endValue ? new Date(endValue) : null;
  if (Number.isNaN(start.valueOf()) || (end && Number.isNaN(end.valueOf()))) {
    showSnackbar("Enter a valid interview date and time."); return;
  }
  try {
    const response = await fetch("/api/v1/interviews", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({
        dedupe_key: data.get("dedupe_key"), starts_at: start.toISOString(),
        ends_at: end ? end.toISOString() : null,
        location: data.get("location"), notes: data.get("notes"),
      }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The interview could not be saved.");
    await loadTrackerData({ background: true });
    showSnackbar("Interview added.");
  } catch (error) { showSnackbar(error instanceof Error ? error.message : "The interview could not be saved."); }
}

function exportTrackerCsv() {
  const quote = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const header = ["Company", "Role", "Status", "Applied date", "Last activity", "Next step", "Resume version", "Source"];
  const rows = trackerApplications().map((job) => [
    job.company, job.title, statusLabel(job.status), job.applied_at || "",
    job.application_updated_at || "", job.next_step || "", job.resume_name || "",
    sourceList(job).join(", "),
  ]);
  const blob = new Blob([[header, ...rows].map((row) => row.map(quote).join(",")).join("\r\n") + "\r\n"], { type: "text/csv" });
  const url = URL.createObjectURL(blob); const link = document.createElement("a");
  link.href = url; link.download = "jobseer-applications.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0); showSnackbar("Tracker CSV exported.");
}

async function saveCompanyNote(company, body) {
  const status = document.querySelector("#company-note-state");
  if (status) status.textContent = "Saving…";
  try {
    const response = await fetch("/api/v1/companies/note", {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ company, body }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The company note could not be saved.");
    if (state.companiesData?.company) state.companiesData.company.note = (await response.json()).note;
    if (document.querySelector("#company-note-state")) document.querySelector("#company-note-state").textContent = "Saved automatically";
  } catch (error) {
    if (status) status.textContent = "Not saved";
    showSnackbar(error instanceof Error ? error.message : "The company note could not be saved.");
  }
}

async function submitContact(form) {
  const payload = Object.fromEntries(new FormData(form));
  try {
    const response = await fetch("/api/v1/contacts", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The contact could not be saved.");
    state.companiesData = null; await loadCompanies(payload.company);
    showSnackbar("Contact added.");
  } catch (error) { showSnackbar(error instanceof Error ? error.message : "The contact could not be saved."); }
}

async function persistQueueOrder(keys, previous) {
  state.queueSaving = true;
  try {
    const response = await fetch("/api/v1/queue/order", {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ dedupe_keys: keys }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The queue order could not be saved.");
    showSnackbar("Queue order saved.");
  } catch (error) {
    previous.forEach(({ key, position }) => {
      const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
      if (job) job.queue_position = position;
    });
    renderQueue();
    showSnackbar(`${error.message} Order restored.`);
  } finally {
    state.queueSaving = false;
  }
}

function reorderQueue(key, targetIndex) {
  if (state.queueSaving) return;
  const jobs = queueJobs();
  const from = jobs.findIndex((job) => job.dedupe_key === key);
  const to = Math.max(0, Math.min(jobs.length - 1, targetIndex));
  if (from < 0 || from === to) return;
  const previous = jobs.map((job) => ({ key: job.dedupe_key, position: job.queue_position }));
  const [moved] = jobs.splice(from, 1);
  jobs.splice(to, 0, moved);
  jobs.forEach((job, position) => { job.queue_position = position; });
  renderQueue();
  requestAnimationFrame(() => document.querySelector(`[data-queue-key="${CSS.escape(key)}"] .queue-drag-handle`)?.focus());
  persistQueueOrder(jobs.map((job) => job.dedupe_key), previous);
}

async function removeFromQueue(key) {
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  if (!job) return;
  const previous = job.status;
  job.status = "saved";
  renderQueue();
  try {
    const result = await patchJob(job, "saved");
    job.status = result.status;
    showSnackbar("Moved to Saved.", {
      action: "Undo", duration: 6000,
      onAction: async () => {
        try { const restored = await patchJob(job, previous); job.status = restored.status; renderQueue(); }
        catch (error) { showSnackbar(error.message || "Couldn't restore the queued job."); }
      },
    });
  } catch (error) {
    job.status = previous;
    renderQueue();
    showSnackbar(`${error.message} Change undone.`);
  }
}

function sessionPathKey() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts[0] !== "queue" || parts[1] !== "session" || !parts[2] || parts[2] === "summary") return "";
  try { return decodeURIComponent(parts.slice(2).join("/")); } catch { return ""; }
}

function sessionElapsed() {
  const session = state.applySession;
  if (!session) return 0;
  return Math.max(0, Math.floor(((session.endedAt || Date.now()) - session.startedAt) / 1000));
}

function elapsedLabel(seconds = sessionElapsed()) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function startSessionClock() {
  clearInterval(state.sessionTimer);
  state.sessionTimer = setInterval(() => {
    const timer = document.querySelector("#session-elapsed");
    if (timer) timer.textContent = elapsedLabel();
  }, 1000);
}

async function startApplySession() {
  if (state.sessionStarting) return;
  state.sessionStarting = true;
  renderQueue();
  try {
    const response = await fetch("/api/v1/queue/session", {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRF-Token": state.csrf, Accept: "application/json" },
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The session could not start.");
    const payload = await response.json();
    for (const incoming of payload.jobs || []) {
      const existing = state.jobs.find((job) => job.dedupe_key === incoming.dedupe_key);
      if (existing) Object.assign(existing, incoming);
    }
    for (const skipped of payload.skipped || []) {
      const job = state.jobs.find((candidate) => candidate.dedupe_key === skipped.dedupe_key);
      if (job) Object.assign(job, { status: "archived", liveness_status: "closed", liveness_evidence: skipped.reason });
    }
    state.applySession = {
      keys: (payload.jobs || []).map((job) => job.dedupe_key),
      documents: payload.documents || [], applied: [], skipped: payload.skipped || [],
      index: 0, startedAt: Date.now(), endedAt: null, awaitingReturn: "",
      checkedKeys: new Set(), checkingKey: "", checkError: "",
    };
    if (!state.applySession.keys.length) {
      state.applySession = null;
      renderQueue();
      showSnackbar("No queued jobs are ready. Add a role from Inbox first.");
    }
    else {
      startSessionClock();
      state.selectedKey = state.applySession.keys[0];
      navigate(`/queue/session/${encodeURIComponent(state.selectedKey)}`);
    }
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "The session could not start.");
    renderQueue();
  } finally {
    state.sessionStarting = false;
  }
}

function sessionRequirements(job) {
  const detail = state.descriptions.get(job.dedupe_key);
  if (detail?.loading && detail.showLoader) return '<div class="session-requirements skeleton" aria-label="Loading requirements"></div>';
  const requirements = detail?.value?.sections?.find((section) => section.key === "requirements")?.text;
  if (!requirements) return '<p class="session-muted">No parsed requirements yet.</p>';
  const lines = String(requirements).split(/\n+/).map((line) => line.trim().replace(/^[-*•]\s*/, "")).filter(Boolean);
  const preview = lines.slice(0, 4).map((line) => `<li>${escapeHtml(line.length > 150 ? `${line.slice(0, 147)}…` : line)}</li>`).join("");
  return `<div class="session-requirements"><ul>${preview}</ul><button class="text-button interactive" type="button" data-session-see-requirements>Read full requirements</button></div>`;
}

function sessionLocationFact(location) {
  if (!location) return "";
  const places = String(location).split(";").map((place) => place.trim()).filter(Boolean);
  if (places.length < 2) return factItem("Location", location);
  return `<div class="session-location-fact"><dt>Location</dt><dd><details class="session-locations"><summary>${escapeHtml(places[0])} <span>+${places.length - 1} more</span></summary><ul>${places.slice(1).map((place) => `<li>${escapeHtml(place)}</li>`).join("")}</ul></details></dd></div>`;
}

function sessionPromptMarkup(job) {
  const session = state.applySession;
  if (!session.checkedKeys.has(job.dedupe_key)) {
    return session.checkError
      ? `<div class="session-check-state" role="alert"><p>${escapeHtml(session.checkError)}</p><button class="tonal-button interactive" type="button" data-session-retry-check>Retry check</button></div>`
      : '<p class="session-check-state" role="status">Checking that this posting is still open…</p>';
  }
  if (session.awaitingReturn !== "prompt") return `<button class="filled-button interactive" type="button" data-session-apply ${!applicationUrl(job) ? "disabled" : ""}>${job.status === "applying" ? "Reopen application" : "Apply"}</button>`;
  const options = (session.documents || []).map((document) => {
    const date = document.date ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(`${document.date}T00:00:00`)) : "";
    return `<option value="${escapeHtml(document.id)}">${escapeHtml(document.name)}${date ? ` — ${escapeHtml(date)}` : ""}</option>`;
  }).join("");
  return `<section class="submission-prompt" aria-labelledby="submission-title"><h2 id="submission-title">Did you submit?</h2><label for="session-resume">Resume version <span>(optional)</span></label><select id="session-resume"><option value="">Not recorded</option>${options}</select><div><button class="filled-button interactive" type="button" data-session-applied>Mark applied</button><button class="tonal-button interactive" type="button" data-session-not-yet>Not yet</button><button class="text-button interactive" type="button" data-session-skip>Skip</button></div></section>`;
}

function applySessionMarkup(job) {
  const session = state.applySession;
  const progress = `${session.index + 1} of ${session.keys.length}`;
  const quickFill = state.quickFillEnabled
    ? `<aside class="session-quick-fill" id="session-quick-fill" aria-label="Quick-fill">${state.quickFillProfile ? quickFillInnerMarkup({ embedded: true }) : '<p class="session-muted">Loading Quick-fill…</p>'}</aside>`
    : `<aside class="session-review" id="session-posting-review" aria-label="Posting review">${overviewMarkup(job)}${atGlanceMarkup(job)}${descriptionMarkup(job, { compact: true })}</aside>`;
  const siteWarning = job.nonpublic_site ? `<p class="company-warning"><span aria-hidden="true">!</span>${job.public_apply_url ? "Apply opens the verified public posting." : "No public apply link verified. Find this role on the employer’s public careers site."}</p>` : "";
  const uncertain = job.liveness_status === "unknown" ? `<p class="company-warning"><span aria-hidden="true">?</span>Couldn’t confirm this posting is open: ${escapeHtml(job.liveness_evidence || "the provider did not respond")}. Check before submitting.</p>` : "";
  return `<section class="apply-session${state.quickFillEnabled ? "" : " apply-session--review"}" aria-labelledby="session-job-title"><header class="session-header"><div><strong>${escapeHtml(progress)}</strong><span id="session-elapsed">${elapsedLabel()}</span></div><div><button class="text-button interactive" type="button" data-session-skip>Skip</button><button class="text-button interactive" type="button" data-session-end>End session</button></div></header><div class="session-columns"><main class="session-job"><div class="session-identity">${companyLogoMarkup(job.company, "detail-company-logo")}<div><p class="session-company">${escapeHtml(job.company)}</p><h1 id="session-job-title" tabindex="-1">${escapeHtml(job.title)}</h1></div></div><dl class="session-facts">${sessionLocationFact(job.location)}${factItem("Term", job.terms)}${factItem("Deadline", deadlineLabel(job.deadline))}${factItem("Posted", formatDate(job.first_seen))}</dl>${siteWarning}${uncertain}<div class="session-primary-action">${sessionPromptMarkup(job)}</div><section><h2>Requirements preview</h2>${sessionRequirements(job)}</section>${state.quickFillEnabled ? overviewMarkup(job) + descriptionMarkup(job, { compact: true }) : ""}</main>${quickFill}</div></section>`;
}

function sessionSummaryMarkup() {
  const session = state.applySession;
  const elapsed = elapsedLabel(sessionElapsed());
  const empty = !session.applied.length && !session.skipped.length;
  const applied = session.applied.map((key) => state.jobs.find((job) => job.dedupe_key === key)).filter(Boolean);
  const skipped = session.skipped.map((item) => state.jobs.find((job) => job.dedupe_key === item.dedupe_key)).filter(Boolean);
  return `<section class="page-shell session-summary-page"><div class="session-summary"><h1 id="session-summary-title" tabindex="-1">${empty ? "No applications recorded." : "Session complete."}</h1><p>${empty ? "Your queue is unchanged. Return when you’re ready to apply." : `You applied to ${session.applied.length} ${session.applied.length === 1 ? "role" : "roles"}, skipped ${session.skipped.length}, and spent ${escapeHtml(elapsed)}.`}</p>${applied.length ? `<h2>Applied</h2><ul>${applied.map((job) => `<li>${escapeHtml(job.company)} · ${escapeHtml(job.title)}</li>`).join("")}</ul>` : ""}${skipped.length ? `<h2>Skipped</h2><ul>${skipped.map((job) => `<li>${escapeHtml(job.company)} · ${escapeHtml(job.title)}</li>`).join("")}</ul>` : ""}<div><a class="tonal-button interactive" href="/queue" data-route>Return to queue</a>${applied.length ? '<a class="text-button interactive" href="/tracker" data-route>Open tracker</a>' : ""}</div></div></section>`;
}

function renderApplySession({ focus = false } = {}) {
  document.title = "Apply session — JobSeer";
  if (!state.loaded) {
    routeView.innerHTML = '<section class="page-shell"><div class="queue-skeleton skeleton" aria-label="Loading application session"></div></section>';
    return;
  }
  const session = state.applySession;
  if (!session) {
    const key = sessionPathKey();
    const queued = queueJobs();
    if (key && queued.some((job) => job.dedupe_key === key)) {
      state.applySession = { keys: queued.map((job) => job.dedupe_key), documents: [],
        applied: [], skipped: [], index: queued.findIndex((job) => job.dedupe_key === key),
        startedAt: Date.now(), endedAt: null, awaitingReturn: "",
        checkedKeys: new Set(), checkingKey: "", checkError: "" };
      startSessionClock();
      renderApplySession({ focus });
      return;
    }
    routeView.innerHTML = '<section class="page-shell"><div class="empty-state"><h1 id="page-title" tabindex="-1">That session has ended.</h1><p>Start a new focused pass from your current queue.</p><a class="tonal-button interactive" href="/queue" data-route>Return to queue</a></div></section>';
    return;
  }
  if (window.location.pathname.endsWith("/summary") || session.index >= session.keys.length) {
    routeView.innerHTML = sessionSummaryMarkup();
    if (focus) document.querySelector("#session-summary-title")?.focus({ preventScroll: true });
    return;
  }
  const routeKey = sessionPathKey();
  const routeIndex = session.keys.indexOf(routeKey);
  if (routeIndex >= 0) session.index = routeIndex;
  const key = session.keys[session.index];
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  if (!job) { endApplySession(); return; }
  state.selectedKey = key;
  routeView.innerHTML = applySessionMarkup(job);
  showCachedOverview(key);
  const surface = document.querySelector("#session-quick-fill");
  if (surface && state.quickFillEnabled && state.quickFillProfile) bindQuickFillSurface(surface);
  loadDescription(key);
  if (state.quickFillEnabled && state.quickFillProfile) {
    const contextKey = `${job.dedupe_key}\u0000${job.company}`;
    if (contextKey !== state.quickFillContextKey) loadQuickFillContext();
  }
  if (!session.checkedKeys.has(key) && !session.checkingKey && !session.checkError) verifySessionJob(key);
  if (focus) document.querySelector("#session-job-title")?.focus({ preventScroll: true });
}

async function verifySessionJob(key) {
  const session = state.applySession;
  if (!session || session.checkingKey || session.checkedKeys.has(key)) return;
  session.checkingKey = key;
  session.checkError = "";
  try {
    const response = await fetch("/api/v1/queue/check", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ dedupe_key: key }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't check this posting.");
    const result = await response.json();
    if (state.applySession !== session || session.keys[session.index] !== key) return;
    const job = state.jobs.find((item) => item.dedupe_key === key);
    if (job) Object.assign(job, { liveness_status: result.status, liveness_evidence: result.evidence });
    if (result.status === "closed") {
      if (job) job.status = "archived";
      session.skipped.push({ dedupe_key: key, reason: "Posting closed" });
      showSnackbar("Posting closed; moved to the next role.");
      session.checkingKey = "";
      advanceApplySession("closed");
      return;
    }
    session.checkedKeys.add(key);
    renderApplySession();
  } catch (error) {
    if (state.applySession === session && session.keys[session.index] === key) {
      session.checkError = error instanceof Error ? error.message : "Couldn't check this posting.";
      renderApplySession();
    }
  } finally {
    if (session.checkingKey === key) session.checkingKey = "";
  }
}

async function openSessionApplication() {
  const session = state.applySession;
  const job = session && state.jobs.find((candidate) => candidate.dedupe_key === session.keys[session.index]);
  if (!job || !session.checkedKeys.has(job.dedupe_key) || !applicationUrl(job) || session.awaitingReturn) return;
  window.open(applicationUrl(job), "_blank", "noopener");
  const previous = job.status;
  job.status = "applying";
  session.awaitingReturn = "waiting";
  session.openedAt = Date.now();
  renderApplySession();
  try {
    const result = await patchJob(job, "applying");
    job.status = result.status;
  } catch (error) {
    job.status = previous;
    session.awaitingReturn = "";
    renderApplySession();
    showSnackbar(`${error.message} Change undone.`);
  }
}

function showSubmissionPrompt() {
  const session = state.applySession;
  if (!session || session.awaitingReturn !== "waiting" || Date.now() - session.openedAt < 300) return;
  session.awaitingReturn = "prompt";
  renderApplySession({ focus: true });
}

function advanceApplySession(kind) {
  const session = state.applySession;
  if (!session) return;
  const key = session.keys[session.index];
  if (kind === "skipped") session.skipped.push({ dedupe_key: key, reason: "Skipped in session" });
  session.awaitingReturn = "";
  session.checkingKey = "";
  session.checkError = "";
  session.index += 1;
  if (session.index >= session.keys.length) endApplySession();
  else {
    state.selectedKey = session.keys[session.index];
    navigate(`/queue/session/${encodeURIComponent(state.selectedKey)}`);
  }
}

async function markSessionApplied() {
  const session = state.applySession;
  if (!session) return;
  const key = session.keys[session.index];
  const documentId = document.querySelector("#session-resume")?.value || null;
  try {
    const response = await secureWrite(`/api/v1/applications/${encodeURIComponent(key)}/applied`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ document_id: documentId }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The application could not be saved.");
    const snapshot = (await response.json()).snapshot;
    const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
    if (job) Object.assign(job, { status: "applied", applied_at: snapshot.captured_at, resume_document_id: snapshot.resume_document_id, resume_name: snapshot.resume_name, queue_position: null });
    session.applied.push(key);
    advanceApplySession("applied");
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "The application could not be saved.");
  }
}

function endApplySession() {
  const session = state.applySession;
  if (!session) return;
  session.endedAt = Date.now();
  clearInterval(state.sessionTimer);
  state.sessionTimer = null;
  navigate("/queue/session/summary");
}

function updateNav(root) {
  document.querySelectorAll("[data-nav]").forEach((item) => {
    if (item.dataset.nav === root) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
}

function renderRoute({ focus = false } = {}) {
  const root = routeRoot();
  document.documentElement.dataset.applySession = String(root === "queue" && window.location.pathname.startsWith("/queue/session"));
  updateNav(root);
  if (root === "inbox") {
    parseInboxUrl();
    renderInbox({ focus });
    if (!state.loaded && !state.loading) loadInbox();
  } else if (root === "queue") {
    if (window.location.pathname.startsWith("/queue/session")) renderApplySession({ focus });
    else renderQueue({ focus });
    if (!state.loaded && !state.loading) loadInbox();
  } else if (root === "tracker") {
    renderTracker({ focus });
    if (!state.loaded && !state.loading) loadInbox();
    if (!state.trackerData && !state.trackerLoading) loadTrackerData();
  } else if (root === "companies") {
    const name = companyNameFromRoute();
    if (name !== state.companyLoadedName) state.companiesData = null;
    renderCompanies({ focus });
    if (!state.loaded && !state.loading) loadInbox();
    if (!state.companiesData && !state.companiesLoading) loadCompanies(name);
  } else if (root === "profile") {
    renderProfile({ focus });
    if (!state.loaded && !state.loading) loadInbox();
    if (!state.rules && !state.rulesLoading) loadRules();
  } else renderPlaceholder(root, { focus });
}

async function loadInbox() {
  if (state.loading) return;
  state.loading = true;
  state.error = "";
  state.skeletonAt = 0;
  clearTimeout(loadingTimer);
  loadingTimer = setTimeout(() => {
    state.skeletonAt = performance.now();
    if (routeRoot() === "inbox") renderInbox();
    else if (routeRoot() === "queue") renderRoute();
  }, 300);
  try {
    const session = navigator.onLine ? await refreshSession({ force: true }) : { csrf_token: "", features: {} };
    state.csrf = session.csrf_token || "";
    state.quickFillEnabled = Boolean(session.features?.quick_fill);
    state.aiOverviewEnabled = Boolean(session.features?.ai_overview);
    quickFillTrigger.hidden = !state.quickFillEnabled;
    quickFillSheet.hidden = !state.quickFillEnabled;
    if (state.quickFillEnabled && !state.quickFillProfile && !state.quickFillError) loadQuickFillData();
    if (!state.quickFillEnabled) closeQuickFill({ restoreFocus: false });
    const [jobsResponse, viewsResponse, logosResponse, iconKeysResponse] = await Promise.all([
      fetch("/api/v1/jobs", { credentials: "same-origin", headers: { Accept: "application/json" } }),
      fetch("/api/v1/saved-views", { credentials: "same-origin", headers: { Accept: "application/json" } }),
      fetch("/static/company-logos/manifest.json", { credentials: "same-origin", headers: { Accept: "application/json" } }),
      fetch("/api/v1/company-icons", { credentials: "same-origin", headers: { Accept: "application/json" } }),
    ]);
    if (jobsResponse.redirected || viewsResponse.redirected
        || !jobsResponse.headers.get("Content-Type")?.includes("application/json")
        || !viewsResponse.headers.get("Content-Type")?.includes("application/json")) {
      authRecovery();
      throw new Error("Session expired. Reconnecting through Authentik…");
    }
    if (!jobsResponse.ok) throw new Error((await jobsResponse.json().catch(() => ({}))).error || "The job list did not respond.");
    if (!viewsResponse.ok) throw new Error((await viewsResponse.json().catch(() => ({}))).error || "Saved views did not respond.");
    const [payload, viewsPayload] = await Promise.all([jobsResponse.json(), viewsResponse.json()]);
    const logos = logosResponse.ok ? await logosResponse.json().catch(() => ({})) : {};
    const iconKeys = iconKeysResponse.ok ? await iconKeysResponse.json().catch(() => ({})) : {};
    state.companyLogos = Object.fromEntries(Object.entries(logos).map(([name, filename]) => [normalizeCompany(name), filename]));
    state.companyIconKeys = new Set(Array.isArray(iconKeys.keys) ? iconKeys.keys : []);
    const wait = state.skeletonAt ? Math.max(0, 500 - (performance.now() - state.skeletonAt)) : 0;
    if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
    state.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    state.savedViews = Array.isArray(viewsPayload.saved_views) ? viewsPayload.saved_views : [];
    state.refreshedAt = payload.refreshed_at || null;
    state.loaded = true;
    clearTimeout(inboxRetryTimer);
    inboxRetryTimer = null;
    updateTodayStrip();
    if (!state.trackerData && !state.trackerLoading) loadTrackerData({ background: true });
    try { localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString()); } catch { /* Storage is optional. */ }
  } catch (error) {
    const message = error instanceof Error ? error.message : "The job list did not respond.";
    if (!state.loaded) state.error = message;
    else if (Date.now() - lastNetworkNoticeAt > 60000) {
      showSnackbar(`${message} Keeping the last loaded jobs; retrying shortly.`);
      lastNetworkNoticeAt = Date.now();
    }
    if (navigator.onLine && !inboxRetryTimer) {
      inboxRetryTimer = setTimeout(() => { inboxRetryTimer = null; loadInbox(); }, 15000);
    }
  } finally {
    clearTimeout(loadingTimer);
    state.loading = false;
    renderRoute();
  }
}

function updateOfflineState() {
  offlineState.hidden = navigator.onLine;
  if (!navigator.onLine) {
    state.csrf = "";
    state.quickFillEnabled = false;
    quickFillTrigger.hidden = true;
    quickFillSheet.hidden = true;
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

function safeHttpsUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" ? url.href : "";
  } catch { return ""; }
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
      key: `document:${document.name}`, label: document.name,
      value: String(document.url || document.name), group: "Documents",
      detail: document.date ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(`${document.date}T00:00:00`)) : "",
    }));
  const templates = (Array.isArray(profile.answer_templates) ? profile.answer_templates : [])
    .filter((template) => String(template?.body || "").trim())
    .map((template) => {
      const value = templateValue(profile.answer_overrides?.[template.name] ?? template.body);
      return { key: `template:${template.name}`, label: template.name, value, group: "Short answers", detail: `${new Intl.NumberFormat().format(value.length)} characters` };
    });
  const account = profile.company_account;
  const accountItems = account?.sign_in_email ? [{
    key: "account:email", label: "ATS sign-in email", value: account.sign_in_email,
    group: "ATS account", detail: account.account_exists === true ? "Account confirmed" : account.account_exists === false ? "No account yet" : "Account status unknown",
  }] : [];
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
  const all = [...fields, ...documents, ...templates, ...stories, ...accountItems];
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

function editorField(label, value, attributes, {
  multiline = false, type = "text", inputmode = "", autocomplete = "",
  spellcheck = "", autocapitalize = "",
} = {}) {
  const nativeAttributes = `${inputmode ? ` inputmode="${inputmode}"` : ""}${autocomplete ? ` autocomplete="${autocomplete}"` : ""}${spellcheck ? ` spellcheck="${spellcheck}"` : ""}${autocapitalize ? ` autocapitalize="${autocapitalize}"` : ""}`;
  const control = multiline
    ? `<textarea rows="3" ${attributes}>${escapeHtml(value)}</textarea>`
    : `<input type="${type}" value="${escapeHtml(value)}"${nativeAttributes} ${attributes}>`;
  return `<label class="quick-fill-edit-field"><span>${escapeHtml(label)}</span>${control}</label>`;
}

function quickFillEditorMarkup() {
  const profile = state.quickFillProfile || {};
  const job = currentJob();
  const fields = (profile.fields || []).map((field, index) => editorField(
    field.label || field.key, field.value || "", `data-profile-kind="fields" data-profile-index="${index}" data-profile-property="value"`,
    { multiline: String(field.value || "").length > 120 || String(field.value || "").includes("\n") },
  )).join("");
  const templates = (profile.answer_templates || []).map((template, index) => `<details class="quick-fill-edit-card"><summary>${escapeHtml(template.name)}</summary>${editorField("Template", template.body || "", `data-profile-kind="answer_templates" data-profile-index="${index}" data-profile-property="body"`, { multiline: true })}<small>${new Intl.NumberFormat().format(String(template.body || "").length)} characters before variables are replaced</small></details>`).join("");
  const documents = (profile.documents || []).map((document, index) => `<details class="quick-fill-edit-card"><summary>${escapeHtml(document.name)}</summary>${editorField("Name", document.name || "", `data-profile-kind="documents" data-profile-index="${index}" data-profile-property="name"`)}${editorField("Date", document.date || "", `data-profile-kind="documents" data-profile-index="${index}" data-profile-property="date"`)}${editorField("Path or URL", document.url || "", `data-profile-kind="documents" data-profile-index="${index}" data-profile-property="url"`)}</details>`).join("");
  const stories = (profile.stories || []).map((story, index) => `<details class="quick-fill-edit-card"><summary>${escapeHtml(story.title)}</summary>${editorField("Title", story.title || "", `data-profile-kind="stories" data-profile-index="${index}" data-profile-property="title"`)}${editorField("Competencies", (story.competencies || []).join(", "), `data-profile-kind="stories" data-profile-index="${index}" data-profile-property="competencies"`)}${["situation", "task", "action", "result", "reflection"].map((part) => editorField(statusLabel(part), story[part] || "", `data-profile-kind="stories" data-profile-index="${index}" data-profile-property="${part}"`, { multiline: true })).join("")}</details>`).join("");
  const overrides = (profile.answer_templates || []).map((template) => `<label class="quick-fill-edit-field"><span>${escapeHtml(template.name)} override</span><textarea rows="3" placeholder="Use the global template" data-context-kind="override" data-context-key="${escapeHtml(template.name)}">${escapeHtml(profile.answer_overrides?.[template.name] || "")}</textarea></label>`).join("");
  const account = profile.company_account || {};
  const accountEditor = job ? `<section><h3>${escapeHtml(job.company)} ATS account</h3><label class="quick-fill-edit-field"><span>Account exists</span><select data-context-kind="account" data-context-property="account_exists"><option value="" ${account.account_exists == null ? "selected" : ""}>Not recorded</option><option value="yes" ${account.account_exists === true ? "selected" : ""}>Yes</option><option value="no" ${account.account_exists === false ? "selected" : ""}>No</option></select></label>${editorField("Sign-in email", account.sign_in_email || "", 'data-context-kind="account" data-context-property="sign_in_email"', { type: "email", autocomplete: "email" })}${editorField("Password manager link", account.password_manager_url || "", 'data-context-kind="account" data-context-property="password_manager_url"', { type: "url", inputmode: "url", autocomplete: "off", spellcheck: "false", autocapitalize: "off" })}<p class="quick-fill-privacy">JobSeer never stores passwords.</p></section>` : "";
  return `<div class="quick-fill-editor">${accountEditor}${job ? `<section><h3>Answers for this job</h3>${overrides || '<p class="quick-fill-empty">No templates yet.</p>'}</section>` : ""}<section><h3>Profile fields</h3>${fields || `<p class="quick-fill-empty">No fields yet. Start with blank labels, then enter only what you want to reuse.</p><button class="tonal-button interactive" type="button" data-quick-fill-setup ${state.quickFillContextPendingKey || state.quickFillSetupSaving ? "disabled" : ""}>Create blank fields</button>`}</section><section><h3>Documents</h3>${documents || '<p class="quick-fill-empty">No documents yet.</p>'}</section><section><h3>Short answers</h3>${templates || '<p class="quick-fill-empty">No templates yet.</p>'}</section><section><h3>Story bank</h3>${stories || '<p class="quick-fill-empty">No stories yet.</p>'}</section><div class="quick-fill-data-actions"><button class="outlined-button interactive" type="button" data-profile-export>Export JSON</button><button class="outlined-button interactive" type="button" data-profile-import-trigger>Import JSON</button><input class="visually-hidden" type="file" accept="application/json,.json" data-profile-import tabindex="-1"></div></div>`;
}

function quickFillInnerMarkup({ popout = false, embedded = false } = {}) {
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
  const passwordManagerUrl = safeHttpsUrl(state.quickFillProfile?.company_account?.password_manager_url);
  const passwordManagerLink = passwordManagerUrl
    ? `<a class="quick-fill-password-link" href="${escapeHtml(passwordManagerUrl)}" target="_blank" rel="noopener noreferrer">Open password manager</a>`
    : "";
  const emptySetup = `<div class="quick-fill-setup"><h3>Set up your copy fields.</h3><p>Start with blank labels for contact, links, education, and availability. No personal details are prefilled.</p><button class="tonal-button interactive" type="button" data-quick-fill-setup ${state.quickFillContextPendingKey || state.quickFillSetupSaving ? "disabled" : ""}>Create blank fields</button></div>`;
  const body = state.quickFillEdit
    ? quickFillEditorMarkup()
    : `<label class="quick-fill-search" for="quick-fill-search-${popout ? "popout" : "docked"}">${icons.search}<input id="quick-fill-search-${popout ? "popout" : "docked"}" type="search" value="${escapeHtml(state.quickFillQuery)}" placeholder="Search fields" aria-label="Search Quick-fill fields"></label>${guidance ? `<p class="quick-fill-guidance">${escapeHtml(guidance)}</p>` : ""}${passwordManagerLink}<div class="quick-fill-groups">${state.quickFillError ? `<div class="quick-fill-empty" role="alert">${escapeHtml(state.quickFillError)}</div>` : content || (!state.quickFillProfile ? '<p class="quick-fill-empty">Loading fields…</p>' : state.quickFillProfile.fields?.length ? '<p class="quick-fill-empty">No filled fields match this search. Use Edit to add a value.</p>' : emptySetup)}</div>`;
  return `<div class="quick-fill-header"><div><h2>Quick-fill</h2><p>${state.quickFillEdit ? `<span class="quick-fill-save-state">${escapeHtml(state.quickFillSaveState || "Changes save automatically")}</span>` : escapeHtml(status)}</p></div><button class="text-button interactive quick-fill-edit-toggle" type="button" data-quick-fill-edit>${state.quickFillEdit ? "Done" : "Edit"}</button><button class="icon-button interactive" type="button" data-quick-fill-popout aria-label="Pop out Quick-fill" ${popout ? "hidden" : ""}>${icons.external}</button><button class="icon-button interactive" type="button" data-quick-fill-close aria-label="Close Quick-fill" ${embedded ? "hidden" : ""}>${icons.close}</button></div>${body}`;
}

function bindQuickFillSurface(root, { popout = false } = {}) {
  root.addEventListener("click", (event) => {
    const copy = event.target.closest("[data-copy-index]");
    if (copy) { copyQuickFillItem(Number(copy.dataset.copyIndex)); return; }
    if (event.target.closest("[data-quick-fill-popout]")) { popOutQuickFill(); return; }
    if (event.target.closest("[data-quick-fill-setup]")) { setupBlankQuickFill(); return; }
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
    if (event.target.matches("[data-context-kind]:not(select)")) {
      updateQuickFillContext(event.target);
      return;
    }
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
    if (event.target.matches("select[data-context-kind]")) { updateQuickFillContext(event.target); return; }
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

function profileContextDocument(profile = state.quickFillProfile) {
  return {
    answer_overrides: cloneJson(profile?.answer_overrides || {}),
    company_account: cloneJson(profile?.company_account || {
      account_exists: null, sign_in_email: "", password_manager_url: "",
    }),
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

async function setupBlankQuickFill() {
  if (!state.quickFillEnabled || !state.quickFillProfile || state.quickFillProfile.fields?.length || state.quickFillSetupSaving || state.quickFillContextPendingKey) return;
  state.quickFillSetupSaving = true;
  setQuickFillSaveState("Saving…");
  try {
    const context = profileContextDocument();
    const saved = await persistQuickFillProfile({ ...profileDocument(), fields: cloneJson(blankQuickFillFields) });
    Object.assign(saved, context);
    state.quickFillProfile = saved;
    state.quickFillSavedProfile = cloneJson(saved);
    state.quickFillEdit = true;
    setQuickFillSaveState("Saved automatically");
    renderQuickFill();
    renderSessionQuickFill();
    requestAnimationFrame(() => document.querySelector("#quick-fill-sheet [data-profile-kind], #session-quick-fill [data-profile-kind]")?.focus());
    showSnackbar("Blank Quick-fill fields are ready. Add only details you want to reuse.");
  } catch (error) {
    setQuickFillSaveState("Setup failed");
    showSnackbar(error instanceof Error ? error.message : "Quick-fill setup failed. Retry.");
  } finally {
    state.quickFillSetupSaving = false;
  }
}

async function persistQuickFillContext(context, job) {
  if (!job) throw new Error("Choose a job before saving its Quick-fill details.");
  const response = await fetch("/api/v1/profile/context", {
    method: "PUT", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
    body: JSON.stringify({
      dedupe_key: job.dedupe_key, company: job.company,
      answer_overrides: context.answer_overrides,
      company_account: context.company_account,
    }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Job-specific Quick-fill details could not be saved.");
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
      Object.assign(saved, profileContextDocument());
      state.quickFillSavedProfile = cloneJson(saved);
      if (version === state.quickFillSaveVersion) {
        state.quickFillProfile = saved;
        state.eligibility.clear();
        if (state.selectedKey) loadEligibility(state.selectedKey);
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

function updateQuickFillContext(control) {
  if (!state.quickFillProfile) return;
  const job = currentJob();
  if (!job) return;
  const contextKey = `${job.dedupe_key}\u0000${job.company}`;
  if (!state.quickFillProfile.answer_overrides) state.quickFillProfile.answer_overrides = {};
  if (!state.quickFillProfile.company_account) state.quickFillProfile.company_account = {
    account_exists: null, sign_in_email: "", password_manager_url: "",
  };
  if (control.dataset.contextKind === "override") {
    const key = control.dataset.contextKey;
    if (control.value) state.quickFillProfile.answer_overrides[key] = control.value;
    else delete state.quickFillProfile.answer_overrides[key];
  } else if (control.dataset.contextKind === "account") {
    const property = control.dataset.contextProperty;
    state.quickFillProfile.company_account[property] = property === "account_exists"
      ? control.value === "" ? null : control.value === "yes"
      : control.value;
  }
  state.quickFillContextVersion += 1;
  const version = state.quickFillContextVersion;
  const snapshot = profileContextDocument();
  clearTimeout(state.quickFillContextTimer);
  setQuickFillSaveState("Unsaved changes");
  state.quickFillContextTimer = setTimeout(async () => {
    setQuickFillSaveState("Saving…");
    try {
      const saved = await persistQuickFillContext(snapshot, job);
      if (version === state.quickFillContextVersion && contextKey === state.quickFillContextKey) {
        state.quickFillSavedContext = profileContextDocument(saved);
        state.quickFillProfile.answer_overrides = saved.answer_overrides;
        state.quickFillProfile.company_account = saved.company_account;
        setQuickFillSaveState("Saved automatically");
      }
    } catch (error) {
      if (version === state.quickFillContextVersion && contextKey === state.quickFillContextKey && state.quickFillSavedContext) {
        Object.assign(state.quickFillProfile, cloneJson(state.quickFillSavedContext));
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
  const context = profileContextDocument();
  setQuickFillSaveState("Importing…");
  try {
    const parsed = JSON.parse(await file.text());
    const candidate = parsed.profile && typeof parsed.profile === "object" ? parsed.profile : parsed;
    const saved = await persistQuickFillProfile(candidate);
    Object.assign(saved, context);
    state.quickFillProfile = saved;
    state.quickFillSavedProfile = cloneJson(saved);
    state.quickFillSaveVersion += 1;
    state.eligibility.clear();
    if (state.selectedKey) loadEligibility(state.selectedKey);
    renderQuickFill();
    showSnackbar("Quick-fill JSON imported.", {
      action: "Undo",
      duration: 6000,
      onAction: async () => {
        try {
          const restored = await persistQuickFillProfile(previous);
          Object.assign(restored, context);
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

function renderSessionQuickFill() {
  const surface = document.querySelector("#session-quick-fill");
  if (!surface || !state.quickFillEnabled || !state.quickFillProfile) return;
  surface.innerHTML = quickFillInnerMarkup({ embedded: true });
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
  renderSessionQuickFill();
  if (state.quickFillProfile && (state.quickFillOpen || state.quickFillPopout || window.location.pathname.startsWith("/queue/session/")) && currentJob()) {
    loadQuickFillContext();
  }
}

async function loadQuickFillContext() {
  const job = currentJob();
  if (!job || !state.quickFillProfile) return;
  const contextKey = `${job.dedupe_key}\u0000${job.company}`;
  if (contextKey === state.quickFillContextPendingKey) return;
  state.quickFillContextPendingKey = contextKey;
  state.quickFillContextKey = contextKey;
  state.quickFillContextLoadVersion += 1;
  const version = state.quickFillContextLoadVersion;
  state.quickFillProfile.answer_overrides = {};
  state.quickFillProfile.company_account = null;
  state.copiedByJob.set(job.dedupe_key, new Set());
  renderQuickFill();
  try {
    const params = new URLSearchParams({ job: job.dedupe_key, company: job.company });
    const response = await fetch(`/api/v1/profile?${params}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Job-specific Quick-fill details did not respond.");
    const profile = (await response.json()).profile || {};
    const selected = currentJob();
    if (version !== state.quickFillContextLoadVersion || !selected || `${selected.dedupe_key}\u0000${selected.company}` !== contextKey) return;
    state.quickFillProfile = profile;
    state.quickFillSavedProfile = cloneJson(profile);
    state.quickFillSavedContext = profileContextDocument(profile);
    state.copiedByJob.set(job.dedupe_key, new Set(profile.copied_fields || []));
    state.quickFillError = "";
  } catch (error) {
    if (version === state.quickFillContextLoadVersion) {
      state.quickFillError = error instanceof Error ? error.message : "Job-specific Quick-fill details did not respond.";
    }
  } finally {
    if (version === state.quickFillContextLoadVersion) {
      state.quickFillContextPendingKey = "";
      renderQuickFill();
      renderSessionQuickFill();
    }
  }
}

function openQuickFill() {
  if (!state.quickFillEnabled) return;
  state.quickFillOpen = true;
  renderQuickFill();
  loadQuickFillContext();
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
  if (jobKey !== "none") {
    fetch("/api/v1/profile/copy", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ dedupe_key: jobKey, target_key: item.key }),
    }).then(async (response) => {
      if (response.ok) return;
      state.copiedByJob.get(jobKey)?.delete(item.key); renderQuickFill();
      showSnackbar((await response.json().catch(() => ({}))).error || "Copy marker could not be saved.");
    }).catch(() => {
      state.copiedByJob.get(jobKey)?.delete(item.key); renderQuickFill(); showSnackbar("Copy marker could not be saved.");
    });
  }
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

function openCapture(url = "", title = "") {
  if (!captureDialog || captureDialog.open) return;
  captureError.hidden = true;
  captureError.textContent = "";
  captureForm.reset();
  captureForm.elements.url.value = url;
  if (title) captureForm.elements.title.value = title;
  captureDialog.showModal();
  captureForm.elements.url.focus();
}

async function submitCapture() {
  const payload = Object.fromEntries(new FormData(captureForm));
  const button = document.querySelector("#capture-submit");
  button.disabled = true;
  captureError.hidden = true;
  try {
    const response = await fetch("/api/v1/capture", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "The posting could not be added.");
    captureDialog.close();
    state.status = "all";
    state.selectedKey = result.job.dedupe_key;
    state.scrollTop = 0;
    syncInboxUrl();
    await loadInbox();
    showSnackbar("Job added to Inbox.");
  } catch (error) {
    captureError.textContent = error instanceof Error ? error.message : "The posting could not be added.";
    captureError.hidden = false;
    document.querySelector("#capture-manual").open = true;
  } finally {
    button.disabled = false;
  }
}

function selectJob(key, { push = true, focusDetail = false } = {}) {
  if (!state.jobs.some((job) => job.dedupe_key === key)) return;
  const changed = key !== state.selectedKey;
  const update = () => {
    state.selectedKey = key;
    if (push) syncInboxUrl();
    renderSelectedJob();
  };
  const reduceMotion = document.documentElement.dataset.motion === "reduce"
    || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (changed && !reduceMotion && document.startViewTransition && document.querySelector(".reading-pane")?.getClientRects().length) {
    jobViewTransition?.skipTransition();
    const transition = document.startViewTransition(update);
    jobViewTransition = transition;
    transition.finished.finally(() => {
      if (jobViewTransition === transition) jobViewTransition = null;
    }).catch(() => {});
    if (focusDetail) transition.updateCallbackDone.then(() => document.querySelector("#job-title")?.focus({ preventScroll: true })).catch(() => {});
  } else {
    update();
    if (focusDetail) document.querySelector("#job-title")?.focus({ preventScroll: true });
  }
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
  if (state.copySelectionMode || event.ctrlKey || event.metaKey) {
    if (state.selectedKeys.has(key)) state.selectedKeys.delete(key); else state.selectedKeys.add(key);
    state.rangeAnchor = index; state.selectedKey = key; syncInboxUrl(); renderInbox(); return;
  }
  state.selectedKeys.clear(); state.rangeAnchor = index; syncVisibleJobSelection(); selectJob(key);
}

async function patchJob(job, status, notes = job.notes || "") {
  const response = await secureWrite(`/api/v1/jobs/${encodeURIComponent(job.dedupe_key)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ status, notes }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The change could not be saved.");
  return response.json();
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
    results.forEach((result, index) => {
      if (result.status !== "fulfilled") return;
      jobs[index].status = result.value.status;
      if (result.value.liveness) Object.assign(jobs[index], {
        liveness_status: result.value.liveness.status,
        liveness_evidence: result.value.liveness.evidence,
        liveness_checked_at: new Date().toISOString(),
      });
    });
    state.undo = { previous, next: jobs.map((job) => ({ key: job.dedupe_key, status: job.status })) };
    const noun = jobs.length === 1 ? "role" : `${new Intl.NumberFormat().format(jobs.length)} roles`;
    const closed = results.filter((result) => result.status === "fulfilled" && result.value.status === "archived" && result.value.liveness?.status === "closed").length;
    const message = closed ? `${closed === 1 ? "Posting" : `${closed} postings`} closed and skipped.` : `${label} ${noun}.`;
    showSnackbar(message, { action: "Undo", onAction: undoLastDecision, duration: 6000 });
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
    undo.next.forEach((snapshot) => { const job = state.jobs.find((candidate) => candidate.dedupe_key === snapshot.key); if (job) job.status = snapshot.status; });
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
  state.selectedKeys.clear(); syncVisibleJobSelection(); selectJob(jobs[next].dedupe_key);
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
  if (event.target instanceof Element && event.target.closest(".copy-menu")) {
    if (event.key === "Escape") {
      const menu = event.target.closest(".copy-menu");
      menu.open = false;
      menu.querySelector("summary")?.focus();
      event.preventDefault();
    }
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openCommand(document.activeElement); return; }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && routeRoot() === "inbox" && shortcutScopeAllows(event)) { event.preventDefault(); undoLastDecision(); return; }
  if (event.ctrlKey || event.metaKey || event.altKey) return;
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
    if (key === "q") {
      event.preventDefault();
      const job = state.jobs.find((candidate) => candidate.dedupe_key === state.selectedKey);
      if (jobHasActiveBlocker(job)) { showSnackbar("Override the eligibility verdict before queueing."); return; }
      performDecision("queued", "Queued"); return;
    }
    if (key === "a") {
      const job = state.jobs.find((candidate) => candidate.dedupe_key === state.selectedKey);
      if (jobHasActiveBlocker(job)) { event.preventDefault(); showSnackbar("Override the eligibility verdict before applying."); return; }
      if (job && applicationUrl(job)) { event.preventDefault(); window.open(applicationUrl(job), "_blank", "noopener"); performDecision("applying", "Opened"); }
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
  if (!event.target.closest(".copy-menu")) document.querySelector(".copy-menu[open]")?.removeAttribute("open");
  if (event.target.closest("[data-refresh-quick-fill-access]")) {
    loadInbox().then(() => showSnackbar(state.quickFillEnabled ? "Quick-fill is ready." : state.error || "Quick-fill is not enabled for this session."));
    return;
  }
  const copyScope = event.target.closest("[data-copy-scope]");
  if (copyScope) { copyInboxScope(copyScope.dataset.copyScope); return; }
  if (event.target.closest("[data-copy-selected]")) {
    copyInboxJobs(filteredJobs().filter((job) => state.selectedKeys.has(job.dedupe_key)), { finishSelection: true }); return;
  }
  if (event.target.closest("[data-cancel-copy-selection]")) {
    state.copySelectionMode = false; state.selectedKeys.clear(); renderInbox(); return;
  }
  if (event.target.closest("[data-open-capture]")) { openCapture(); return; }
  if (event.target.closest("[data-close-capture]")) { captureDialog.close(); return; }
  const route = event.target.closest("[data-route]");
  if (route instanceof HTMLAnchorElement) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); navigate(route.href); return;
  }
  const trackerView = event.target.closest("[data-tracker-view]");
  if (trackerView) { state.trackerView = trackerView.dataset.trackerView; syncTrackerUrl(); renderTracker({ focus: true }); return; }
  const trackerSort = event.target.closest("[data-tracker-sort]");
  if (trackerSort) { state.trackerSort = trackerSort.dataset.trackerSort; syncTrackerUrl(); renderTracker(); return; }
  if (event.target.closest("[data-export-tracker]")) { exportTrackerCsv(); return; }
  const reminderAction = event.target.closest("[data-reminder-action]");
  if (reminderAction) { updateReminder(Number(reminderAction.dataset.reminderId), reminderAction.dataset.reminderAction); return; }
  if (event.target.closest("[data-retry-tracker]")) { state.trackerData = null; state.trackerError = ""; loadTrackerData(); return; }
  if (event.target.closest("[data-retry-companies]")) { state.companiesData = null; state.companiesError = ""; loadCompanies(); return; }
  if (event.target.closest("[data-retry-rules]")) { state.rules = null; state.rulesError = ""; loadRules(); return; }
  const undoRule = event.target.closest("[data-undo-rule-action]");
  if (undoRule) { undoRuleAction(Number(undoRule.dataset.undoRuleAction)); return; }
  const startSession = event.target.closest("[data-start-session]");
  if (startSession && startSession.getAttribute("aria-disabled") !== "true") { startApplySession(); return; }
  const queueMove = event.target.closest("[data-queue-move]");
  if (queueMove) {
    const jobs = queueJobs();
    const index = jobs.findIndex((job) => job.dedupe_key === queueMove.dataset.queueKey);
    reorderQueue(queueMove.dataset.queueKey, index + (queueMove.dataset.queueMove === "up" ? -1 : 1));
    return;
  }
  const queueRemove = event.target.closest("[data-queue-remove]");
  if (queueRemove) { removeFromQueue(queueRemove.dataset.queueRemove); return; }
  if (event.target.closest("[data-session-retry-check]")) {
    if (state.applySession) {
      state.applySession.checkError = "";
      verifySessionJob(state.applySession.keys[state.applySession.index]);
      renderApplySession();
    }
    return;
  }
  if (event.target.closest("[data-session-apply]")) { openSessionApplication(); return; }
  if (event.target.closest("[data-session-see-requirements]")) {
    const section = document.querySelector('.apply-session .parsed-section[data-section-key="requirements"]');
    if (section) { section.open = true; section.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }); }
    return;
  }
  if (event.target.closest("[data-session-applied]")) { markSessionApplied(); return; }
  if (event.target.closest("[data-session-not-yet]")) { if (state.applySession) state.applySession.awaitingReturn = ""; renderApplySession({ focus: true }); return; }
  if (event.target.closest("[data-session-skip]")) { advanceApplySession("skipped"); return; }
  if (event.target.closest("[data-session-end]")) { endApplySession(); return; }
  const row = event.target.closest(".job-row[data-job-key]");
  if (row) { handleRowSelection(row, event); return; }
  const companyRole = event.target.closest(".company-role[data-job-key]");
  if (companyRole) { selectJob(companyRole.dataset.jobKey); return; }
  if (event.target.closest("[data-override-eligibility]")) { overrideEligibility(); return; }
  const markApplied = event.target.closest("[data-mark-applied]");
  if (markApplied) { setTrackerStatus(markApplied.dataset.markApplied, "applied"); return; }
  const moveToQueue = event.target.closest("[data-move-to-queue]");
  if (moveToQueue) { setTrackerStatus(moveToQueue.dataset.moveToQueue, "queued"); return; }
  if (event.target.closest("[data-open-posting]")) {
    const job = currentJob();
    if (job && applicationUrl(job)) window.open(applicationUrl(job), "_blank", "noopener");
    return;
  }
  const statusAction = event.target.closest("[data-status-action]");
  if (statusAction && statusAction.getAttribute("aria-disabled") !== "true") { performDecision(statusAction.dataset.statusAction, statusAction.dataset.statusAction === "saved" ? "Saved" : "Queued"); return; }
  const apply = event.target.closest("[data-apply-now]");
  if (apply && apply.getAttribute("aria-disabled") !== "true") {
    const job = state.jobs.find((candidate) => candidate.dedupe_key === state.selectedKey);
    if (job && applicationUrl(job)) { window.open(applicationUrl(job), "_blank", "noopener"); performDecision("applying", "Opened"); }
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
  if (event.target.closest("[data-back-to-list]")) { state.focus = false; navigate(inboxUrl("")); return; }
  if (event.target.closest("[data-retry-jobs]")) { state.loaded = false; state.error = ""; renderInbox(); loadInbox(); return; }
  if (event.target.closest("[data-retry-description]")) { state.descriptions.delete(state.selectedKey); loadDescription(state.selectedKey, { force: true }); renderSelectedJob(); return; }
  if (event.target.closest("[data-save-manual-description]")) { saveManualDescription(); return; }
  if (event.target.closest("[data-retry-overview]")) { loadOverview(state.selectedKey, { force: true }); return; }
  if (event.target.closest("[data-generate-overview]")) { loadOverview(state.selectedKey); if (routeRoot() === "inbox") renderSelectedJob(); else if (window.location.pathname.startsWith("/queue/session/")) renderApplySession(); return; }
  if (event.target.closest("[data-open-command]")) { openCommand(event.target.closest("[data-open-command]")); return; }
  if (event.target.closest("[data-open-shortcuts]")) { openShortcuts(event.target.closest("[data-open-shortcuts]")); return; }
  if (event.target.closest("[data-quick-fill-toggle]")) { state.quickFillOpen ? closeQuickFill() : openQuickFill(); return; }
  const close = event.target.closest("[data-close-dialog]");
  if (close) { close.closest("dialog")?.close(); return; }
  const command = event.target.closest("[data-command]");
  if (command) runCommand(command.dataset.command);
});

document.addEventListener("dragstart", (event) => {
  const trackerCard = event.target.closest(".board-card[data-tracker-key]");
  if (trackerCard) {
    state.trackerDraggedKey = trackerCard.dataset.trackerKey;
    trackerCard.classList.add("is-dragged");
    event.dataTransfer?.setData("text/plain", state.trackerDraggedKey);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
    return;
  }
  const card = event.target.closest(".queue-card[data-queue-key]");
  if (!card) return;
  state.queueDraggedKey = card.dataset.queueKey;
  card.classList.add("is-dragged");
  event.dataTransfer?.setData("text/plain", state.queueDraggedKey);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
});

document.addEventListener("dragover", (event) => {
  if (state.trackerDraggedKey && event.target.closest("[data-board-status]")) { event.preventDefault(); return; }
  if (state.queueDraggedKey && event.target.closest(".queue-card[data-queue-key]")) event.preventDefault();
});

document.addEventListener("drop", (event) => {
  const boardColumn = event.target.closest("[data-board-status]");
  if (boardColumn && state.trackerDraggedKey) {
    event.preventDefault(); const key = state.trackerDraggedKey; state.trackerDraggedKey = "";
    setTrackerStatus(key, boardColumn.dataset.boardStatus); return;
  }
  const target = event.target.closest(".queue-card[data-queue-key]");
  if (!target || !state.queueDraggedKey) return;
  event.preventDefault();
  const jobs = queueJobs();
  reorderQueue(state.queueDraggedKey, jobs.findIndex((job) => job.dedupe_key === target.dataset.queueKey));
  state.queueDraggedKey = "";
});

document.addEventListener("dragend", (event) => {
  event.target.closest(".queue-card")?.classList.remove("is-dragged");
  event.target.closest(".board-card")?.classList.remove("is-dragged");
  state.queueDraggedKey = "";
  state.trackerDraggedKey = "";
});

document.addEventListener("input", (event) => {
  if (event.target.id === "job-search") {
    state.query = event.target.value; state.scrollTop = 0; syncInboxUrl({ replace: true }); renderInbox();
    const search = document.querySelector("#job-search"); search?.focus(); search?.setSelectionRange(state.query.length, state.query.length);
  } else if (event.target.id === "job-notes") {
    const key = state.selectedKey;
    const value = event.target.value;
    clearTimeout(state.notesTimer); document.querySelector("#notes-state").textContent = "Unsaved changes"; state.notesTimer = setTimeout(() => saveNotes(key, value), 600);
  } else if (event.target.id === "manual-description-text") {
    state.manualDescriptionDrafts.set(state.selectedKey, event.target.value);
  } else if (event.target.id === "tracker-search") {
    state.trackerQuery = event.target.value; syncTrackerUrl({ replace: true }); renderTracker();
    const search = document.querySelector("#tracker-search"); search?.focus(); search?.setSelectionRange(state.trackerQuery.length, state.trackerQuery.length);
  } else if (event.target.id === "company-note") {
    const company = state.companiesData?.company?.name;
    const body = event.target.value;
    if (document.querySelector("#company-note-state")) document.querySelector("#company-note-state").textContent = "Unsaved changes";
    clearTimeout(state.companyNoteTimer);
    state.companyNoteTimer = setTimeout(() => saveCompanyNote(company, body), 600);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "job-sort") { state.sort = event.target.value; state.scrollTop = 0; syncInboxUrl(); renderInbox(); }
  else if (event.target.id === "tracker-status") { state.trackerStatus = event.target.value; syncTrackerUrl(); renderTracker(); }
  else if (event.target.matches("[data-board-status-select]")) setTrackerStatus(event.target.dataset.boardStatusSelect, event.target.value);
  else if (event.target.matches("[data-tracker-status-select]")) setTrackerStatus(event.target.dataset.trackerStatusSelect, event.target.value);
  else if (event.target.matches("[data-job-status-select]")) setTrackerStatus(event.target.dataset.jobStatusSelect, event.target.value);
  else if (event.target.id === "linkedin-csv") { const file = event.target.files?.[0]; event.target.value = ""; importLinkedInCsv(file); }
  else if (event.target.matches("[data-rule-toggle]")) toggleRule(Number(event.target.dataset.ruleToggle), event.target.checked);
});

document.addEventListener("load", (event) => {
  if (event.target instanceof HTMLImageElement && event.target.closest(".job-logo, .detail-company-logo, .company-monogram")) {
    const logo = event.target.parentElement;
    if (!event.target.naturalWidth) return;
    logo.classList.add("has-logo");
    if (logo.hasAttribute("data-logo-light")) logo.classList.add("logo-needs-light");
  }
}, true);

document.addEventListener("error", (event) => {
  if (event.target instanceof HTMLImageElement && event.target.closest(".job-logo, .detail-company-logo, .company-monogram")) {
    event.target.hidden = true;
    event.target.parentElement.classList.remove("has-logo", "logo-needs-light");
  }
}, true);

document.addEventListener("focusout", (event) => {
  if (event.target.matches("[data-next-step]")) saveTrackerNextStep(event.target.dataset.nextStep, event.target.value, event.target);
});

document.addEventListener("submit", (event) => {
  if (event.target.id === "capture-form") { event.preventDefault(); submitCapture(); }
  if (event.target.id === "interview-form") { event.preventDefault(); submitInterview(event.target); }
  if (event.target.id === "contact-form") { event.preventDefault(); submitContact(event.target); }
  if (event.target.id === "rule-form") { event.preventDefault(); submitRule(event.target); }
});

document.addEventListener("paste", (event) => {
  if (routeRoot() !== "inbox" || captureDialog.open) return;
  if (event.target.closest("input, textarea, [contenteditable='true']")) return;
  const pasted = event.clipboardData?.getData("text/plain")?.trim() || "";
  if (!/^https:\/\/\S+$/i.test(pasted)) return;
  event.preventDefault();
  openCapture(pasted);
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

for (const dialog of [commandDialog, shortcutDialog, filterDialog, captureDialog]) {
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
  if (!document.hidden) showSubmissionPrompt();
});
window.addEventListener("focus", showSubmissionPrompt);
async function resumeFromIdle() {
  if (document.hidden || !navigator.onLine) return;
  try {
    await refreshSession();
    clearTimeout(sessionRetryTimer);
    sessionRetryTimer = null;
    if (state.error || !state.loaded) await loadInbox();
    if (routeRoot() === "tracker" && state.trackerError) await loadTrackerData({ background: true });
  } catch (error) {
    if (!sessionRetryTimer) {
      sessionRetryTimer = setTimeout(() => { sessionRetryTimer = null; resumeFromIdle(); }, 15000);
    }
    if (Date.now() - lastNetworkNoticeAt > 60000) {
      showSnackbar(error instanceof Error ? error.message : "Connection interrupted. Retrying shortly…");
      lastNetworkNoticeAt = Date.now();
    }
  }
}
document.addEventListener("visibilitychange", resumeFromIdle);
window.addEventListener("focus", resumeFromIdle);
window.addEventListener("online", () => { updateOfflineState(); loadInbox(); });
window.addEventListener("offline", updateOfflineState);

history.scrollRestoration = "manual";
if (window.location.pathname === "/" || window.location.pathname === "/index.html") navigate("/inbox", { replace: true }); else renderRoute();

const shareParams = new URLSearchParams(window.location.search);
const sharedUrl = shareParams.get("share_url") || shareParams.get("url") || (shareParams.get("text") || "").match(/https:\/\/\S+/)?.[0];
if (sharedUrl && routeRoot() === "inbox") {
  history.replaceState(history.state, "", inboxUrl());
  openCapture(sharedUrl, shareParams.get("title") || "");
}
const bookmarklet = document.querySelector("#capture-bookmarklet");
bookmarklet.href = `javascript:(()=>{window.open(${JSON.stringify(`${window.location.origin}/inbox?share_url=`)}+encodeURIComponent(location.href),'_blank')})()`;
bookmarklet.addEventListener("click", (event) => { event.preventDefault(); showSnackbar("Drag this link to your bookmarks bar."); });

const themeColor = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
document.querySelector('meta[name="theme-color"]')?.setAttribute("content", themeColor);
updateOfflineState();
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}
