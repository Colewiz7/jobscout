"use strict";

import { bindObjectMenus, toggleMenu } from "./action-menu.js";
import { inboxCopyText } from "./copy-text.js";
import { countLabel } from "./counts.js";
import { findPostingHighlights, HIGHLIGHT_LABELS } from "./highlight-terms.js";
import { ariaKeyshortcuts, formatShortcut, isTextEntry, matchShortcut } from "./shortcuts.js";
import { Countdown, EditSessions, UndoStack, closedUndoIds, exitDelay, mergeSnackbar, snackbarDuration, textChange, withdrawSnackbar } from "./undo-stack.js";
import { fuzzyScore, menuCommands, rankCommands } from "./command-palette.js";
import { recordedAs, restoreUnsaved, routeIndex, saveStep, sessionFromServer, sessionLists, sessionPatch, staleRead, stepAfter, stepUndo, undoStep, undoUnsaved, withRecord } from "./apply-session.js";
import { bindDrag, flip, flipKeyed, placeNodes } from "./drag.js";
import { boardUndoOrders, cancelAnnouncement, columnKeys, createLoadGate, createOrderSaver, liftAnnouncement, moveAnnouncement, moveKey, placeKey, positionedKeys, restoreOrder, sortedInsertIndex, trackerCompare } from "./drag-logic.js";
import { adoptIds, applyOp, eligibilityWarning, fieldKey, itemId, limitReason, mergeDocuments, newId, opKeys, PROFILE_KINDS, referenceWarning, renameOverrides, restoredMessage, withOrder } from "./profile-edit.js";
import { conflictMessage, createSaveQueue } from "./save-queue.js";
import { changedFields, fromLocalInput, pickFields, splitInterviews, toLocalInput } from "./record-edit.js";
import { COMPANY_FILTERS, companyFilter, filterCompanies, linkHref, linkLabel, mergeCopy, mergeTargets } from "./company-edit.js";
import { cleanTag, dateInputValue, hasTag, moveId, pickedDay, reminderUndo, removableTags, RULE_KINDS, ruleValueHelp, snoozeChoices, tagOptions, upcomingDay, withManualTag } from "./inbox-edit.js";
import { companyDismissTargets, companyLabel, eligibilityVerdict, fitActions, loadLatest, moveGroup, pruneKeys, rangeSelection, restoreSlots, selectAllVisible, splitEligible, stepKey, toggleKey, visibleSelection } from "./selection.js";

const routeView = document.querySelector("#route-view");
const commandDialog = document.querySelector("#command-dialog");
const commandInput = document.querySelector("#command-input");
const commandResults = document.querySelector("#command-results");
const shortcutDialog = document.querySelector("#shortcut-dialog");
const filterDialog = document.querySelector("#filter-dialog");
const filterForm = document.querySelector("#filter-form");
const sourceOptions = document.querySelector("#source-options");
const remoteFilter = document.querySelector("#remote-filter");
const tagGroup = document.querySelector("#tag-filter-group");
const tagOptionsList = document.querySelector("#tag-options");
const savedViewName = document.querySelector("#saved-view-name");
const snackbarRegion = document.querySelector("#snackbar-region");
const assertiveRegion = document.querySelector("#assertive-region");
const reorderRegion = document.querySelector("#reorder-region");
const quickFillTrigger = document.querySelector("#quick-fill-trigger");
const quickFillSheet = document.querySelector("#quick-fill-sheet");
const captureDialog = document.querySelector("#capture-dialog");
const confirmDialog = document.querySelector("#confirm-dialog");
const editDialog = document.querySelector("#edit-dialog");
const editForm = document.querySelector("#edit-form");
const captureForm = document.querySelector("#capture-form");
const captureError = document.querySelector("#capture-error");
const offlineState = document.querySelector("#offline-state");

const ROW_HEIGHT = 88;
const DIVIDER_HEIGHT = 32;
const LAST_VISIT_KEY = "jobseer.inboxLastVisit";
const allowedStatuses = new Set(["all", "new", "saved", "queued", "applying", "applied", "interviewing", "offer", "rejected", "archived"]);
const allowedSorts = new Set(["score", "newest", "company"]);
const boardStatuses = ["applying", "applied", "interviewing", "offer", "rejected"];
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
  more: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5.5h.01M12 12h.01M12 18.5h.01" stroke-width="3"/></svg>',
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
  { id: "archived", label: "Go to Archived", detail: "Restore dismissed jobs", route: "/inbox?status=archived", icon: "inbox" },
  { id: "add-job", label: "Add job", detail: "Add a posting by its link", action: "capture", icon: "inbox" },
  { id: "new-view", label: "New saved view", detail: "Save the current Inbox filters", action: "new-view", icon: "filter" },
  { id: "shortcuts", label: "Show keyboard shortcuts", detail: "Review the power-user map", action: "shortcuts", shortcut: "?" },
];

const state = {
  jobs: [], loaded: false, loading: false, error: "", csrf: "", refreshedAt: null,
  query: "", status: "new", sort: "score", source: "", remote: false, tag: "", focus: false,
  selectedKey: "", selectedKeys: new Set(), rangeAnchor: "", rangeBase: new Set(), copySelectionMode: false,
  rowSelection: { queue: { keys: new Set(), anchor: "", base: new Set(), focus: "" }, tracker: { keys: new Set(), anchor: "", base: new Set(), focus: "" } }, scrollTop: 0,
  entries: [], totalHeight: 0, lastVisit: readStoredDate(LAST_VISIT_KEY), savedViews: [], savedViewsOrderRevision: 0,
  pending: new Set(), notesTimer: null, notesDraft: null, skeletonAt: 0, descriptions: new Map(),
  manualDescriptionDrafts: new Map(), manualDescriptionSaving: new Set(),
  aiOverviewEnabled: false, overviews: new Map(), overviewCacheAttempted: new Set(), overviewPollTimers: new Map(),
  eligibility: new Map(),
  quickFillEnabled: false, quickFillOpen: false, quickFillProfile: null,
  quickFillError: "", quickFillQuery: "", quickFillItems: [], atsOrdering: {},
  copiedByJob: new Map(), copiedKey: "", quickFillPopout: null,
  quickFillEdit: false, quickFillSaveState: "", quickFillSaveTimer: null,
  quickFillSetupSaving: false,
  quickFillSaveVersion: 0, quickFillImportVersion: -1, quickFillSavedProfile: null,
  quickFillAliases: {}, quickFillProfileError: null,
  quickFillContextKey: "", quickFillContextPendingKey: "", quickFillContextLoadVersion: 0,
  quickFillSavedContext: null,
  sessionStarting: false, statusSaving: new Set(),
  applySession: null, sessionTimer: null, sessionLoad: "",
  trackerData: null, trackerLoading: false, trackerError: "", trackerShowLoader: false,
  trackerView: "table", trackerStatus: "all", trackerSort: "last_activity",
  trackerQuery: "",
  companiesData: null, companyLoadedName: null, companiesLoading: false,
  companiesError: "", companiesShowLoader: false, companyNoteTimer: null, companyNoteDraft: null,
  companiesReload: false, companiesFocusPending: false, companyQuery: "", companyShow: "all",
  rules: null, rulesLoading: false, rulesError: "", rulesOrderRevision: 0,
  companyLogos: {}, companyIconKeys: new Set(), pastInterviewsOpen: false,
  upcomingFollowupsOpen: false, doneFollowupsOpen: false,
};

let commandSelection = 0;
let paletteEntries = [];
let commandMatches = commands;
let dialogTrigger = null;
let editor = null;
let goChordUntil = 0;
let loadingTimer = null;
let snackbarCurrent = null;
const snackbarQueue = [];
const undoStack = new UndoStack(20);
const textSessions = new EditSessions();
// hide tokens expire with the bar
const closingUndos = new Set();
let jobViewTransition = null;
let sessionCheckedAt = 0;
let sessionCheckPromise = null;
let inboxRetryTimer = null;
let sessionRetryTimer = null;
let lastNetworkNoticeAt = 0;
let inboxLoadedAt = 0;
let queuePayObserver = null;

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
  noticeHidden(response);
  return response;
}

// another tab hid this job
function noticeHidden(response) {
  if (response.status !== 410) return;
  reloadJobs();
  if (state.trackerData) loadTrackerData({ background: true });
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
  state.tag = params.get("tag") || "";
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
  if (state.tag) params.set("tag", state.tag);
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

// the inbox tab that holds it
function jobHref(key) {
  const status = state.jobs.find((job) => job.dedupe_key === key)?.status || "all";
  return `/inbox/${encodeURIComponent(key)}${status === "new" ? "" : `?status=${status}`}`;
}

function roleLinkMarkup(key, title) {
  return `<a class="role-link" href="${escapeHtml(jobHref(key))}" data-route>${escapeHtml(title)}</a>`;
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
    && (!state.remote || isRemote(job))
    && (!state.tag || hasTag(job, state.tag)));
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
  const tabs = [["new", "New"], ["saved", "Saved"], ["queued", "Queued"], ["archived", "Archived"], ["all", "All"]];
  return tabs.map(([value, label]) => `<button class="status-tab interactive" type="button" role="tab" data-status-tab="${value}" aria-selected="${state.status === value}" tabindex="${state.status === value ? "0" : "-1"}"><span>${label}</span><span class="count">${new Intl.NumberFormat().format(counts[value] || 0)}</span></button>`).join("");
}

function activeFilterChips() {
  const chips = [];
  if (state.query) chips.push(["query", `Search: ${state.query}`]);
  if (state.source) chips.push(["source", state.source]);
  if (state.remote) chips.push(["remote", "Remote"]);
  if (state.tag) chips.push(["tag", `Tag: ${state.tag}`]);
  return chips.map(([key, label]) => `<button class="filter-chip interactive" type="button" data-clear-filter="${key}" aria-label="Remove ${escapeHtml(label)} filter"><span>${escapeHtml(label)}</span>${icons.close}</button>`).join("");
}

function savedViewsMarkup() {
  if (!state.savedViews.length) return "";
  const chip = (view, index) => `<li class="saved-view-wrap" data-saved-view-id="${view.id}"><button class="saved-view-handle drag-handle interactive" type="button" data-drag-handle aria-label="Reorder ${escapeHtml(view.name)}" title="Reorder ${escapeHtml(view.name)}">${icons.queue}</button><button class="saved-view interactive" type="button" data-saved-view="${index}">${escapeHtml(view.name)}</button>${moreButtonMarkup(`${view.name} view`)}</li>`;
  return `<div class="saved-views"><span id="saved-views-label">Views</span><ul class="saved-view-list" aria-labelledby="saved-views-label">${state.savedViews.map(chip).join("")}</ul></div>`;
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
  if (job.connections_count) chips.push(countLabel(job.connections_count, "connection"));
  if (sources.length > 1) chips.push(`${sources.length} sources`);
  if (job.nonpublic_site) chips.unshift("Check apply site");
  const location = job.location ? `<span class="row-location">${escapeHtml(job.location)}</span>` : "";
  return `<button class="job-row interactive" type="button" role="option" data-offset="${entry.offset}" data-job-key="${escapeHtml(job.dedupe_key)}" data-job-index="${entry.jobIndex}" aria-selected="${bulkSelected}"${selected ? ' aria-current="true"' : ""} tabindex="${selected ? "0" : "-1"}">
    ${companyLogoMarkup(job.company)}
    ${fresh ? '<span class="unread-dot" aria-label="Unread"></span>' : ""}
    <span class="job-row-copy"><span class="job-row-title" title="${escapeHtml(job.title)}">${job.ghost_job ? '<span class="row-warning" aria-label="Repeated posting pattern" title="Repeated posting pattern">!</span>' : ""}${escapeHtml(job.title)}</span><span class="job-row-meta" title="${escapeHtml([job.company, job.location].filter(Boolean).join(", "))}"><span>${escapeHtml(job.company)}</span>${location}</span></span>
    <span class="job-row-end">${chips.slice(0, 2).map((chip) => `<span class="row-chip">${escapeHtml(chip)}</span>`).join("")}${job.first_seen ? `<time datetime="${escapeHtml(job.first_seen)}" title="${escapeHtml(formatAbsolute(job.first_seen))}">${escapeHtml(formatDate(job.first_seen))}</time>` : ""}</span>
    <span class="row-more" data-menu-trigger aria-hidden="true">${icons.more}</span>
    ${bulkSelected ? '<span class="selection-check" aria-hidden="true">✓</span>' : ""}
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
  const archive = state.status === "archived" && !state.query && !state.source && !state.remote && !state.tag;
  if (archive) return '<div class="list-empty"><h2>Nothing archived.</h2><p>Roles you dismiss wait here in case you change your mind.</p></div>';
  const filtered = Boolean(state.query || state.source || state.remote || state.tag || state.status !== "new");
  return `<div class="list-empty"><h2>${filtered ? "No roles match this view." : "Your inbox is clear."}</h2><p>${filtered ? "Change or clear the active filters." : "New roles will appear here when the scout finds them."}</p>${filtered ? '<button class="tonal-button interactive" type="button" data-clear-all-filters>Clear filters</button>' : ""}</div>`;
}

async function copyInboxJobs(jobs, { finishSelection = false } = {}) {
  if (!jobs.length) return;
  const contents = inboxCopyText(jobs);
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
    state.rangeAnchor = "";
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

function queuePayValue(job) {
  if (job.pay) return job.pay;
  const detail = state.descriptions.get(job.dedupe_key);
  if (detail?.error || detail?.value?.description_error) return "Unavailable";
  if (job.pay_checked || detail?.value?.description_text) return "Not stated";
  return "Checking…";
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
  const correct = compact ? "" : '<button class="text-button interactive" type="button" data-edit-posting="description">Correct description</button>';
  return parsed || original ? `<section class="detail-section posting-sections" aria-labelledby="description-heading"><h2 id="description-heading">About the role</h2>${legend ? `<div class="highlight-key" aria-label="Highlight key">${legend}</div>` : ""}${parsed}${original}${correct}</section>` : "";
}

function overviewMarkup(job) {
  if (!state.aiOverviewEnabled) return "";
  const entry = state.overviews.get(job.dedupe_key);
  if (!entry) return `<section class="detail-section ai-overview overview-resolved"><div class="overview-heading"><h2>AI overview</h2><span>Selected from the posting</span></div><button class="tonal-button interactive" type="button" data-generate-overview>Generate overview</button></section>`;
  if (entry.checking) return `<section class="detail-section ai-overview overview-checking" aria-label="Checking for a saved AI overview"><div class="overview-heading"><h2>AI overview</h2><span>Checking saved overview</span></div><div class="overview-placeholder${entry.showSkeleton ? " skeleton" : ""}" aria-hidden="true"></div></section>`;
  const shown = entry.regenerating && entry.items?.length;
  if (!shown && (entry.loading || entry.status === "queued" || entry.status === "running")) return `<section class="detail-section ai-overview overview-checking" data-overview-section tabindex="-1" aria-live="polite"><div class="overview-heading"><h2>AI overview</h2><span>${entry.status === "queued" ? "Queued for the next run" : "Reading the posting"}</span></div><div class="overview-placeholder skeleton" aria-hidden="true"></div></section>`;
  if (entry.error) return `<section class="detail-section overview-error"><h2>AI overview</h2><p>${escapeHtml(entry.error)}</p><button class="text-button interactive" type="button" data-retry-overview>Retry overview</button></section>`;
  if (entry.dismissed && entry.items?.length) return `<section class="detail-section ai-overview overview-dismissed" data-overview-section tabindex="-1" aria-labelledby="ai-overview-heading"><div class="overview-heading"><h2 id="ai-overview-heading">AI overview</h2><span>Dismissed for this job</span></div><button class="text-button interactive" type="button" data-restore-overview>Show overview</button></section>`;
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
  return `<section class="detail-section ai-overview overview-resolved" data-overview-section tabindex="-1" aria-labelledby="ai-overview-heading"><div class="overview-heading"><h2 id="ai-overview-heading">${fallback ? "Posting overview" : "AI overview"}</h2>${shown ? '<span aria-live="polite">Regenerating. This one stays until the new one is ready.</span>' : `<span>${fallback ? "Model response incomplete; showing source-checked facts" : "Only facts stated in the posting"}</span><div class="overview-actions"><button class="text-button interactive" type="button" data-regenerate-overview>Regenerate</button><button class="text-button interactive" type="button" data-dismiss-overview>Dismiss</button></div>`}</div>${legend ? `<div class="highlight-key overview-highlight-key" aria-label="Highlight key">${legend}</div>` : ""}<dl class="overview-grid">${groups}</dl></section>`;
}

function atGlanceMarkup(job) {
  const overview = state.overviews.get(job.dedupe_key);
  if (state.aiOverviewEnabled && overview?.items?.length && !overview.dismissed) return "";
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
    : result.overridden ? "The original blocker stays on record, and revoking brings it back." : "Confirm these details before applying.";
  const evidence = (active.length ? active : result.overridden ? result.blockers : findings).map((item) => `
    <li><span aria-hidden="true">${item.overridden ? "✓" : "!"}</span><div><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(item.evidence)}</p><small>${escapeHtml(item.comparison)} · ${escapeHtml(item.provenance)}</small></div></li>`).join("");
  const notes = active.length ? [] : [...new Set((result.overrides || []).map((item) => item.note).filter(Boolean))];
  const noteMarkup = notes.map((note) => `<p class="override-note"><span>Your note</span>${escapeHtml(note)}</p>`).join("");
  const action = active.length
    ? '<button class="outlined-button interactive" type="button" data-override-eligibility>Override verdict</button>'
    : (result.overrides || []).length ? '<button class="text-button interactive" type="button" data-revoke-override>Revoke override</button>' : "";
  return `<section class="eligibility-banner${active.length ? " is-blocked" : ""}" aria-labelledby="eligibility-heading"><div><p class="banner-label">Eligibility</p><h2 id="eligibility-heading" tabindex="-1"><span aria-hidden="true">${active.length ? "!" : "✓"}</span>${heading}</h2><p>${intro}</p></div><ul>${evidence}</ul>${noteMarkup}${action ? `<div class="eligibility-actions">${action}</div>` : ""}</section>`;
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
  if (!job && !state.entries.some((entry) => entry.type === "job")) return ""; // the list already explains why
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
  const activityDetails = history.length ? `<div class="activity-timeline">${history.map((item) => `<div data-activity-id="${item.id}">${statusMarkup(item.to_status)}<span class="activity-end"><time datetime="${escapeHtml(item.changed_at)}">${escapeHtml(formatAbsolute(item.changed_at))}</time>${item.id ? `<button class="icon-button interactive activity-dismiss" type="button" data-dismiss-activity="${item.id}" aria-label="Dismiss ${escapeHtml(statusLabel(item.to_status))} from ${escapeHtml(formatAbsolute(item.changed_at))}" title="Dismiss from the timeline">${icons.close}</button>` : ""}</span></div>`).join("")}</div>` : "";
  const interviewDetails = interviews.length ? `<div class="activity-interviews">${interviews.map((item) => `<div><strong>${item.cancelled_at ? "Cancelled interview" : "Interview"}</strong><time datetime="${escapeHtml(item.starts_at)}">${escapeHtml(formatAbsolute(item.starts_at))}</time>${item.location ? `<span>${escapeHtml(item.location)}</span>` : ""}</div>`).join("")}</div>` : "";
  const repostWarning = job.ghost_job ? `<p class="company-warning repost-warning"><span aria-hidden="true">!</span>This title has appeared ${new Intl.NumberFormat().format(job.repost_count)} times in 90 days: ${job.repost_dates.map((value) => escapeHtml(value)).join(", ")}. This is an observation, not a claim about the employer.</p>` : "";
  const ruleNotice = job.archived_by_rule ? `<div class="rule-notice"><div><strong>Archived by rule</strong><p>${escapeHtml(job.archived_by_rule)}</p></div><button class="text-button interactive" type="button" data-undo-rule-action="${job.rule_action_id}">Undo</button></div>` : "";
  const siteWarning = job.nonpublic_site ? `<p class="company-warning"><span aria-hidden="true">!</span>This link came from a non-public Workday site. ${job.public_apply_url ? "Apply opens the verified public RTX posting." : "No exact public posting was verified. Find it on the employer’s public careers site before applying."}</p>` : "";
  const applyingActions = job.status === "applying"
    ? `<button class="filled-button interactive" type="button" data-mark-applied="${escapeHtml(job.dedupe_key)}" ${pending || state.statusSaving.has(job.dedupe_key) ? "disabled" : ""}>Mark applied</button><button class="tonal-button interactive" type="button" data-move-to-queue="${escapeHtml(job.dedupe_key)}" ${pending || state.statusSaving.has(job.dedupe_key) ? "disabled" : ""}>Back to queue</button>`
    : "";
  const normalActions = job.status === "applying" ? applyingActions
    : `<button class="filled-button interactive" type="button" ${job.status === "applied" ? `data-move-to-queue="${escapeHtml(job.dedupe_key)}"` : 'data-status-action="queued" aria-keyshortcuts="Q" title="Queue (Q)"'} ${pending || eligibilityBlocked || job.status === "queued" ? 'aria-disabled="true"' : ""}>${job.status === "queued" ? "Queued" : job.status === "applied" ? "Back to queue" : "Queue"}</button><button class="tonal-button interactive" type="button" data-status-action="saved" aria-keyshortcuts="S" title="Save (S)" ${pending || job.status === "saved" ? 'aria-disabled="true"' : ""}>${job.status === "saved" ? "Saved" : "Save"}</button><button class="outlined-button interactive" type="button" ${job.status === "applied" ? 'data-open-posting' : 'data-apply-now aria-keyshortcuts="A" title="Apply now (A)"'} ${!applicationUrl(job) || pending || eligibilityBlocked ? 'aria-disabled="true"' : ""}>${job.status === "applied" ? "Open posting" : "Apply now"}</button>`;
  return `<article class="job-detail" aria-labelledby="job-title">
    <header class="job-detail-header"><button class="detail-back text-button interactive" type="button" data-back-to-list>← Inbox</button><div class="detail-identity">${companyLogoMarkup(job.company, "detail-company-logo")}<div class="detail-heading"><h1 id="job-title" tabindex="-1">${escapeHtml(job.title)}</h1><a href="/companies/${encodeURIComponent(job.company || "")}" data-route>${escapeHtml(job.company)}</a>${job.location ? `<p class="detail-location">${escapeHtml(job.location)}</p>` : ""}</div></div>
      <dl class="fact-strip">${factItem("Posted", formatDate(job.first_seen))}${factItem("Pay", job.pay)}${factItem("Source", sourceText)}${factItem("Term", job.terms)}${factItem("Deadline", deadlineLabel(description?.deadline))}</dl>
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
      <section class="detail-section" aria-labelledby="posting-details-heading"><h2 id="posting-details-heading">Posting details</h2><dl class="detail-facts">${factItem("Status", visibleStatus)}${factItem("First seen", formatAbsolute(job.first_seen))}${factItem("Last seen", formatAbsolute(job.last_seen))}${factItem("Sources", sourceText)}${factItem("Tags", (job.manual_tags || []).join(", "))}${factItem("Rule tags", (job.rule_tags || []).join(", "))}${factItem("Ranking boost", job.rule_boost ? `+${job.rule_boost} from rules` : "")}${factItem("Connections", job.connections_count ? `${new Intl.NumberFormat().format(job.connections_count)} at company` : "")}</dl><div class="posting-detail-actions">${job.url ? `<a class="original-link" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">Open original posting ${icons.external}</a>` : ""}<button class="text-button interactive" type="button" data-edit-posting ${pending ? 'aria-disabled="true"' : ""}>Edit posting</button><button class="text-button interactive" type="button" data-add-tag>Add tag</button></div></section>
      <section class="detail-section" aria-labelledby="activity-heading"><h2 id="activity-heading" tabindex="-1">Activity</h2><div class="status-line"><span aria-hidden="true"></span><strong>${escapeHtml(visibleStatus)}</strong>${job.application_updated_at ? `<time datetime="${escapeHtml(job.application_updated_at)}" title="${escapeHtml(formatAbsolute(job.application_updated_at))}">${escapeHtml(formatDate(job.application_updated_at))}</time>` : ""}</div><div class="activity-status-control"><span>Correct status</span>${statusSelectMarkup(job, "data-job-status-select")}</div>${activityDetails}${interviewDetails}${job.resume_name ? `<p class="resume-sent"><span>Resume sent</span><strong>${escapeHtml(job.resume_name)}</strong></p>` : ""}<label class="notes-field" for="job-notes"><span>Notes</span><textarea id="job-notes" rows="5" placeholder="Add context for your next step">${escapeHtml(job.notes || "")}</textarea><small id="notes-state">Saved automatically</small></label></section>
      ${recentCompanyWarning || otherRoles.length ? `<section class="detail-section" aria-labelledby="company-history-heading"><h2 id="company-history-heading">Company history</h2>${recentCompanyWarning}<div class="company-roles">${otherRoles.map((other) => `<button class="company-role interactive" type="button" data-job-key="${escapeHtml(other.dedupe_key)}"><span>${escapeHtml(other.title)}</span><span>${escapeHtml(statusLabel(other.status || "new"))}</span></button>`).join("")}</div></section>` : ""}
    </div>
  </article>`;
}

// one table for bar and More
const bulkActions = {
  "inbox-save": { label: "Save", shortcut: "s", run: () => performDecision("saved", "Saved") },
  "inbox-queue": { label: "Queue", shortcut: "q", run: () => queueSelection() },
  "inbox-dismiss": { label: "Dismiss", shortcut: "x", run: () => performDecision("archived", "Dismissed") },
  "inbox-company": { label: "Dismiss company", run: () => dismissSelectedCompanies() },
  "inbox-restore": { label: "Restore", run: () => performDecision("new", "Restored") },
  "inbox-tags": { label: "Tags", menu: () => tagMenuItems([...state.selectedKeys]) },
  "inbox-hide": { label: "Hide…", run: (trigger) => hideInboxJobs([...state.selectedKeys], trigger) },
  "queue-save": { label: "Save for later", shortcut: "s", run: () => dequeueRows(selectedRows("queue")) },
  "queue-top": { label: "Move to top", run: () => moveRowsToEdge(selectedRows("queue"), "top") },
  "queue-bottom": { label: "Move to bottom", run: () => moveRowsToEdge(selectedRows("queue"), "bottom") },
  "queue-apply": { label: "Apply", run: () => startApplySession(selectedRows("queue")) },
  "queue-tags": { label: "Tags", menu: () => tagMenuItems(selectedRows("queue")) },
  "queue-hide": { label: "Hide…", run: (trigger) => hideRows("queue", trigger) },
  "tracker-status": { label: "Change status", menu: () => trackerStatusItems(selectedRows("tracker")) },
  "tracker-tags": { label: "Tags", menu: () => tagMenuItems(selectedRows("tracker")) },
  "tracker-hide": { label: "Hide…", run: (trigger) => hideRows("tracker", trigger) },
};

function bulkButton(id) {
  const { label, shortcut, menu } = bulkActions[id];
  const popup = menu ? ' aria-haspopup="menu" aria-expanded="false"' : "";
  const hint = shortcut ? ` aria-keyshortcuts="${ariaKeyshortcuts(shortcut)}" title="${label} (${formatShortcut(shortcut)})"` : "";
  const kbd = shortcut ? `<kbd class="button-shortcut" aria-hidden="true">${formatShortcut(shortcut)}</kbd>` : "";
  return `<button class="text-button interactive" type="button" data-bulk="${id}" data-bulk-fold${popup}${hint}>${label}${kbd}</button>`;
}

function bulkBarShell(name, count, ids, clearLabel = "Clear selection") {
  return `<div class="bulk-bar" role="group" data-bulk-bar="${name}" aria-label="Bulk actions"><span class="bulk-count"><strong>${new Intl.NumberFormat().format(count)}</strong> selected</span><div class="bulk-actions">${ids.map(bulkButton).join("")}<button class="text-button interactive" type="button" data-bulk-more aria-haspopup="menu" aria-expanded="false" hidden>More${icons.more}</button></div><button class="icon-button interactive" type="button" data-clear-selection="${name}" aria-label="${clearLabel}" aria-keyshortcuts="Escape" title="${clearLabel} (Esc)">${icons.close}</button></div>`;
}

function bulkBarMarkup() {
  const count = state.selectedKeys.size;
  if (state.copySelectionMode) return `<div class="bulk-bar copy-selection-bar" aria-label="Copy selection"><span><strong>${new Intl.NumberFormat().format(count)}</strong> selected · Click jobs, or Shift-click for a range</span><button class="tonal-button interactive" type="button" data-copy-selected ${count ? "" : "disabled"}>Copy selected</button><button class="text-button interactive" type="button" data-cancel-copy-selection>Cancel</button></div>`;
  if (!count) return "";
  const actions = selectionArchived() ? ["inbox-restore"] : ["inbox-save", "inbox-queue", "inbox-dismiss", "inbox-company"];
  return bulkBarShell("inbox", count, [...actions, "inbox-tags", "inbox-hide"]);
}

function runBulk(id, trigger) {
  const action = bulkActions[id];
  if (!action) return;
  if (action.menu) toggleMenu(trigger, action.menu(), { label: action.label });
  else action.run(trigger);
}

// folded menus lose their anchor
function bulkMoreItems(bar, more) {
  let grouped = false;
  return [...bar.querySelectorAll("[data-bulk-fold][hidden]")].flatMap((button) => {
    const action = bulkActions[button.dataset.bulk];
    const after = grouped;
    grouped = Boolean(action.menu);
    if (!action.menu) return [{ label: action.label, shortcut: action.shortcut, divider: after, run: () => action.run(more) }];
    return action.menu().map((item, index) => ({ ...item, divider: index === 0 }));
  });
}

const bulkBarSizes = typeof ResizeObserver === "function" ? new ResizeObserver((entries) => {
  for (const entry of entries) {
    if (entry.target.isConnected) fitBulkBar(entry.target); else bulkBarSizes.unobserve(entry.target);
  }
  liftSnackbars();
}) : null;

// carrel: overflow, never sideways scroll
function fitBulkBar(bar) {
  const group = bar.querySelector(".bulk-actions");
  const more = bar.querySelector("[data-bulk-more]");
  if (!group || !more) return;
  const buttons = [...group.querySelectorAll("[data-bulk-fold]")];
  buttons.forEach((button) => { button.hidden = false; });
  more.hidden = false;
  const gap = parseFloat(getComputedStyle(group).columnGap) || 0;
  const count = fitActions(buttons.map((button) => button.offsetWidth), group.clientWidth, more.offsetWidth, gap);
  buttons.forEach((button, index) => { button.hidden = index >= count; });
  more.hidden = count === buttons.length;
}

function mountBulkBars() {
  for (const bar of document.querySelectorAll(".bulk-bar[data-bulk-bar]")) {
    fitBulkBar(bar);
    bulkBarSizes?.observe(bar);
  }
  liftSnackbars();
}

// snackbars stack above the bar
function liftSnackbars() {
  const tops = [...document.querySelectorAll(".bulk-bar")].map((bar) => bar.getBoundingClientRect()).filter((box) => box.height && box.top < window.innerHeight && box.bottom > window.innerHeight / 2).map((box) => box.top);
  const lift = tops.length ? Math.max(0, window.innerHeight - Math.min(...tops)) : 0;
  document.documentElement.style.setProperty("--bulk-clearance", `${Math.round(lift)}px`);
}

function selectionArchived() {
  return state.selectedKeys.size > 0 && [...state.selectedKeys].every((key) => state.jobs.find((job) => job.dedupe_key === key)?.status === "archived");
}

function copyMenuMarkup() {
  return `<button class="icon-button interactive copy-menu" type="button" data-copy-menu aria-haspopup="menu" aria-expanded="false" aria-label="Copy jobs" title="Copy jobs">${icons.copy}<span class="copy-menu-caret" aria-hidden="true">⌄</span></button>`;
}

function copyMenuItems() {
  const jobs = filteredJobs();
  const format = (value) => new Intl.NumberFormat().format(value);
  const newCount = jobs.filter((job) => job.status === "new").length;
  return [
    { label: "All in view", hint: format(jobs.length), disabled: !jobs.length, run: () => copyInboxScope("all") },
    { label: "New in view", hint: format(newCount), disabled: !newCount, run: () => copyInboxScope("new") },
    { label: "Current job", disabled: !jobs.some((job) => job.dedupe_key === state.selectedKey), run: () => copyInboxScope("single") },
    { label: "Select multiple…", run: () => copyInboxScope("multiple") },
  ];
}

function inboxMarkup() {
  const jobs = filteredJobs();
  buildEntries(jobs);
  state.selectedKeys = visibleSelection(state.selectedKeys, jobs); // else bulk acts on hidden jobs
  if (!state.selectedKey || !state.jobs.some((job) => job.dedupe_key === state.selectedKey)) state.selectedKey = jobs[0]?.dedupe_key || "";
  const selectedJob = jobs.length ? state.jobs.find((job) => job.dedupe_key === state.selectedKey) : undefined; // empty list, nothing to read
  const chips = activeFilterChips();
  return `<section class="inbox-page${state.focus ? " is-focus" : ""}${jobs.length && selectedKeyFromPath() ? " has-route-selection" : ""}">
    <aside class="inbox-list" aria-label="Job inbox"><div class="list-header"><div class="list-title-row"><h1 tabindex="-1">Inbox</h1><span>${new Intl.NumberFormat().format(jobs.length)}</span></div>${savedViewsMarkup()}<label class="job-search" for="job-search">${icons.search}<input id="job-search" type="search" autocomplete="off" placeholder="Search jobs" value="${escapeHtml(state.query)}" aria-label="Search jobs" aria-keyshortcuts="/"></label><div class="list-tools"><div class="status-tabs" role="tablist" aria-label="Job status">${visibleStatusTabs()}</div><button class="icon-button interactive" type="button" data-open-filters aria-label="Filter jobs" title="Filter jobs">${icons.filter}</button>${copyMenuMarkup()}<label class="sort-field"><span class="visually-hidden">Sort jobs</span><select id="job-sort" aria-label="Sort jobs"><option value="score" ${state.sort === "score" ? "selected" : ""}>Best match</option><option value="newest" ${state.sort === "newest" ? "selected" : ""}>Newest</option><option value="company" ${state.sort === "company" ? "selected" : ""}>Company</option></select></label></div>${chips ? `<div class="active-filters">${chips}</div>` : ""}</div>
      <div class="job-viewport" id="job-viewport" ${jobs.length ? 'role="listbox" aria-multiselectable="true" aria-label="Jobs"' : ""} ${state.copySelectionMode ? 'tabindex="-1"' : ""}>${jobs.length ? '<div class="job-list-layer" id="job-list-layer"></div>' : emptyListMarkup()}</div>${bulkBarMarkup()}</aside>
    <section class="reading-pane" id="inbox-reading-pane" aria-label="Role detail">${detailMarkup(selectedJob)}</section>
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
    const job = state.jobs.find((item) => item.dedupe_key === key);
    if (job) {
      job.pay = value.pay || job.pay || "";
      job.pay_checked ||= Boolean(value.description_text);
    }
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
    if (routeRoot() === "queue" && !window.location.pathname.startsWith("/queue/session/")) {
      const card = Array.from(document.querySelectorAll(".queue-card")).find((item) => item.dataset.queueKey === key);
      const job = state.jobs.find((item) => item.dedupe_key === key);
      const fact = card?.querySelector(".queue-pay-fact");
      if (fact && job) fact.querySelector("dd").textContent = queuePayValue(job);
    }
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
    noticeHidden(response);
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
  // a regenerate keeps its old items
  if (["queued", "running"].includes(payload?.status)) {
    const current = state.overviews.get(key);
    // the server keeps the last ready one
    const kept = payload.items?.length ? { items: payload.items, source: payload.source || "ai" }
      : current?.regenerating && current.items?.length ? { items: current.items, source: current.source } : null;
    state.overviews.set(key, kept
      ? { loading: false, status: payload.status, error: "", regenerating: true, dismissed: false, ...kept }
      : { loading: false, status: payload.status, items: [], error: "" });
    scheduleOverviewPoll(key);
  } else if (payload?.items?.length) {
    state.overviews.set(key, { loading: false, items: payload.items, source: payload.source || "ai", error: "", dismissed: Boolean(payload.dismissed) });
    const timer = state.overviewPollTimers.get(key);
    if (timer) clearTimeout(timer);
    state.overviewPollTimers.delete(key);
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

function overviewPath(key, action) {
  return `/api/v1/jobs/${encodeURIComponent(key)}/overview/${action}`;
}

function overviewFocus(selector) {
  return () => (document.querySelector(selector) || document.querySelector("[data-overview-section]"))?.focus({ preventScroll: true });
}

async function setOverviewDismissed(key, dismissed, { offer = true } = {}) {
  const entry = state.overviews.get(key);
  if (!entry?.items?.length) return;
  const refocus = overviewFocus(dismissed ? "[data-restore-overview]" : "[data-dismiss-overview]");
  entry.dismissed = dismissed;
  updateOverviewUI(key);
  refocus();
  const { status, error } = await companyWrite(overviewPath(key, dismissed ? "dismiss" : "restore"), { method: "POST", body: "{}" }, "Couldn't change the overview.");
  // another tab got there first
  if (status === 409) { showSnackbar(dismissed ? "Already dismissed in another tab." : "Already shown in another tab."); return; }
  if (error) {
    entry.dismissed = !dismissed;
    updateOverviewUI(key);
    overviewFocus(dismissed ? "[data-dismiss-overview]" : "[data-restore-overview]")();
    assertiveRegion.textContent = `${error} Change undone.`;
    showSnackbar(`${error} Change undone.`);
    return;
  }
  if (offer) offerUndoSnackbar(dismissed ? "Overview dismissed." : "Overview shown again.", [`overview:${key}`], () => setOverviewDismissed(key, !dismissed, { offer: false }));
  else showSnackbar(dismissed ? "Overview dismissed again." : "Overview shown again.");
}

async function regenerateOverview(key) {
  const before = state.overviews.get(key);
  state.overviews.set(key, before?.items?.length
    ? { ...before, regenerating: true, dismissed: false, status: "running" }
    : { loading: true, status: "running", items: [], error: "" });
  updateOverviewUI(key);
  overviewFocus("[data-overview-section]")();
  const { body, error } = await companyWrite(overviewPath(key, "regenerate"), { method: "POST", body: "{}" }, "Couldn't regenerate the overview.");
  if (error) {
    if (before) state.overviews.set(key, before); else state.overviews.delete(key);
    updateOverviewUI(key);
    assertiveRegion.textContent = error;
    showSnackbar(error);
    return;
  }
  applyOverviewPayload(key, body);
  updateOverviewUI(key);
  if (document.activeElement === document.body) overviewFocus("[data-regenerate-overview]")();
  // only a saved overview has a past
  if (body.can_undo && body.revision != null) offerUndoSnackbar("Regenerating the overview.", [`overview:${key}`], () => undoRegenerate(key, body.revision));
  else showSnackbar("Overview regenerated.");
}

async function undoRegenerate(key, revision) {
  const { body, error } = await companyWrite(overviewPath(key, "regenerate/undo"), { method: "POST", body: JSON.stringify({ expected_revision: revision }) }, "Couldn't undo the regenerate.");
  if (error) { showSnackbar(`Couldn't undo. ${error}`); return; }
  applyOverviewPayload(key, body);
  updateOverviewUI(key);
  showSnackbar("Previous overview restored.");
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
    noticeHidden(response);
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't save that description.");
    const value = (await response.json()).description || {};
    state.descriptions.set(key, { loading: false, showLoader: false, value, error: "" });
    const job = state.jobs.find((item) => item.dedupe_key === key);
    if (job) {
      job.pay = value.pay || "";
      job.pay_checked = Boolean(value.description_text);
    }
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
  if (!state.quickFillEnabled || !key) return;
  const written = await loadLatest(state.eligibility, key, fetchEligibility, force);
  if (written && routeRoot() === "inbox" && state.selectedKey === key) renderSelectedJob();
}

async function fetchEligibility(key) {
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(key)}/eligibility`, {
      credentials: "same-origin", headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Eligibility did not respond.");
    return { loading: false, value: (await response.json()).eligibility, error: "" };
  } catch (error) {
    return { loading: false, value: null, error: error instanceof Error ? error.message : "Eligibility did not respond." };
  }
}

// in order, not document order
function eligibilityFocus() {
  const target = ["[data-revoke-override]", "[data-override-eligibility]", "#eligibility-heading", "#job-title"].map((selector) => document.querySelector(selector)).find(Boolean);
  target?.focus({ preventScroll: true });
}

function applyEligibility(key, value) {
  state.eligibility.set(key, { loading: false, value, error: "" });
  if (routeRoot() === "inbox" && state.selectedKey === key) renderSelectedJob();
  else renderJobSurface();
}

function overrideEligibility(key = state.selectedKey, trigger = null) {
  const active = (state.eligibility.get(key)?.value?.blockers || []).filter((item) => !item.overridden);
  if (!key || !active.length) return;
  const named = new Intl.ListFormat("en-US", { type: "conjunction" }).format(active.map((item) => item.label));
  openEditor({
    title: "Override the eligibility verdict?",
    intro: `${named} will stop blocking this job, so you can queue and apply. The blocker stays on record, and you can revoke the override later.`,
    trigger, focusBack: eligibilityFocus, submit: "Override", always: true,
    fields: [{ name: "note", label: "Why it's fine to apply", value: "", optional: true, rows: 3, maxlength: 1000, help: "Kept with the override, like what a recruiter told you." }],
    save: ({ note = "" }) => saveOverride(key, note),
  });
}

async function saveOverride(key, note) {
  const { body, error } = await companyWrite(`/api/v1/jobs/${encodeURIComponent(key)}/eligibility/override`, { method: "POST", body: JSON.stringify({ note }) }, "The override could not be recorded.");
  if (error) return error;
  applyEligibility(key, body.eligibility);
  offerUndoSnackbar("Eligibility override recorded.", [`override:${key}`], () => changeOverride(key, body.override_ids, false, { undone: "Override undone." }));
  return "";
}

// undo goes by override id
async function changeOverride(key, ids, restore, { undone = "", offer = false } = {}) {
  const url = `/api/v1/jobs/${encodeURIComponent(key)}/eligibility/override${restore ? "/restore" : ""}`;
  const init = restore ? { method: "POST", body: JSON.stringify({ ids }) } : { method: "DELETE", ...(ids ? { body: JSON.stringify({ ids }) } : {}) };
  const inBanner = Boolean(document.activeElement?.closest(".eligibility-banner"));
  const { status, body, error } = await companyWrite(url, init, "Couldn't change the override. Retry shortly.");
  // show what the other tab saved
  if (status === 409 && body.eligibility) { applyEligibility(key, body.eligibility); showSnackbar("The override changed in another tab, so it now shows that version."); return; }
  if (error) { assertiveRegion.textContent = error; showSnackbar(offer ? error : `Couldn't undo. ${error}`); return; }
  applyEligibility(key, body.eligibility);
  if (inBanner || document.activeElement === document.body) eligibilityFocus();
  if (offer) offerUndoSnackbar("Override revoked. The verdict blocks this job again.", [`override:${key}`], () => changeOverride(key, body.ids, true, { undone: "Override restored." }));
  else showSnackbar(undone);
}

function revokeOverride(key = state.selectedKey) {
  if (key) changeOverride(key, null, false, { offer: true });
}

function renderInbox({ focus = false } = {}) {
  if (state.error) routeView.innerHTML = errorMarkup(state.error);
  else if (!state.loaded) routeView.innerHTML = state.skeletonAt ? loadingMarkup() : "";
  else routeView.innerHTML = inboxMarkup();
  document.title = "Inbox · JobSeer";
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
  mountBulkBars();
  if (focus) document.querySelector("#job-title, .inbox-list h1")?.focus({ preventScroll: true });
}

function syncVisibleJobSelection() {
  for (const row of document.querySelectorAll("#job-list-layer .job-row")) {
    const selected = row.dataset.jobKey === state.selectedKey;
    const bulkSelected = state.selectedKeys.has(row.dataset.jobKey);
    row.setAttribute("aria-selected", String(bulkSelected));
    if (selected) row.setAttribute("aria-current", "true"); else row.removeAttribute("aria-current");
    row.tabIndex = selected ? 0 : -1;
    const check = row.querySelector(".selection-check");
    if (bulkSelected && !check) row.insertAdjacentHTML("beforeend", '<span class="selection-check" aria-hidden="true">✓</span>');
    if (!bulkSelected) check?.remove();
  }
  const list = document.querySelector(".inbox-list");
  const oldBar = list?.querySelector(".bulk-bar");
  const nextBar = bulkBarMarkup();
  if (oldBar) oldBar.outerHTML = nextBar;
  else if (nextBar) list?.insertAdjacentHTML("beforeend", nextBar);
  mountBulkBars();
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
  document.title = `${isSession ? "Session" : page[0]} · JobSeer`;
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
    ${rowSelectMarkup("queue", job)}
    <button class="queue-drag-handle drag-handle interactive" type="button" data-drag-handle aria-label="Reorder ${escapeHtml(job.title)}" title="Reorder ${escapeHtml(job.title)}">${icons.queue}</button>
    <div class="queue-card-copy"><h2 title="${escapeHtml(job.title)}">${roleLinkMarkup(job.dedupe_key, job.title)}</h2><p>${escapeHtml(job.company)}</p><dl>${factItem("Location", job.location)}<div class="queue-pay-fact"><dt>Pay</dt><dd>${escapeHtml(queuePayValue(job))}</dd></div>${factItem("Deadline", deadline)}${factItem("Score", job.score == null ? "" : new Intl.NumberFormat().format(job.score))}</dl>${checked}</div>
    <div class="queue-card-actions"><button class="icon-button interactive" type="button" data-queue-move="up" data-queue-key="${escapeHtml(job.dedupe_key)}" aria-label="Move up, ${escapeHtml(job.title)}" title="Move up, ${escapeHtml(job.title)}" ${index === 0 ? "disabled" : ""}>↑</button><button class="icon-button interactive" type="button" data-queue-move="down" data-queue-key="${escapeHtml(job.dedupe_key)}" aria-label="Move down, ${escapeHtml(job.title)}" title="Move down, ${escapeHtml(job.title)}" ${index === count - 1 ? "disabled" : ""}>↓</button><button class="text-button interactive" type="button" data-queue-remove="${escapeHtml(job.dedupe_key)}">Save for later</button>${moreButtonMarkup(job.title)}</div>
  </article>`;
}

function renderQueue({ focus = false } = {}) {
  document.title = "Queue · JobSeer";
  if (state.error) {
    routeView.innerHTML = errorMarkup(state.error);
  } else if (!state.loaded) {
    routeView.innerHTML = state.skeletonAt ? '<section class="page-shell"><div class="queue-skeleton skeleton"></div></section>' : "";
  } else {
    const jobs = queueJobs();
    const held = heldRowSelect();
    routeView.innerHTML = `<section class="queue-page" aria-labelledby="queue-title"><header class="queue-heading"><div><h1 id="queue-title" tabindex="-1">Apply queue</h1><p>${jobs.length ? `${new Intl.NumberFormat().format(jobs.length)} ${jobs.length === 1 ? "role" : "roles"}, ordered for a focused pass.` : "Your next application session starts here."}</p></div>${jobs.length ? `<button class="filled-button interactive" type="button" data-start-session ${state.sessionStarting ? 'aria-disabled="true" aria-busy="true"' : ""}>${state.sessionStarting ? "Checking postings…" : "Start session"}</button>` : ""}</header>${jobs.length ? `<div class="queue-list" aria-label="Queued jobs">${jobs.map((job, index) => queueCard(job, index, jobs.length)).join("")}</div>` : `<div class="empty-state"><h2>Queue a role worth your time.</h2><p>Jobs you queue from Inbox will wait here in deadline order.</p><a class="tonal-button interactive" href="/inbox" data-route>Find roles</a></div>`}</section>`;
    syncRowSelection("queue");
    if (held && !focus) focusRowSelect("queue", held);
  }
  if (focus) document.querySelector("#queue-title, .empty-state h2")?.focus({ preventScroll: true });
  queuePayObserver?.disconnect();
  if (state.loaded && !state.error && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        const key = entry.target.dataset.queueKey;
        const job = state.jobs.find((item) => item.dedupe_key === key);
        if (job && !job.pay && !state.descriptions.has(key)) void loadDescription(key);
      }
    }, { rootMargin: "180px" });
    queuePayObserver = observer;
    document.querySelectorAll(".queue-card").forEach((card) => observer.observe(card));
  }
}

function statusMarkup(status) {
  const symbols = {
    applying: "↗", applied: "✓", interviewing: "◉", offer: "★", rejected: "×",
    queued: "→", saved: "+", archived: "—", new: "•",
  };
  return `<span class="status-word status-${escapeHtml(status)}"><span aria-hidden="true">${symbols[status] || "•"}</span>${escapeHtml(statusLabel(status))}</span>`;
}

const statusOptions = ["new", "saved", "queued", "applying", "applied", "interviewing", "offer", "rejected", "archived"];

function moreButtonMarkup(name) {
  return `<button class="icon-button interactive more-button" type="button" data-menu-trigger aria-haspopup="menu" aria-expanded="false" aria-label="More actions for ${escapeHtml(name)}" aria-keyshortcuts="Shift+F10">${icons.more}</button>`;
}

function statusSelectMarkup(job, attribute) {
  const options = statusOptions;
  return `<label class="status-editor"><span class="visually-hidden">Change status for ${escapeHtml(job.title)}</span><select ${attribute}="${escapeHtml(job.dedupe_key)}" aria-label="Change status for ${escapeHtml(job.title)}" ${state.statusSaving.has(job.dedupe_key) ? "disabled" : ""}>${options.map((status) => `<option value="${status}" ${status === job.status ? "selected" : ""}>${escapeHtml(statusLabel(status))}</option>`).join("")}</select></label>`;
}

function parseTrackerUrl() {
  const params = new URLSearchParams(window.location.search);
  state.trackerView = params.get("view") === "board" ? "board" : params.get("view") === "calibration" ? "calibration" : "table";
  state.trackerStatus = allowedStatuses.has(params.get("status")) ? params.get("status") : "all";
  state.trackerSort = ["company", "status", "applied", "last_activity", "manual"].includes(params.get("sort")) ? params.get("sort") : "last_activity";
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
  applications.sort(trackerCompare(state.trackerSort));
  return applications;
}

function trackerInsights(applications) {
  const now = Date.now();
  const appliedWeek = applications.filter((job) => job.applied_at && now - new Date(job.applied_at) <= 7 * 86400000).length;
  const withDate = applications.filter((job) => job.applied_at);
  const responses = withDate.filter((job) => ["interviewing", "offer", "rejected"].includes(job.status)).length;
  const responseRate = withDate.length ? `${Math.round((responses / withDate.length) * 100)}%` : "None yet";
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
    <div><strong>${escapeHtml(bestResume?.[0] || "No resume data")}</strong><span>${bestResume ? `${bestResume[1].responses} of ${countLabel(bestResume[1].total, "response")}` : "By resume version"}</span></div>
    <div><strong>${escapeHtml(topSource?.[0] || "No source data")}</strong><span>${topSource ? countLabel(topSource[1], "application") : "Funnel by source"}</span></div>
  </div>`;
}

function reminderDue(reminder, now = new Date()) {
  const effective = new Date(reminder.snoozed_until || reminder.due_at);
  return !Number.isNaN(effective.valueOf()) && effective <= now;
}

function reminderRowMarkup(reminder) {
  const done = reminder.status === "done";
  const when = done ? reminder.completed_at : reminder.snoozed_until || reminder.due_at;
  const label = `${reminder.company} follow-up`;
  const actions = done
    ? `<button class="text-button interactive" type="button" data-reminder-action="reopen" data-reminder-id="${reminder.id}">Reopen</button>`
    : `<button class="text-button interactive" type="button" data-reminder-snooze="${reminder.id}" aria-haspopup="menu" aria-expanded="false">Snooze</button><button class="tonal-button interactive" type="button" data-reminder-action="done" data-reminder-id="${reminder.id}">Done</button>`;
  return `<article class="reminder-row" data-reminder-id="${reminder.id}"><div><strong>${escapeHtml(reminder.company)}</strong><span>${roleLinkMarkup(reminder.dedupe_key, reminder.title)}</span>${reminder.note ? `<span class="reminder-note">${escapeHtml(reminder.note)}</span>` : ""}</div><time datetime="${escapeHtml(when)}" title="${escapeHtml(formatAbsolute(when))}">${done ? `Done ${escapeHtml(formatDate(when).replace(/^(Today|Yesterday)$/, (word) => word.toLowerCase()))}` : escapeHtml(reminderDue(reminder) ? formatDate(when) : upcomingDay(when))}</time><div>${actions}${moreButtonMarkup(label)}</div></article>`;
}

function reminderMarkup() {
  const now = new Date();
  const open = state.trackerData?.reminders || [];
  const due = open.filter((reminder) => reminderDue(reminder, now));
  const upcoming = open.filter((reminder) => !reminderDue(reminder, now));
  const done = state.trackerData?.done_reminders || [];
  if (!open.length && !done.length) return "";
  const format = new Intl.NumberFormat();
  const intro = due.length ? `${due.length} ${due.length === 1 ? "application needs" : "applications need"} a next step.` : "Nothing due right now.";
  const upcomingMarkup = upcoming.length ? `<details class="reminder-more" data-reminder-group="upcoming" ${state.upcomingFollowupsOpen ? "open" : ""}><summary>Coming up (${format.format(upcoming.length)})</summary><div class="reminder-list">${upcoming.map(reminderRowMarkup).join("")}</div></details>` : "";
  const doneMarkup = done.length ? `<details class="reminder-more" data-reminder-group="done" ${state.doneFollowupsOpen ? "open" : ""}><summary>Done in the last 2 weeks (${format.format(done.length)})</summary><div class="reminder-list">${done.map(reminderRowMarkup).join("")}</div></details>` : "";
  return `<section class="tracker-section" aria-labelledby="followups-title"><div class="section-heading"><div><h2 id="followups-title" tabindex="-1">${due.length ? "Follow-ups due" : "Follow-ups"}</h2><p>${intro}</p></div></div>${due.length ? `<div class="reminder-list">${due.map(reminderRowMarkup).join("")}</div>` : ""}${upcomingMarkup}${doneMarkup}</section>`;
}

function interviewMarkup(item) {
  const cancelled = Boolean(item.cancelled_at);
  return `<article class="${cancelled ? "is-cancelled" : ""}" data-interview-id="${item.id}"><time datetime="${escapeHtml(item.starts_at)}">${escapeHtml(formatAbsolute(item.starts_at))}</time><div><strong>${escapeHtml(item.company)}</strong><span>${roleLinkMarkup(item.dedupe_key, item.title)}${item.location ? ` · ${escapeHtml(item.location)}` : ""}</span>${cancelled ? '<span class="interview-status">Cancelled</span>' : ""}</div><div class="row-actions">${cancelled ? "" : `<a class="text-button interactive" href="/api/v1/interviews.ics?id=${item.id}">.ics</a>`}${moreButtonMarkup(`${item.company} interview`)}</div></article>`;
}

function interviewsMarkup(applications) {
  const { upcoming, past, scheduled } = splitInterviews(state.trackerData?.interviews || []);
  const options = applications.map((job) => `<option value="${escapeHtml(job.dedupe_key)}">${escapeHtml(job.company)}, ${escapeHtml(job.title)}</option>`).join("");
  const pastMarkup = past.length ? `<details class="interview-past" ${state.pastInterviewsOpen ? "open" : ""}><summary>Past interviews (${new Intl.NumberFormat().format(past.length)})</summary><div class="interview-list">${past.map(interviewMarkup).join("")}</div></details>` : "";
  return `<section class="tracker-section" aria-labelledby="interviews-title"><div class="section-heading"><div><h2 id="interviews-title" tabindex="-1">Interviews</h2><p>${scheduled ? `${scheduled} upcoming.` : "Keep interview details with the application."}</p></div>${scheduled ? '<a class="outlined-button interactive" href="/api/v1/interviews.ics">Export .ics</a>' : ""}</div>${upcoming.length ? `<div class="interview-list">${upcoming.map(interviewMarkup).join("")}</div>` : ""}${pastMarkup}<details class="interview-add"><summary>Add interview</summary><form id="interview-form"><label>Application<select name="dedupe_key" required><option value="">Choose application</option>${options}</select></label><label>Starts<input name="starts_at" type="datetime-local" required></label><label>Ends <span>(optional)</span><input name="ends_at" type="datetime-local"></label><label>Location <span>(optional)</span><input name="location" type="text" maxlength="500" autocomplete="off"></label><label>Notes <span>(optional)</span><textarea name="notes" rows="3" maxlength="20000"></textarea></label><button class="outlined-button interactive" type="submit">Save interview</button></form></details></section>`;
}

function trackerTableMarkup(applications) {
  if (!applications.length) return '<div class="empty-state"><h2>No applications match.</h2><p>Change the Tracker filters or apply to a role from Queue.</p><a class="tonal-button interactive" href="/queue" data-route>Open queue</a></div>';
  return `<div class="tracker-table-wrap"><table class="tracker-table"><thead><tr><th class="select-cell"><label class="row-select"><input type="checkbox" data-row-select-all="tracker" aria-label="Select all applications in this view"></label></th><th><button type="button" data-tracker-sort="company">Company</button></th><th>Role</th><th><button type="button" data-tracker-sort="status">Status</button></th><th><button type="button" data-tracker-sort="applied">Applied</button></th><th><button type="button" data-tracker-sort="last_activity">Last activity</button></th><th>Next step</th><th>Resume</th><th>Source</th><th><span class="visually-hidden">Actions</span></th></tr></thead><tbody>${applications.map((job) => `<tr data-tracker-key="${escapeHtml(job.dedupe_key)}"><td class="select-cell">${rowSelectMarkup("tracker", job)}</td><td class="company-cell"><a href="/companies/${encodeURIComponent(job.company)}" data-route>${escapeHtml(job.company)}</a>${job.connections_count ? `<span class="connections-count">${countLabel(job.connections_count, "connection")}</span>` : ""}</td><td data-label="Role">${roleLinkMarkup(job.dedupe_key, job.title)}</td><td data-label="Status"><div class="tracker-status-cell">${statusMarkup(job.status)}${statusSelectMarkup(job, "data-tracker-status-select")}</div></td><td data-label="Applied">${escapeHtml(job.applied_at ? formatDate(job.applied_at) : "")}</td><td data-label="Last activity">${escapeHtml(job.application_updated_at ? formatDate(job.application_updated_at) : "")}</td><td data-label="Next step"><input class="next-step-input" type="text" maxlength="1000" value="${escapeHtml(job.next_step || "")}" placeholder="Add next step" aria-label="Next step for ${escapeHtml(job.title)}" data-next-step="${escapeHtml(job.dedupe_key)}"></td><td data-label="Resume">${escapeHtml(job.resume_name || "")}</td><td data-label="Source">${escapeHtml(sourceList(job).join(", "))}</td><td class="row-menu-cell">${moreButtonMarkup(job.title)}</td></tr>`).join("")}</tbody></table></div>`;
}

function trackerBoardMarkup(applications) {
  return `<div class="tracker-board" aria-label="Applications by status">${boardStatuses.map((status) => {
    const jobs = applications.filter((job) => job.status === status);
    return `<section class="board-column" data-board-status="${status}"><header><h2>${escapeHtml(statusLabel(status))}</h2><span>${jobs.length}</span></header><div>${jobs.map((job) => `<article class="board-card" data-tracker-key="${escapeHtml(job.dedupe_key)}">${rowSelectMarkup("tracker", job)}<button class="board-drag-handle drag-handle interactive" type="button" data-drag-handle aria-label="Reorder ${escapeHtml(job.title)}" title="Reorder ${escapeHtml(job.title)}">${icons.queue}</button><a href="/companies/${encodeURIComponent(job.company)}" data-route>${escapeHtml(job.company)}</a><strong>${roleLinkMarkup(job.dedupe_key, job.title)}</strong>${job.connections_count ? `<span>${countLabel(job.connections_count, "connection")}</span>` : ""}${statusSelectMarkup(job, "data-board-status-select")}${moreButtonMarkup(job.title)}</article>`).join("")}</div></section>`;
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
  }).join("")}</div><div class="calibration-table" role="region" aria-label="Scores and outcomes" tabindex="0"><table><thead><tr><th>Score</th><th>Company</th><th>Role</th><th>Outcome</th></tr></thead><tbody>${applications.filter((job) => job.score != null).sort((a, b) => b.score - a.score).map((job) => `<tr><td>${new Intl.NumberFormat().format(job.score)}</td><td>${escapeHtml(job.company)}</td><td>${escapeHtml(job.title)}</td><td>${statusMarkup(job.status)}</td></tr>`).join("")}</tbody></table></div></section>`;
}

const trackerSorts = [["last_activity", "Last activity"], ["applied", "Date applied"], ["company", "Company"], ["status", "Status"], ["manual", "Manual"]];

function trackerMarkup() {
  const all = state.trackerData?.applications || [];
  const applications = trackerApplications();
  const statuses = ["all", "applying", "applied", "interviewing", "offer", "rejected"];
  return `<section class="tracker-page" aria-labelledby="tracker-title"><header class="tracker-heading"><div><h1 id="tracker-title" tabindex="-1">Application tracker</h1><p>Keep the next action visible, then learn from the outcome.</p></div><div class="view-switch" aria-label="Tracker view">${[["table", "Table"], ["board", "Board"], ["calibration", "Calibration"]].map(([value, label]) => `<button class="interactive" type="button" data-tracker-view="${value}" aria-pressed="${state.trackerView === value}">${label}</button>`).join("")}</div></header>${trackerInsights(all)}${reminderMarkup()}${interviewsMarkup(all)}<section class="tracker-section applications-section" aria-labelledby="applications-title"><div class="section-heading"><div><h2 id="applications-title">Applications</h2><p>${applications.length} ${applications.length === 1 ? "application" : "applications"} in this view.</p></div><button class="outlined-button interactive" type="button" data-export-tracker>Export CSV</button></div><div class="tracker-tools"><label>${icons.search}<input id="tracker-search" type="search" placeholder="Search applications" value="${escapeHtml(state.trackerQuery)}" aria-label="Search applications"></label><label><span class="visually-hidden">Filter by status</span><select id="tracker-status">${statuses.map((status) => `<option value="${status}" ${state.trackerStatus === status ? "selected" : ""}>${status === "all" ? "All statuses" : statusLabel(status)}</option>`).join("")}</select></label><label><span class="visually-hidden">Sort applications</span><select id="tracker-sort">${trackerSorts.map(([value, label]) => `<option value="${value}" ${state.trackerSort === value ? "selected" : ""}>${label}</option>`).join("")}</select></label></div>${state.trackerView === "board" ? trackerBoardMarkup(applications) : state.trackerView === "calibration" ? calibrationMarkup(applications) : trackerTableMarkup(applications)}</section></section>`;
}

function renderTracker({ focus = false } = {}) {
  document.title = "Tracker · JobSeer";
  parseTrackerUrl();
  if (state.trackerError) routeView.innerHTML = `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load the tracker.</h1><p>${escapeHtml(state.trackerError)}</p><button class="tonal-button interactive" type="button" data-retry-tracker>Retry</button></div></section>`;
  else if (!state.trackerData) routeView.innerHTML = state.trackerShowLoader ? '<section class="page-shell"><div class="tracker-skeleton skeleton"></div></section>' : "";
  else {
    // status saves rerender the board
    const held = document.activeElement?.closest?.(".board-card[data-tracker-key]") && document.activeElement.matches(".board-drag-handle") ? document.activeElement.closest(".board-card").dataset.trackerKey : "";
    const box = heldRowSelect();
    routeView.innerHTML = trackerMarkup();
    if (held && !focus) document.querySelector(`.board-card[data-tracker-key="${CSS.escape(held)}"] .board-drag-handle`)?.focus({ preventScroll: true });
    syncRowSelection("tracker");
    if (box && !focus) focusRowSelect("tracker", box);
  }
  if (focus) document.querySelector("#tracker-title, .error-state h1")?.focus({ preventScroll: true });
}

const trackerLoads = createLoadGate();

// older reads miss this write
function trackerWriteLanded() {
  if (trackerLoads.invalidate()) loadTrackerData({ background: true });
}

// newest fetch wins, polls never skip
async function loadTrackerData({ background = false } = {}) {
  const generation = trackerLoads.start();
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
    const data = await response.json();
    if (!trackerLoads.current(generation)) return;
    state.trackerData = data;
    overlayUnsavedOrders();
  } catch (error) {
    if (!trackerLoads.current(generation)) return;
    const message = error instanceof Error ? error.message : "The tracker did not respond.";
    if (!state.trackerData) state.trackerError = message;
    else if (Date.now() - lastNetworkNoticeAt > 60000) {
      showSnackbar(`${message} Keeping the last loaded tracker; retrying shortly.`);
      lastNetworkNoticeAt = Date.now();
    }
  } finally {
    clearTimeout(timer);
    trackerLoads.finish(generation);
  }
  if (trackerLoads.current(generation)) {
    state.trackerLoading = false; state.trackerShowLoader = false;
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
    const date = new Date(item.starts_at); return !item.cancelled_at && date >= now && date <= week;
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

function parseCompaniesUrl() {
  const params = new URLSearchParams(window.location.search);
  state.companyQuery = params.get("q") || "";
  state.companyShow = companyFilter(params.get("show"));
}

function syncCompaniesUrl() {
  const params = new URLSearchParams();
  if (state.companyQuery) params.set("q", state.companyQuery);
  if (state.companyShow !== "all") params.set("show", state.companyShow);
  history.replaceState(history.state, "", `/companies${params.size ? `?${params}` : ""}`);
}

function companyCardMarkup(company) {
  const postings = company.postings.length;
  const applications = company.applications.length;
  return `<article class="company-card${company.hidden ? " is-hidden" : ""}" data-company-key="${escapeHtml(company.key)}">${companyLogoMarkup(company.name, "company-monogram")}<div><h2><a class="company-card-link" href="/companies/${encodeURIComponent(company.name)}" data-route>${escapeHtml(company.name)}</a></h2><p>${postings} ${postings === 1 ? "posting" : "postings"} · ${applications} ${applications === 1 ? "application" : "applications"}</p>${company.connections_count ? `<span>${countLabel(company.connections_count, "connection")}</span>` : ""}${company.hidden ? "<span>Hidden from Companies</span>" : ""}</div>${moreButtonMarkup(company.name)}</article>`;
}

function companyResultsMarkup(all) {
  const companies = filterCompanies(all, { query: state.companyQuery, show: state.companyShow });
  if (companies.length) return `<div class="company-grid">${companies.map(companyCardMarkup).join("")}</div>`;
  const query = state.companyQuery.trim();
  const shown = COMPANY_FILTERS.find(([value]) => value === state.companyShow)[1];
  const title = query ? `No results for “${escapeHtml(query)}”` : state.companyShow === "hidden" ? "No hidden companies." : `No companies match “${escapeHtml(shown)}”.`;
  return `<div class="empty-state"><h2>${title}</h2><p>${query && state.companyShow !== "all" ? `Only ${escapeHtml(shown.toLowerCase())} are searched.` : "Try another name or filter."}</p><button class="tonal-button interactive" type="button" data-clear-company-filters>Clear filters</button></div>`;
}

function companyListMarkup(companies) {
  const add = '<button class="tonal-button interactive" type="button" data-add-company>Add company</button>';
  if (!companies.length) return `<div class="empty-state"><h2>No companies yet.</h2><p>Companies appear after the scout finds a posting, or add one yourself.</p>${add}</div>`;
  const visible = filterCompanies(companies, { query: state.companyQuery, show: state.companyShow }).length;
  return `<section class="companies-page" aria-labelledby="companies-title"><header class="companies-heading"><div><h1 id="companies-title" tabindex="-1">Companies</h1><p>Roles, applications, contacts, and context in one place.</p></div><div class="companies-actions">${add}<label class="linkedin-import interactive"><span>Import LinkedIn CSV</span><input id="linkedin-csv" type="file" accept=".csv,text/csv"><small>Only connections matching these companies are saved.</small></label></div></header><div class="tracker-tools company-tools"><label>${icons.search}<input id="company-search" type="search" placeholder="Search companies" value="${escapeHtml(state.companyQuery)}" aria-label="Search companies" aria-describedby="company-count" autocomplete="off"></label><label><span class="visually-hidden">Show companies</span><select id="company-show">${COMPANY_FILTERS.map(([value, label]) => `<option value="${value}" ${state.companyShow === value ? "selected" : ""}>${label}</option>`).join("")}</select></label></div><p class="company-count" id="company-count" aria-live="polite">${companyCountText(visible)}</p><div id="company-results">${companyResultsMarkup(companies)}</div></section>`;
}

function companyCountText(count) {
  return `${new Intl.NumberFormat().format(count)} ${count === 1 ? "company" : "companies"}`;
}

// typing keeps the search focused
function renderCompanyResults() {
  const all = state.companiesData?.companies;
  const results = document.querySelector("#company-results");
  if (!all || !results) return;
  results.innerHTML = companyResultsMarkup(all);
  document.querySelector("#company-count").textContent = companyCountText(filterCompanies(all, { query: state.companyQuery, show: state.companyShow }).length);
}

function contactMarkup(contact) {
  const detail = [contact.title, contact.source === "linkedin_csv" ? "LinkedIn connection" : "Manual contact"].filter(Boolean).join(" · ");
  // stored urls predate the check
  const linkedin = safeHttpsUrl(contact.linkedin_url);
  return `<article class="contact-row" data-contact-id="${contact.id}"><div><strong>${escapeHtml(contact.name)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}${contact.email ? `<a href="mailto:${encodeURIComponent(contact.email)}">${escapeHtml(contact.email)}</a>` : ""}</div><div class="row-actions">${linkedin ? `<a class="text-button interactive" href="${escapeHtml(linkedin)}" target="_blank" rel="noopener noreferrer">LinkedIn ${icons.external}</a>` : ""}${moreButtonMarkup(contact.name)}</div></article>`;
}

function companyLinkMarkup(link) {
  const href = linkHref(link.url);
  const label = linkLabel(link);
  const body = href
    ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer"><span>${escapeHtml(label)}</span>${icons.external}</a>`
    : `<span class="company-link-plain">${escapeHtml(label)}<small>Not linked because it isn't https</small></span>`;
  return `<article class="company-link-row" data-link-id="${link.id}">${body}${moreButtonMarkup(label)}</article>`;
}

function companyDetailMarkup(company) {
  const account = company.account;
  const accountMarkup = company.account_protected
    ? '<p class="protected-copy">ATS account details stay unavailable until protected profile access is enabled.</p>'
    : account ? `<dl class="company-facts">${factItem("Account", account.account_exists === true ? "Exists" : account.account_exists === false ? "Does not exist" : "Unknown")}${factItem("Sign-in email", account.sign_in_email)}${account.password_manager_url ? `<div><dt>Password manager</dt><dd><a href="${escapeHtml(account.password_manager_url)}" target="_blank" rel="noopener noreferrer">Open entry ${icons.external}</a></dd></div>` : ""}</dl>` : '<p class="muted-copy">No ATS account status recorded.</p>';
  const aliases = company.aliases?.length ? `<p>Also matches jobs posted as ${escapeHtml(new Intl.ListFormat("en-US", { type: "conjunction" }).format(company.aliases))}.</p>` : "";
  const hidden = company.hidden ? '<p class="company-warning company-hidden-note"><span aria-hidden="true">!</span><span>Hidden from the Companies index. Its jobs still show everywhere else.</span><button class="text-button interactive" type="button" data-restore-company>Show in Companies</button></p>' : "";
  return `<article class="company-detail" aria-labelledby="company-title"><header class="company-detail-heading" data-company-key="${escapeHtml(company.key)}"><div><a href="/companies" data-route>Companies</a><div class="company-title-row">${companyLogoMarkup(company.name, "company-monogram")}<h1 id="company-title" tabindex="-1">${escapeHtml(company.name)}</h1>${moreButtonMarkup(company.name)}</div><p>${countLabel(company.postings.length, "posting")} · ${countLabel(company.applications.length, "application")}${company.connections_count ? ` · ${countLabel(company.connections_count, "connection")}` : ""}</p>${aliases}</div></header>${hidden}<div class="company-detail-grid"><main>
    <section class="company-section" aria-labelledby="company-applications-title"><h2 id="company-applications-title">Applications</h2>${company.applications.length ? `<div class="company-application-list">${company.applications.map((job) => `<article><div><strong>${roleLinkMarkup(job.dedupe_key, job.title)}</strong><span>${job.applied_at ? `Applied ${escapeHtml(formatDate(job.applied_at))}` : escapeHtml(formatDate(job.application_updated_at))}</span></div>${statusMarkup(job.status)}</article>`).join("")}</div>` : '<p class="muted-copy">No applications at this company yet.</p>'}</section>
    <section class="company-section" aria-labelledby="company-postings-title"><h2 id="company-postings-title">Postings</h2>${company.postings.length ? "" : '<p class="muted-copy">No postings yet. They show up here once the scout finds one.</p>'}<div class="company-posting-list">${company.postings.map((job) => `<article><div><strong>${roleLinkMarkup(job.dedupe_key, job.title)}</strong><span>${escapeHtml(job.location || "Location not listed")}</span></div><div>${statusMarkup(job.status)}${job.url ? `<a class="icon-button interactive" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(job.title)} posting">${icons.external}</a>` : ""}</div></article>`).join("")}</div></section>
    <section class="company-section" aria-labelledby="company-contacts-title"><div class="section-heading"><div><h2 id="company-contacts-title" tabindex="-1">Contacts</h2><p>${company.contacts.length ? `${company.contacts.length} people connected to this company.` : "Keep useful people with the company."}</p></div></div>${company.contacts.length ? `<div class="contact-list">${company.contacts.map(contactMarkup).join("")}</div>` : ""}<details class="contact-add"><summary>Add contact</summary><form id="contact-form"><input type="hidden" name="company" value="${escapeHtml(company.name)}"><label>Name<input name="name" type="text" required maxlength="500" autocomplete="name"></label><label>Role <span>(optional)</span><input name="title" type="text" maxlength="500" autocomplete="organization-title"></label><label>Email <span>(optional)</span><input name="email" type="email" maxlength="500" autocomplete="email"></label><label>LinkedIn URL <span>(optional)</span><input name="linkedin_url" type="url" maxlength="2000" inputmode="url" placeholder="https://"></label><button class="outlined-button interactive" type="submit">Save contact</button></form></details></section>
  </main><aside>
    <section class="company-section"><h2>Notes</h2><label class="company-notes"><span class="visually-hidden">Notes about ${escapeHtml(company.name)}</span><textarea id="company-note" rows="8" maxlength="20000" placeholder="Interview context, referrals, or follow-up notes">${escapeHtml(company.note?.body || "")}</textarea><small id="company-note-state">Saved automatically</small></label></section>
    <section class="company-section"><h2>ATS account</h2>${accountMarkup}</section>
    <section class="company-section" aria-labelledby="company-links-title"><div class="section-heading"><h2 id="company-links-title" tabindex="-1">Links</h2><button class="text-button interactive" type="button" data-add-company-link>Add link</button></div>${(company.saved_links || []).length ? `<div class="company-links saved-links">${company.saved_links.map(companyLinkMarkup).join("")}</div>` : ""}${company.links.length ? `<h3 class="company-links-heading">From postings</h3><div class="company-links">${company.links.slice(0, 12).map((link) => linkHref(link.url) ? `<a href="${escapeHtml(linkHref(link.url))}" target="_blank" rel="noopener noreferrer"><span>${escapeHtml(link.label)}</span>${icons.external}</a>` : `<span class="company-link-plain">${escapeHtml(link.label)}</span>`).join("")}</div>` : ""}${!(company.saved_links || []).length && !company.links.length ? '<p class="muted-copy">No links yet.</p>' : ""}</section>
  </aside></div></article>`;
}

// rerenders must keep focus
function companyFocusPath() {
  const active = document.activeElement;
  if (!active || active === document.body || !routeView.contains(active)) return null;
  // checkboxes have no selection
  const selection = active.matches("textarea, input:not([type=checkbox])") ? [active.selectionStart, active.selectionEnd] : null;
  if (active.id) return { selector: `#${CSS.escape(active.id)}`, selection };
  const holder = active.closest("[data-company-key], [data-link-id], [data-contact-id], [data-rule-id]");
  const attr = holder && ["data-company-key", "data-link-id", "data-contact-id", "data-rule-id"].find((name) => holder.hasAttribute(name));
  if (!attr) return null;
  const role = active.matches("[data-menu-trigger]") ? " [data-menu-trigger]" : active.matches("a") ? " a" : active.matches("[data-rule-toggle]") ? " [data-rule-toggle]" : "";
  return { selector: `[${attr}="${CSS.escape(holder.getAttribute(attr))}"]${role}`, selection };
}

function restoreCompanyFocus(kept) {
  const node = kept && document.querySelector(kept.selector);
  if (!node) return;
  node.focus({ preventScroll: true });
  if (kept.selection && node.setSelectionRange) node.setSelectionRange(...kept.selection);
}

function renderCompanies({ focus = false } = {}) {
  document.title = `${state.companiesData?.company?.name || companyNameFromRoute() || "Companies"} · JobSeer`;
  if (!companyNameFromRoute()) parseCompaniesUrl();
  const kept = focus ? null : companyFocusPath();
  if (state.companiesError) routeView.innerHTML = `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load companies.</h1><p>${escapeHtml(state.companiesError)}</p><button class="tonal-button interactive" type="button" data-retry-companies>Retry</button></div></section>`;
  else if (!state.companiesData) routeView.innerHTML = state.companiesShowLoader ? '<section class="page-shell"><div class="companies-skeleton skeleton"></div></section>' : "";
  else if (state.companiesData.company === null) routeView.innerHTML = '<section class="page-shell"><div class="empty-state"><h1>Company not found.</h1><p>This company is not in the current posting history.</p><a class="tonal-button interactive" href="/companies" data-route>All companies</a></div></section>';
  else if (state.companiesData.company) routeView.innerHTML = companyDetailMarkup(state.companiesData.company);
  else routeView.innerHTML = companyListMarkup(state.companiesData.companies || []);
  if (focus) document.querySelector("#companies-title, #company-title, .empty-state h1")?.focus({ preventScroll: true });
  else restoreCompanyFocus(kept);
}

function ruleDescription(rule) {
  if (rule.kind === "archive_title") return `Archive new titles containing “${rule.pattern}”`;
  if (rule.kind === "tag_title") return `Tag titles containing “${rule.pattern}” as “${rule.value}”`;
  return `Add ${rule.value} ranking points to companies containing “${rule.pattern}”`;
}

function ruleCardMarkup(rule) {
  return `<article class="rule-card" data-rule-id="${rule.id}"><div><h3>${escapeHtml(rule.name)}</h3><p>${escapeHtml(ruleDescription(rule))}</p></div><div class="rule-actions"><label class="rule-toggle"><input type="checkbox" data-rule-toggle="${rule.id}" ${rule.enabled ? "checked" : ""}><span>${rule.enabled ? '<span aria-hidden="true">✓</span>' : ""}${rule.enabled ? "Enabled" : "Paused"}</span></label>${moreButtonMarkup(rule.name)}</div></article>`;
}

function profileMarkup() {
  if (state.rulesError) return `<section class="page-shell"><div class="empty-state error-state"><h1>Couldn't load rules.</h1><p>${escapeHtml(state.rulesError)}</p><button class="tonal-button interactive" type="button" data-retry-rules>Retry</button></div></section>`;
  const rules = state.rules || [];
  return `<section class="profile-page" aria-labelledby="profile-title"><header class="profile-heading"><div><h1 id="profile-title" tabindex="-1">Profile</h1><p>Keep reusable facts and quiet automation in one place.</p></div>${state.quickFillEnabled ? '<button class="tonal-button interactive" type="button" data-quick-fill-toggle>Open Quick-fill</button>' : ""}</header><section class="profile-section" aria-labelledby="rules-title"><div class="section-heading"><div><h2 id="rules-title" tabindex="-1">Inbox rules</h2><p>Rules run top to bottom, and the first archive rule that matches a job claims it. Matches are literal and ignore case. Pausing, editing or deleting a rule does not undo what it already did.</p></div></div><form class="rule-form" id="rule-form"><label><span>Name</span><input name="name" maxlength="120" required placeholder="Archive senior roles"></label><label><span>Action</span><select name="kind"><option value="archive_title">Archive title</option><option value="tag_title">Tag title</option><option value="boost_company">Boost company</option></select></label><label><span>Contains</span><input name="pattern" maxlength="120" required placeholder="Senior"></label><label><span>Tag or boost</span><input name="value" maxlength="40" placeholder="Systems or 25"><small>Leave empty for archive rules.</small></label><button class="filled-button interactive" type="submit">Add rule</button></form>${rules.length ? `<div class="rule-list">${rules.map(ruleCardMarkup).join("")}</div>` : '<div class="profile-empty"><h3>No rules yet.</h3><p>Add one above to archive, tag or boost matching jobs as they arrive.</p></div>'}</section>`;
}

function renderProfile({ focus = false } = {}) {
  document.title = "Profile · JobSeer";
  const kept = focus ? null : companyFocusPath();
  routeView.innerHTML = profileMarkup();
  if (focus) document.querySelector("#profile-title")?.focus({ preventScroll: true });
  else restoreCompanyFocus(kept);
}

async function loadRules() {
  if (state.rulesLoading) return;
  state.rulesLoading = true; state.rulesError = "";
  try {
    const response = await fetch("/api/v1/rules", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Rules did not respond.");
    const body = await response.json();
    state.rules = body.rules || [];
    state.rulesOrderRevision = body.order_revision ?? 0;
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
    jobsWriteLanded();
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
    jobsWriteLanded();
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
    jobsWriteLanded();
    const job = state.jobs.find((item) => item.rule_action_id === Number(id));
    if (job) Object.assign(job, { status: "new", archived_by_rule: null, rule_action_id: null });
    renderInbox({ focus: true }); showSnackbar("Rule archive undone.");
  } catch (error) { showSnackbar(error instanceof Error ? error.message : "The rule action could not be undone."); }
}

// --- rules ---

function ruleFocus(id) {
  return recordFocus(`.rule-card[data-rule-id="${id}"]`, "#rules-title");
}

function placeRule(rule) {
  const list = state.rules || [];
  const index = list.findIndex((item) => item.id === rule.id);
  if (index >= 0) list[index] = rule;
  if (routeRoot() === "profile") renderProfile();
}

function editRule(rule) {
  openEditor({
    title: `Edit ${rule.name}`, intro: "Changes apply from now on. Jobs it already archived stay archived.",
    focusBack: ruleFocus(rule.id),
    fields: [
      { name: "name", label: "Name", value: rule.name, required: true, maxlength: 120 },
      { name: "kind", label: "Action", value: rule.kind, options: RULE_KINDS },
      { name: "pattern", label: "Contains", value: rule.pattern, required: true, maxlength: 120 },
      { name: "value", label: "Tag or points", value: rule.value, optional: true, maxlength: 40, help: ruleValueHelp(rule.kind) },
    ],
    save: (changes) => saveRuleEdit(rule.id, changes, { message: "Rule updated.", undone: "Rule edit undone." }),
  });
}

// undo sends the old rule back
async function saveRuleEdit(id, payload, { message = "", undone = "", offer = true, aliasFrom = null } = {}) {
  const { status, body, error } = await companyWrite(`/api/v1/rules/${id}`, { method: "PATCH", body: JSON.stringify(payload) }, "Couldn't save the rule. Retry shortly.");
  if (status === 404) return "This rule was deleted in another tab.";
  // show what the other tab saved
  if (status === 409 && body.rule) { placeRule(body.rule); return "It changed in another tab, so it now shows that version."; }
  if (error) return error;
  placeRule(body.rule);
  jobsWriteLanded();
  if (aliasFrom !== null) revisionAliases.set(`rule:${id}@${aliasFrom}`, body.rule.revision);
  if (offer) {
    // a kind change clears the value
    const restore = pickFields(body.previous, ["name", "kind", "pattern", "value"]);
    const after = body.rule.revision;
    offerUndoSnackbar(message, [`rule:${id}`], async () => {
      restore.expected_revision = currentRevision(`rule:${id}`, after);
      const failed = await saveRuleEdit(id, restore, { offer: false, aliasFrom: body.previous.revision });
      showSnackbar(failed ? `Couldn't undo. ${failed}` : undone);
    }, { latest: `rule:${id}` });
  }
  return "";
}

async function saveRuleOrder(order, before, rule, { offer = true } = {}) {
  const byId = new Map((state.rules || []).map((item) => [item.id, item]));
  const position = order.indexOf(rule.id) + 1;
  state.rules = order.map((id) => byId.get(id)).filter(Boolean);
  renderProfile();
  ruleFocus(rule.id)();
  announce(`${rule.name} moved to position ${position} of ${order.length}.`);
  // a stale tab gets a 409
  const { status, body, error } = await companyWrite("/api/v1/rules/order", { method: "PUT", body: JSON.stringify({ ids: order, expected_revision: state.rulesOrderRevision }) }, "Couldn't reorder the rules.");
  if (error) {
    const text = status === 409 ? "The rules changed in another tab, so they've reloaded." : `${error} Change undone.`;
    state.rules = before.map((id) => byId.get(id)).filter(Boolean);
    if (status === 409) { state.rules = null; loadRules(); } else { renderProfile(); ruleFocus(rule.id)(); }
    assertiveRegion.textContent = text;
    showSnackbar(text);
    return false;
  }
  state.rulesOrderRevision = body.order_revision;
  jobsWriteLanded();
  if (offer) offerUndoSnackbar(`Moved ${rule.name} to position ${position}.`, ["rules-order"], async () => {
    if (await saveRuleOrder(before, order, rule, { offer: false })) showSnackbar("Move undone.");
  }, { latest: "rules-order" });
  return true;
}

function moveRule(c, to) {
  const before = (state.rules || []).map((item) => item.id);
  const order = moveId(before, c.rule.id, to);
  if (order.join() !== before.join()) saveRuleOrder(order, before, c.rule);
}

async function deleteRule(rule) {
  const list = state.rules || [];
  const index = list.findIndex((item) => item.id === rule.id);
  if (index < 0) return;
  // carrel: next card, else previous
  const focus = focusAfterRemoval(list.map((item) => String(item.id)), [String(rule.id)], ".rule-card", "data-rule-id", "#rule-form input[name='name']");
  list.splice(index, 1);
  renderProfile();
  focus();
  const { status, error } = await companyWrite(`/api/v1/rules/${rule.id}`, { method: "DELETE" }, "Couldn't delete the rule.");
  // another tab got there first
  if (status === 404) { showSnackbar("Already deleted in another tab."); return; }
  if (error) {
    list.splice(index, 0, rule);
    renderProfile();
    ruleFocus(rule.id)();
    assertiveRegion.textContent = `${error} Change undone.`;
    showSnackbar(`${error} Change undone.`);
    return;
  }
  jobsWriteLanded();
  offerUndoSnackbar(`${rule.name} deleted.`, [`rule:${rule.id}`, "rules-order"], () => restoreRule(rule));
}

async function restoreRule(rule) {
  const { status, error } = await companyWrite(`/api/v1/rules/${rule.id}/restore`, { method: "POST", body: "{}" }, "Couldn't restore the rule.");
  if (status === 404) { showSnackbar("Already restored in another tab."); return; }
  if (error) { showSnackbar(`Couldn't undo. ${error}`); return; }
  // its old place comes back
  state.rules = null;
  await loadRules();
  if (document.activeElement === document.body || !routeView.contains(document.activeElement)) ruleFocus(rule.id)();
  jobsWriteLanded();
  showSnackbar(`${rule.name} restored.`);
}

async function loadCompanies(name = companyNameFromRoute()) {
  // a write landed mid load
  if (state.companiesLoading) { state.companiesReload = true; return; }
  state.companiesLoading = true; state.companiesError = ""; state.companyLoadedName = name;
  const timer = setTimeout(() => { state.companiesShowLoader = true; if (routeRoot() === "companies") renderCompanies(); }, 300);
  try {
    const params = name ? `?${new URLSearchParams({ name })}` : "";
    const response = await fetch(`/api/v1/companies${params}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Companies did not respond.");
    const data = await response.json();
    // the route moved on mid load
    if (name === companyNameFromRoute()) state.companiesData = data;
    else state.companiesReload = true;
  } catch (error) {
    state.companiesError = error instanceof Error ? error.message : "Companies did not respond.";
  } finally {
    clearTimeout(timer); state.companiesLoading = false; state.companiesShowLoader = false;
    if (state.companiesReload) { state.companiesReload = false; loadCompanies(); }
    else if (routeRoot() === "companies") {
      const focus = state.companiesFocusPending;
      state.companiesFocusPending = false;
      renderCompanies({ focus });
    }
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
    const companies = new Map(state.companiesData.companies.flatMap((company) => (company.match_keys || [company.key]).map((key) => [key, company.name])));
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

// --- follow-ups ---

function findReminder(id) {
  const data = state.trackerData;
  return [...(data?.reminders || []), ...(data?.done_reminders || [])].find((item) => item.id === id) || null;
}

function reminderFocus(id) {
  return recordFocus(`.reminder-row[data-reminder-id="${id}"]`, "#followups-title, #tracker-title");
}

// closed groups hide their rows
function shownReminderIds() {
  return [...document.querySelectorAll(".reminder-row[data-reminder-id]")].filter((row) => !row.closest("details:not([open])")).map((row) => row.dataset.reminderId);
}

// keeps names the server leaves out
function placeReminder(saved) {
  const data = state.trackerData;
  if (!data) return;
  const old = findReminder(saved.id);
  const merged = { company: old?.company || trackedJob(saved.dedupe_key)?.company || "", title: old?.title || trackedJob(saved.dedupe_key)?.title || "", ...saved };
  data.reminders = (data.reminders || []).filter((item) => item.id !== saved.id);
  data.done_reminders = (data.done_reminders || []).filter((item) => item.id !== saved.id);
  if (merged.status === "done") data.done_reminders.unshift(merged);
  else data.reminders = [...data.reminders, merged].sort((left, right) => new Date(left.snoozed_until || left.due_at) - new Date(right.snoozed_until || right.due_at) || left.id - right.id);
  if (routeRoot() === "tracker") renderTracker();
}

function reminderMessage(action, reminder) {
  if (action === "done") return "Follow-up done.";
  if (action === "reopen") return "Follow-up reopened.";
  if (action === "unsnooze") return "Snooze cleared.";
  return `Snoozed until ${formatAbsolute(reminder.snoozed_until)}.`;
}

async function updateReminder(id, payload, { offer = true, aliasFrom = null } = {}) {
  const reminder = findReminder(id);
  const scope = `reminder:${id}`;
  const body = reminder ? { ...payload, expected_revision: payload.expected_revision ?? currentRevision(scope, reminder.revision) } : payload;
  const inside = document.activeElement?.closest?.(".reminder-row")?.dataset.reminderId === String(id);
  const shown = shownReminderIds();
  const { status, body: result, error } = await companyWrite(`/api/v1/reminders/${id}`, { method: "PUT", body: JSON.stringify(body) }, "The follow-up could not be updated.");
  // show what the other tab saved
  if (status === 409 && result.reminder) { placeReminder(result.reminder); showSnackbar("This follow-up changed in another tab, so it now shows that version."); return "changed"; }
  if (error) { assertiveRegion.textContent = error; showSnackbar(offer ? error : `Couldn't undo. ${error}`); return error; }
  placeReminder(result.reminder);
  trackerWriteLanded();
  if (aliasFrom !== null) revisionAliases.set(`${scope}@${aliasFrom}`, result.reminder.revision);
  // the row may change lists
  if (inside || document.activeElement === document.body) {
    if (shownReminderIds().includes(String(id))) reminderFocus(id)();
    else focusAfterRemoval(shown, [String(id)], ".reminder-row", "data-reminder-id", "#followups-title")();
  }
  if (!offer) return "";
  const after = result.reminder.revision;
  const undo = reminderUndo(payload.action, result.previous);
  offerUndoSnackbar(reminderMessage(payload.action, result.reminder), [scope], async () => {
    const failed = await updateReminder(id, { ...undo, expected_revision: currentRevision(scope, after) }, { offer: false, aliasFrom: result.previous.revision });
    if (!failed) showSnackbar("Follow-up change undone.");
  }, { latest: scope });
  return "";
}

function snoozeItems(reminder) {
  const day = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
  return [
    ...snoozeChoices().map((choice) => ({ label: choice.key === "three-days" ? "Snooze 3 days" : `Snooze until ${choice.label.toLowerCase()}`, hint: day.format(choice.until), run: () => updateReminder(reminder.id, { action: "snooze", until: choice.until.toISOString() }) })),
    { label: "Pick a date…", run: () => pickSnoozeDate(reminder) },
  ];
}

function pickSnoozeDate(reminder) {
  const tomorrow = snoozeChoices()[0].until;
  openEditor({
    title: "Snooze until", intro: `${reminder.company} comes back at 9 am that day.`,
    focusBack: reminderFocus(reminder.id), submit: "Snooze", always: true,
    fields: [{ name: "day", label: "Date", type: "date", value: dateInputValue(tomorrow), min: dateInputValue(tomorrow), required: true }],
    save(changes) {
      const picked = pickedDay(changes.day ?? dateInputValue(tomorrow));
      if (picked.error) return Promise.resolve(picked.error);
      return updateReminder(reminder.id, { action: "snooze", until: picked.until.toISOString() }).then((error) => error === "changed" ? "" : error);
    },
  });
}

function addFollowUp(key, trigger = null) {
  const job = trackedJob(key);
  if (!job) return;
  const tomorrow = snoozeChoices()[0].until;
  openEditor({
    title: "Add a follow-up", intro: `A reminder for ${job.title} at ${job.company}, apart from the one each application gets.`,
    trigger, focusBack: recordFocus(`[data-tracker-key="${CSS.escape(key)}"], #job-list-layer .job-row[data-job-key="${CSS.escape(key)}"]`, "#tracker-title, .inbox-list h1"),
    submit: "Add follow-up", always: true,
    fields: [
      { name: "day", label: "Date", type: "date", value: dateInputValue(snoozeChoices()[1].until), min: dateInputValue(tomorrow), required: true },
      { name: "note", label: "Note", value: "", optional: true, maxlength: 500, help: "What to follow up on, like sending a portfolio link." },
    ],
    async save(changes) {
      const picked = pickedDay(changes.day ?? dateInputValue(snoozeChoices()[1].until));
      if (picked.error) return picked.error;
      const { body, error } = await companyWrite("/api/v1/reminders", { method: "POST", body: JSON.stringify({ dedupe_key: key, due_at: picked.until.toISOString(), note: changes.note || "" }) }, "Couldn't add the follow-up. Retry shortly.");
      if (error) return error;
      if (state.trackerData) {
        state.upcomingFollowupsOpen = true;
        placeReminder(body.reminder);
        // carrel: added items take focus
        if (routeRoot() === "tracker") setTimeout(() => reminderFocus(body.reminder.id)(), 0);
      }
      trackerWriteLanded();
      offerUndoSnackbar(`Follow-up added for ${formatAbsolute(body.reminder.due_at)}.`, [`reminder:${body.reminder.id}`], () => removeFollowUp(body.reminder));
      return "";
    },
  });
}

async function removeFollowUp(reminder) {
  const { status, error } = await companyWrite(`/api/v1/reminders/${reminder.id}`, { method: "DELETE" }, "Couldn't remove the follow-up.");
  if (status === 404) { showSnackbar("Already gone in another tab."); return; }
  if (error) { showSnackbar(`Couldn't undo. ${error}`); return; }
  const data = state.trackerData;
  if (data) {
    data.reminders = (data.reminders || []).filter((item) => item.id !== reminder.id);
    data.done_reminders = (data.done_reminders || []).filter((item) => item.id !== reminder.id);
    if (routeRoot() === "tracker") renderTracker();
  }
  trackerWriteLanded();
  showSnackbar("Follow-up removed.");
}

function writeNextStep(key, value, expected = null, reopen = null) {
  const payload = { next_step: value, ...(expected === null ? {} : { expected_next_step: expected }), ...(reopen ? { reopen_follow_ups: reopen } : {}) };
  return companyWrite(`/api/v1/applications/${encodeURIComponent(key)}/next-step`, { method: "PUT", body: JSON.stringify(payload) }, "The next step could not be saved.");
}

function placeNextStep(key, value, { force = false } = {}) {
  const job = state.trackerData?.applications?.find((item) => item.dedupe_key === key);
  if (job) job.next_step = value;
  const input = document.querySelector(`[data-next-step="${CSS.escape(key)}"]`);
  if (input && (force || input !== document.activeElement)) input.value = value;
}

// one write per job at a time
const nextStepSaves = new Map();

function queueNextStep(key, write) {
  const run = (nextStepSaves.get(key) || Promise.resolve()).then(write);
  nextStepSaves.set(key, run);
  return run;
}

function saveTrackerNextStep(key, value, input) {
  return queueNextStep(key, async () => {
    // the model only holds committed steps
    const previous = state.trackerData?.applications?.find((item) => item.dedupe_key === key)?.next_step || "";
    if (value.trim() === previous) return;
    input?.setAttribute("aria-busy", "true");
    const { status, body, error } = await writeNextStep(key, value, previous);
    input?.removeAttribute("aria-busy");
    if (status === 409) {
      placeNextStep(key, body.next_step ?? "", { force: true });
      showSnackbar("The next step changed elsewhere, so it now shows that version.");
      return;
    }
    if (error) {
      placeNextStep(key, previous, { force: true });
      showSnackbar(`${error} Previous value restored.`);
      return;
    }
    const saved = body.next_step ?? value.trim();
    const closed = body.closed_follow_ups || [];
    placeNextStep(key, saved);
    const message = `${saved ? "Next step saved" : "Next step cleared"}${closed.length ? ", follow-up marked done" : ""}.`;
    // a stale undo gets a 409
    offerUndoSnackbar(message, [`next-step:${key}`], () => undoNextStep(key, previous, saved, closed), { latest: `next-step:${key}` });
    loadTrackerData({ background: true });
  });
}

function undoNextStep(key, before, after, closed = []) {
  return queueNextStep(key, async () => {
    // an empty list still skips closing
    const { status, body, error } = await writeNextStep(key, before, after, closed);
    if (status === 409) {
      placeNextStep(key, body.next_step ?? after, { force: true });
      showSnackbar("Couldn't undo. The next step changed elsewhere, so it now shows that version.");
      return;
    }
    if (error) { showSnackbar(`Couldn't undo. ${error}`); return; }
    placeNextStep(key, body.next_step ?? before, { force: true });
    const reopened = (body.reopened_follow_ups || []).length;
    showSnackbar(!closed.length ? "Next step change undone." : reopened === closed.length ? "Next step change undone, follow-up reopened." : "Next step change undone. The follow-up changed since, so it stays done.");
    loadTrackerData({ background: true });
  });
}

async function writeTrackerStatus(key, job, tracked, status) {
  if (status === "applied") {
    const response = await secureWrite(`/api/v1/applications/${encodeURIComponent(key)}/applied`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ document_id: null }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The status could not be saved.");
    jobsWriteLanded();
    const snapshot = (await response.json()).snapshot;
    Object.assign(job, { applied_at: snapshot.captured_at, resume_document_id: snapshot.resume_document_id, resume_name: snapshot.resume_name, queue_position: null });
  } else {
    const result = await patchJob(job, status);
    job.status = result.status;
    if (tracked) tracked.status = result.status;
    if (result.liveness) Object.assign(job, { liveness_status: result.liveness.status, liveness_evidence: result.liveness.evidence });
  }
}

async function setTrackerStatus(key, status, { offerUndo = true, fallback = null, afterSave = null, onFail = null, undo = null, undoKeys = null } = {}) {
  const tracked = state.trackerData?.applications?.find((item) => item.dedupe_key === key);
  const job = state.jobs.find((item) => item.dedupe_key === key) || tracked || fallback;
  if (!job || job.status === status || state.statusSaving.has(key)) return;
  const previous = job.status;
  state.statusSaving.add(key);
  job.status = status;
  if (tracked) tracked.status = status;
  if (routeRoot() === "tracker") renderTracker();
  if (routeRoot() === "inbox") renderSelectedJob();
  const settle = () => {
    state.statusSaving.delete(key);
    if (routeRoot() === "tracker") renderTracker();
    if (routeRoot() === "inbox") renderSelectedJob();
  };
  try {
    await writeTrackerStatus(key, job, tracked, status);
    trackerWriteLanded();
  } catch (error) {
    job.status = previous; if (tracked) tracked.status = previous;
    onFail?.();
    settle();
    showSnackbar(`${error.message} Status restored.`);
    return;
  }
  // the server keeps the new status
  const placed = afterSave ? await afterSave().then(() => true, () => false) : true;
  await loadTrackerData({ background: true });
  settle();
  const offBoard = routeRoot() === "tracker" && state.trackerView === "board" && !boardStatuses.includes(job.status);
  const moved = `Moved to ${statusLabel(job.status)}${offBoard ? ", off the board" : ""}`;
  const message = job.status === "archived" && status === "queued" ? "Posting closed; it cannot be queued." : placed ? `${moved}.` : `${moved}, but couldn't save its position.`;
  const plainUndo = () => setTrackerStatus(key, previous, { offerUndo: false, fallback: job });
  // closed postings leave both lists
  if (offerUndo) offerUndoSnackbar(message, placed ? undoKeys || [key] : [key], placed && undo ? undo : plainUndo);
  else showSnackbar(message);
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
    noticeHidden(response);
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

const companyNoteSaves = new Map();

function queueCompanyNote(key, company, body) {
  const run = (companyNoteSaves.get(key) || Promise.resolve()).then(() => saveCompanyNote(key, company, body));
  companyNoteSaves.set(key, run);
  return run;
}

function shownCompany(key) {
  const company = state.companiesData?.company;
  return company?.key === key ? company : null;
}

async function saveCompanyNote(key, company, body) {
  const status = document.querySelector("#company-note-state");
  if (status) status.textContent = "Saving…";
  const { body: result, error } = await companyWrite("/api/v1/companies/note", { method: "PUT", body: JSON.stringify({ company, body }) }, "The company note could not be saved.");
  if (error) {
    if (status) status.textContent = "Not saved";
    showSnackbar(error);
    return;
  }
  const shown = shownCompany(key);
  if (shown) shown.note = result.note;
  if (shown && document.querySelector("#company-note-state")) document.querySelector("#company-note-state").textContent = "Saved automatically";
}

async function settleCompanyNote(key, company) {
  const before = textSessions.end(`company-note:${key}`);
  if (state.companyNoteDraft?.key === key) {
    clearTimeout(state.companyNoteTimer);
    queueCompanyNote(key, company, state.companyNoteDraft.body);
    state.companyNoteDraft = null;
  }
  await companyNoteSaves.get(key);
  // unsaved typing isnt undoable
  const shown = shownCompany(key);
  const change = shown && textChange(before, shown.note?.body || "");
  if (!change) return;
  offerUndoSnackbar(change.after ? "Company note saved." : "Company note cleared.", [`company-note:${key}`], () => undoCompanyNote(key, company, change), { latest: `company-note:${key}` });
}

async function undoCompanyNote(key, company, { before, after }) {
  const { status, body, error } = await companyWrite("/api/v1/companies/note", { method: "PUT", body: JSON.stringify({ company, body: before, expected_body: after }) }, "The company note could not be saved.");
  if (status === 409 && body.note) {
    placeCompanyNote(key, body.note);
    showSnackbar("Couldn't undo. The note changed in another tab, so it now shows that version.");
    return;
  }
  if (error) { showSnackbar(`Couldn't undo. ${error}`); return; }
  placeCompanyNote(key, body.note);
  showSnackbar("Company note change undone.");
}

function placeCompanyNote(key, note) {
  const shown = shownCompany(key);
  if (!shown) return;
  shown.note = { ...shown.note, ...note };
  const field = document.querySelector("#company-note");
  if (field && field !== document.activeElement) field.value = shown.note.body || "";
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

const queueSaver = createOrderSaver({
  send: async (keys) => {
    const response = await fetch("/api/v1/queue/order", {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ dedupe_keys: keys }),
    });
    noticeHidden(response);
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The queue order could not be saved.");
  },
  onLanded: () => jobsWriteLanded(),
  // another tab may have reordered
  onRevert: (keys, error) => {
    reloadJobs();
    showSnackbar(`${error.message.replace(/\.?$/, ".")} Reloading the queue.`);
  },
});

const jobLoads = createLoadGate();

function adoptJobs(jobs) {
  state.jobs = Array.isArray(jobs) ? jobs : [];
  for (const job of state.jobs) {
    job.pay ||= state.descriptions.get(job.dedupe_key)?.value?.pay || "";
    job.pay_checked ||= Boolean(state.descriptions.get(job.dedupe_key)?.value?.description_text);
  }
  // this read predates the save
  if (!queueSaver.unsaved) return;
  const queued = new Set(state.jobs.filter((job) => job.status === "queued").map((job) => job.dedupe_key));
  queueSaver.prune((key) => queued.has(key));
  applyQueueOrder(queueSaver.unsaved);
}

async function reloadJobs() {
  const read = jobLoads.start();
  try {
    const response = await fetch("/api/v1/jobs", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The job list did not respond.");
    const payload = await response.json();
    if (!jobLoads.current(read)) return;
    adoptJobs(payload.jobs);
    refreshJobView();
  } catch (error) {
    if (jobLoads.current(read)) showSnackbar(error instanceof Error ? error.message : "The job list did not respond.");
  } finally {
    jobLoads.finish(read);
  }
}

// older reads miss this write
function jobsWriteLanded() {
  if (jobLoads.invalidate()) reloadJobs();
}

function announce(message) {
  // same text twice wouldnt be read
  reorderRegion.textContent = reorderRegion.textContent === message ? `${message}\u00a0` : message;
}

function applyQueueOrder(keys) {
  keys.forEach((key, position) => {
    const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
    if (job && job.status === "queued") job.queue_position = position;
  });
}

// rebuilding the list loses focus
function arrangeQueue(anchorKey = "") {
  const list = document.querySelector(".queue-list");
  if (!list || routeRoot() !== "queue" || window.location.pathname.startsWith("/queue/session/")) return;
  const jobs = queueJobs();
  const cards = new Map([...list.querySelectorAll(":scope > .queue-card[data-queue-key]")].map((card) => [card.dataset.queueKey, card]));
  if (cards.size !== jobs.length || jobs.some((job) => !cards.has(job.dedupe_key))) { renderQueue(); return; }
  const nodes = jobs.map((job) => cards.get(job.dedupe_key));
  const active = document.activeElement;
  flip(nodes.filter((node) => !node.classList.contains("is-lifted")), () => placeNodes(list, nodes, cards.get(anchorKey) || null));
  nodes.forEach((card, index) => {
    card.querySelector('[data-queue-move="up"]').disabled = index === 0;
    card.querySelector('[data-queue-move="down"]').disabled = index === nodes.length - 1;
  });
  if (active instanceof HTMLElement && list.contains(active) && (active !== document.activeElement || active.disabled)) {
    const card = active.closest(".queue-card");
    const other = active.matches("[data-queue-move]") ? card.querySelector(`[data-queue-move="${active.dataset.queueMove === "up" ? "down" : "up"}"]`) : null;
    (active.disabled ? (other && !other.disabled ? other : card.querySelector(".queue-drag-handle")) : active).focus({ preventScroll: true });
  }
}

function reorderQueue(key, targetIndex) {
  const keys = queueJobs().map((job) => job.dedupe_key);
  const from = keys.indexOf(key);
  const to = Math.max(0, Math.min(keys.length - 1, targetIndex));
  if (from < 0 || from === to) return;
  if (!queueSaver.busy) queueSaver.reset(keys);
  const order = moveKey(keys, key, to);
  const touched = keys.slice(Math.min(from, to), Math.max(from, to) + 1);
  applyQueueOrder(order);
  arrangeQueue(key);
  offerMoveSnackbar(moveAnnouncement(to, order.length), "queue-move", { message: "Queue move", keys: touched, run: () => undoQueueMove(keys, touched) }, queueSaver.save(order));
}

// a full restore undoes later moves
function undoQueueMove(previous, touched) {
  const current = queueJobs().map((job) => job.dedupe_key);
  const slots = current.map((key, index) => touched.includes(key) ? index : -1).filter((index) => index >= 0);
  const restored = restoreOrder(previous.filter((key) => touched.includes(key)), current.filter((key) => touched.includes(key)));
  const order = [...current];
  slots.forEach((slot, index) => { order[slot] = restored[index]; });
  if (!queueSaver.busy) queueSaver.reset(current);
  applyQueueOrder(order);
  arrangeQueue();
  queueSaver.save(order).then((error) => { if (!error) showSnackbar("Queue move undone."); }); // failures report themselves
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
    offerUndoSnackbar("Moved to Saved.", [key], async () => {
      try { const restored = await patchJob(job, previous); job.status = restored.status; refreshJobView(); showSnackbar("Back in the queue."); }
      catch (error) { showSnackbar(error.message || "Couldn't restore the queued job."); }
    });
  } catch (error) {
    job.status = previous;
    renderQueue();
    showSnackbar(`${error.message} Change undone.`);
  }
}

// --- hiding jobs ---
// the scout never shows them again

// --- activity ---

// only the timeline loses it
async function setActivityDismissed(entry, dismissed, { offer = true, order = [] } = {}) {
  const data = state.trackerData;
  if (!data) return;
  // carrel: next entry, else previous
  const rest = order.filter((id) => id !== String(entry.id));
  const next = rest[Math.min(Math.max(0, order.indexOf(String(entry.id))), rest.length - 1)];
  data.history = dismissed ? data.history.filter((item) => item.id !== entry.id)
    : [...data.history.filter((item) => item.id !== entry.id), entry].sort((left, right) => new Date(left.changed_at) - new Date(right.changed_at) || left.id - right.id);
  renderJobSurface();
  if (dismissed) (document.querySelector(`[data-dismiss-activity="${next}"]`) || document.querySelector("#activity-heading"))?.focus({ preventScroll: true });
  const { status, error } = await companyWrite(`/api/v1/activity/${entry.id}/${dismissed ? "dismiss" : "restore"}`, { method: "POST", body: "{}" }, "Couldn't change the activity entry.");
  if (status === 409) { showSnackbar(dismissed ? "Already dismissed in another tab." : "Already back in another tab."); return; }
  if (error) {
    trackerWriteLanded();
    assertiveRegion.textContent = `${error} Change undone.`;
    showSnackbar(`${error} Change undone.`);
    return;
  }
  if (offer) offerUndoSnackbar(`${statusLabel(entry.to_status)} dismissed from the timeline.`, [`activity:${entry.id}`], () => setActivityDismissed(entry, false, { offer: false }));
  else showSnackbar("Activity entry restored.");
}

// --- tags ---

function tagFocus(keys) {
  return recordFocus(`#job-list-layer .job-row[data-job-key="${CSS.escape(keys[0])}"]`, ".inbox-list h1, #tracker-title");
}

function tagMenuItems(keys) {
  const jobs = keys.map(trackedJob).filter(Boolean);
  return [
    { label: "Add tag…", run: () => addTag(keys) },
    { label: "Remove tag…", disabled: !removableTags(jobs).length, run: () => removeTag(keys) },
  ];
}

function addTag(keys, trigger = null) {
  const count = keys.length;
  const known = tagOptions(state.jobs);
  openEditor({
    title: count > 1 ? `Tag ${new Intl.NumberFormat().format(count)} jobs` : "Add tag",
    intro: "Rules never take off a tag you add here.", trigger, focusBack: tagFocus(keys), submit: "Add tag",
    fields: [{ name: "tag", label: "Tag", value: "", required: true, maxlength: 40, help: known.length ? `In use: ${known.slice(0, 8).join(", ")}` : "" }],
    save: ({ tag }) => saveTags(keys, cleanTag(tag), false),
  });
}

function removeTag(keys, trigger = null) {
  const options = removableTags(keys.map(trackedJob).filter(Boolean));
  if (!options.length) return;
  const format = new Intl.NumberFormat();
  openEditor({
    title: keys.length > 1 ? "Remove a tag from these jobs" : "Remove tag", intro: "Tags from rules stay. Edit or pause the rule to change those.",
    trigger, focusBack: tagFocus(keys), submit: "Remove tag", always: true,
    fields: [{ name: "tag", label: "Tag", value: options[0].tag, options: options.map((item) => [item.tag, keys.length > 1 ? `${item.tag} (${format.format(item.count)})` : item.tag]) }],
    save: (changes) => saveTags(keys, changes.tag ?? options[0].tag, true),
  });
}

// undo reverts only these rows
async function saveTags(keys, tag, remove) {
  if (!tag) return "Add a tag of up to 40 characters.";
  const jobs = keys.flatMap((key) => [state.jobs.find((job) => job.dedupe_key === key), state.trackerData?.applications?.find((job) => job.dedupe_key === key)]).filter(Boolean);
  const before = jobs.map((job) => [job, { manual_tags: job.manual_tags, tags: job.tags }]);
  for (const job of jobs) Object.assign(job, withManualTag(job, tag, remove));
  renderJobSurface();
  const { body, error } = await companyWrite("/api/v1/job-tags", { method: "POST", body: JSON.stringify({ dedupe_keys: keys, tag, remove }) }, "Couldn't save the tag. Retry shortly.");
  if (error) {
    for (const [job, old] of before) Object.assign(job, old);
    renderJobSurface();
    return error;
  }
  jobsWriteLanded(); trackerWriteLanded();
  const changed = body.changed;
  const count = new Intl.NumberFormat().format(changed.length);
  const many = keys.length > 1;
  if (!changed.length) { showSnackbar(remove ? `No job had “${body.tag}” any more.` : `Already tagged “${body.tag}”.`); return ""; }
  const message = remove
    ? many ? `Removed “${body.tag}” from ${count} ${changed.length === 1 ? "job" : "jobs"}.` : `Removed “${body.tag}”.`
    : many ? `Tagged ${count} ${changed.length === 1 ? "job" : "jobs"} “${body.tag}”.` : `Tagged “${body.tag}”.`;
  offerUndoSnackbar(message, changed.map((key) => `tags:${key}`), () => undoTags(body.ids, body.tag, !remove));
  return "";
}

// another tab may have retagged
async function undoTags(ids, tag, remove) {
  const { status, body, error } = await companyWrite("/api/v1/job-tags/undo", { method: "POST", body: JSON.stringify({ ids, remove }) }, "Couldn't undo the tag.");
  if (error && status !== 409) { showSnackbar(`Couldn't undo. ${error}`); return; }
  for (const key of body.changed || []) {
    for (const job of [state.jobs.find((item) => item.dedupe_key === key), state.trackerData?.applications?.find((item) => item.dedupe_key === key)]) {
      if (job) Object.assign(job, withManualTag(job, tag, remove));
    }
  }
  renderJobSurface();
  jobsWriteLanded(); trackerWriteLanded();
  if (status === 409) showSnackbar("Couldn't undo. These tags changed in another tab.");
  else showSnackbar(remove ? "Tag removed." : "Tag restored.");
}

function hideInboxJobs(keys, trigger) {
  const order = filteredJobs().map((job) => job.dedupe_key);
  hideJobs(keys, { trigger, focus: focusAfterRemoval(order, keys, "#job-list-layer .job-row", "data-job-key", "[data-clear-all-filters], .inbox-list h1") });
}

// a refetch could beat the write
function renderJobSurface() {
  if (routeRoot() === "inbox") renderInbox();
  else if (routeRoot() === "queue" && !window.location.pathname.startsWith("/queue/session/")) renderQueue();
  else if (routeRoot() === "tracker") renderTracker();
}

// carrel: next item, else previous
function focusAfterRemoval(order, keys, selector, attr, fallback) {
  const index = Math.max(0, order.findIndex((key) => keys.includes(key)));
  const rest = order.filter((key) => !keys.includes(key));
  const next = rest[Math.min(index, rest.length - 1)];
  return () => {
    const node = next && document.querySelector(`${selector}[${attr}="${CSS.escape(next)}"]`);
    const target = node ? (node.matches(".job-row") ? node : node.querySelector("button[data-menu-trigger]")) : null;
    (target || document.querySelector(fallback))?.focus({ preventScroll: true });
  };
}

function hideCopy(jobs) {
  if (jobs.length === 1) return {
    title: `Hide “${jobs[0].title}” at ${jobs[0].company}?`,
    body: "It won't come back, even if the scout finds it again on another site. Its notes and history are kept but never shown. You can undo only right after.",
  };
  return {
    title: `Hide ${new Intl.NumberFormat().format(jobs.length)} jobs?`,
    body: "They won't come back, even if the scout finds them again on another site. Their notes and history are kept but never shown. You can undo only right after.",
  };
}

async function hideJobs(keys, { trigger = null, focus = null } = {}) {
  const known = (key) => state.jobs.find((job) => job.dedupe_key === key) || state.trackerData?.applications?.find((job) => job.dedupe_key === key);
  const jobs = keys.map(known).filter(Boolean);
  if (!jobs.length || jobs.some((job) => state.pending.has(job.dedupe_key))) return;
  if (!await confirmDelete({ ...hideCopy(jobs), action: "Hide", trigger })) return;
  const hidden = new Set(jobs.map((job) => job.dedupe_key));
  const removed = { jobs: state.jobs.filter((job) => hidden.has(job.dedupe_key)), tracked: (state.trackerData?.applications || []).filter((job) => hidden.has(job.dedupe_key)) };
  const nextKey = hidden.has(state.selectedKey) ? nextKeyAfter([...hidden]) : state.selectedKey;
  const restore = (keep = hidden) => {
    for (const job of removed.jobs) if (keep.has(job.dedupe_key) && !state.jobs.includes(job)) state.jobs.push(job);
    if (state.trackerData) for (const job of removed.tracked) if (keep.has(job.dedupe_key) && !state.trackerData.applications.includes(job)) state.trackerData.applications.push(job);
  };
  state.jobs = state.jobs.filter((job) => !hidden.has(job.dedupe_key));
  if (state.trackerData) state.trackerData.applications = state.trackerData.applications.filter((job) => !hidden.has(job.dedupe_key));
  for (const key of hidden) state.selectedKeys.delete(key);
  if (routeRoot() === "inbox") { state.selectedKey = nextKey; syncInboxUrl(); }
  renderJobSurface();
  focus?.();
  let created, undoToken;
  try {
    const response = await secureWrite("/api/v1/hidden", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ dedupe_keys: [...hidden] }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't hide the job.");
    ({ hidden: created = [], undo_token: undoToken } = await response.json());
  } catch (error) {
    restore();
    renderJobSurface();
    assertiveRegion.textContent = `${error.message} Change undone.`;
    showSnackbar(`${error.message} Change undone.`);
    return;
  }
  jobsWriteLanded(); trackerWriteLanded();
  // another tab hid these first
  if (!created.length) { showSnackbar("Already hidden in another tab."); return; }
  const mine = new Set(created.map((entry) => entry.dedupe_key));
  const message = mine.size === 1 ? "Role hidden." : `${new Intl.NumberFormat().format(mine.size)} roles hidden.`;
  offerUndoSnackbar(message, [...mine], () => unhideJobs(undoToken, () => restore(mine)), { untilClosed: true });
}

async function unhideJobs(undoToken, restore) {
  try {
    const response = await secureWrite("/api/v1/hidden/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ undo_token: undoToken }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't undo.");
  } catch (error) {
    showSnackbar(`${error.message} The role stays hidden.`);
    return;
  }
  restore();
  jobsWriteLanded(); trackerWriteLanded();
  renderJobSurface();
  showSnackbar("Hide undone.");
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

function stopSessionClock() {
  clearInterval(state.sessionTimer);
  state.sessionTimer = null;
}

// same session keeps its checks
function adoptSession(row, documents = null) {
  const current = state.applySession?.id === row.id ? state.applySession : null;
  const session = Object.assign(current || { documents: [], index: 0, awaitingReturn: "", openedAt: 0, checkedKeys: new Set(), checkingKey: "", checkError: "" }, sessionFromServer(row));
  if (documents) session.documents = documents;
  state.applySession = session;
  if (session.endedAt) stopSessionClock(); else startSessionClock();
  return session;
}

function sessionScope(session) { return `session:${session.id}`; }

// whole lists need their own revision
async function writeSession(session, lists, expected) {
  const response = await secureWrite(`/api/v1/queue/session/${session.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ expected_revision: expected, ...sessionPatch(lists) }),
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 409 && body.session) {
    adoptSession(body.session);
    throw Object.assign(new Error(body.error || "The session changed in another tab."), { conflict: true });
  }
  if (!response.ok) throw new Error(body.error || "Couldn't save the session.");
  sessionLoads.invalidate(); // reads from before this lose
  return adoptSession(body.session);
}

const sessionLoads = createLoadGate();

async function startApplySession(keys = null, { at = "", replace = false } = {}) {
  if (state.sessionStarting) return;
  state.sessionStarting = true;
  if (!at) renderQueue();
  try {
    const response = await fetch("/api/v1/queue/session", {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRF-Token": state.csrf, Accept: "application/json", ...(keys ? { "Content-Type": "application/json" } : {}) },
      ...(keys ? { body: JSON.stringify({ dedupe_keys: keys }) } : {}),
    });
    noticeHidden(response);
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The session could not start.");
    const payload = await response.json();
    for (const incoming of payload.jobs || []) {
      const existing = state.jobs.find((job) => job.dedupe_key === incoming.dedupe_key);
      if (existing) Object.assign(existing, incoming);
    }
    if (!payload.session) {
      state.applySession = null;
      renderQueue();
      showSnackbar("No queued jobs are ready. Add a role from Inbox first.");
      return;
    }
    if (keys && !at) state.rowSelection.queue.keys.clear();
    const session = adoptSession(payload.session, payload.documents || []);
    session.index = routeIndex(session, at);
    state.selectedKey = session.keys[session.index];
    navigate(`/queue/session/${encodeURIComponent(state.selectedKey)}`, { replace });
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "The session could not start.");
    if (at) { state.sessionLoad = "missing"; renderApplySession(); } else renderQueue();
  } finally {
    state.sessionStarting = false;
  }
}

// a reload resumes the saved session
async function loadApplySession() {
  if (state.sessionLoad === "loading") return;
  state.sessionLoad = "loading";
  let fallback = "";
  try {
    const response = await fetch("/api/v1/queue/session", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't load the session.");
    const { session, documents } = await response.json();
    const key = sessionPathKey();
    const summary = window.location.pathname.endsWith("/summary");
    if (session && (summary || (!session.ended_at && session.keys.includes(key)))) {
      const restored = adoptSession(session, documents);
      restored.index = summary ? restored.keys.length : routeIndex(restored, key);
      if (state.jobs.find((job) => job.dedupe_key === key)?.status === "applying") restored.awaitingReturn = "prompt";
      state.sessionLoad = "";
    } else if (key && queueJobs().some((job) => job.dedupe_key === key)) fallback = key;
    else state.sessionLoad = "missing";
  } catch (error) {
    state.sessionLoad = error instanceof Error ? error.message : "Couldn't load the session.";
  }
  if (fallback) { state.sessionLoad = ""; await startApplySession(queueJobs().map((job) => job.dedupe_key), { at: fallback, replace: true }); return; }
  if (window.location.pathname.startsWith("/queue/session")) renderApplySession({ focus: true });
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
  if (recordedAs(session, job.dedupe_key) === "applied") return '<p class="session-check-state">Marked applied in this session.</p>';
  if (!session.checkedKeys.has(job.dedupe_key)) {
    return session.checkError
      ? `<div class="session-check-state" role="alert"><p>${escapeHtml(session.checkError)}</p><button class="tonal-button interactive" type="button" data-session-retry-check>Retry check</button></div>`
      : '<p class="session-check-state" role="status">Checking that this posting is still open…</p>';
  }
  if (session.awaitingReturn !== "prompt") return `<button class="filled-button interactive" type="button" data-session-apply ${!applicationUrl(job) ? "disabled" : ""}>${job.status === "applying" ? "Reopen application" : "Apply"}</button>`;
  const options = (session.documents || []).map((document) => {
    const date = document.date ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(`${document.date}T00:00:00`)) : "";
    return `<option value="${escapeHtml(document.id)}" ${document.default ? "selected" : ""}>${escapeHtml(document.name)}${date ? `, ${escapeHtml(date)}` : ""}${document.default ? " (default)" : ""}</option>`;
  }).join("");
  return `<section class="submission-prompt" aria-labelledby="submission-title"><h2 id="submission-title">Did you submit?</h2><label for="session-resume">Resume version <span>(optional)</span></label><select id="session-resume"><option value="">Not recorded</option>${options}</select><div><button class="filled-button interactive" type="button" data-session-applied>Mark applied</button><button class="tonal-button interactive" type="button" data-session-not-yet>Not yet</button><button class="text-button interactive" type="button" data-session-skip>Skip</button></div></section>`;
}

function applySessionMarkup(job) {
  const session = state.applySession;
  const progress = `${session.index + 1} of ${session.keys.length}`;
  const quickFill = state.quickFillEnabled
    ? `<aside class="session-quick-fill" id="session-quick-fill" aria-label="Quick-fill">${state.quickFillProfile ? quickFillInnerMarkup({ embedded: true }) : '<p class="session-muted">Loading Quick-fill…</p>'}</aside>`
    : `<aside class="session-review" id="session-posting-review" aria-label="Posting review"><h2>Posting review</h2>${overviewMarkup(job)}${atGlanceMarkup(job)}${descriptionMarkup(job, { compact: true })}</aside>`;
  const siteWarning = job.nonpublic_site ? `<p class="company-warning"><span aria-hidden="true">!</span>${job.public_apply_url ? "Apply opens the verified public posting." : "No public apply link verified. Find this role on the employer’s public careers site."}</p>` : "";
  const uncertain = job.liveness_status === "unknown" ? `<p class="company-warning"><span aria-hidden="true">?</span>Couldn’t confirm this posting is open: ${escapeHtml(job.liveness_evidence || "the provider did not respond")}. Check before submitting.</p>` : "";
  return `<section class="apply-session${state.quickFillEnabled ? "" : " apply-session--review"}" aria-labelledby="session-job-title"><header class="session-header"><div><strong>${escapeHtml(progress)}</strong><span id="session-elapsed">${elapsedLabel()}</span></div><div><button class="text-button interactive" type="button" data-session-previous ${session.index === 0 ? "disabled" : ""}>Previous</button>${recordedAs(session, job.dedupe_key) ? '<button class="text-button interactive" type="button" data-session-next>Next</button>' : '<button class="text-button interactive" type="button" data-session-skip>Skip</button>'}<button class="text-button interactive" type="button" data-session-end>End session</button></div></header><div class="session-columns"><main class="session-job"><div class="session-identity">${companyLogoMarkup(job.company, "detail-company-logo")}<div><p class="session-company">${escapeHtml(job.company)}</p><h1 id="session-job-title" tabindex="-1">${escapeHtml(job.title)}</h1></div></div><dl class="session-facts">${sessionLocationFact(job.location)}${factItem("Pay", queuePayValue(job))}${factItem("Term", job.terms)}${factItem("Deadline", deadlineLabel(job.deadline))}${factItem("Posted", formatDate(job.first_seen))}</dl>${siteWarning}${uncertain}<div class="session-primary-action">${sessionPromptMarkup(job)}</div><section><h2>Requirements preview</h2>${sessionRequirements(job)}</section>${state.quickFillEnabled ? overviewMarkup(job) + descriptionMarkup(job, { compact: true }) : ""}</main>${quickFill}</div></section>`;
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
  document.title = "Apply session · JobSeer";
  if (!state.loaded) {
    routeView.innerHTML = '<section class="page-shell"><div class="queue-skeleton skeleton" aria-label="Loading application session"></div></section>';
    return;
  }
  const session = state.applySession;
  if (!session) {
    if (!state.sessionLoad) loadApplySession();
    if (state.sessionLoad === "loading" || state.sessionStarting) {
      routeView.innerHTML = '<section class="page-shell"><div class="queue-skeleton skeleton" aria-label="Loading application session"></div></section>';
      return;
    }
    if (state.sessionLoad !== "missing") {
      routeView.innerHTML = `<section class="page-shell"><div class="empty-state error-state"><h1 id="page-title" tabindex="-1">Couldn't load the session.</h1><p>${escapeHtml(state.sessionLoad)}</p><button class="tonal-button interactive" type="button" data-session-reload>Retry</button></div></section>`;
      return;
    }
    routeView.innerHTML = '<section class="page-shell"><div class="empty-state"><h1 id="page-title" tabindex="-1">That session has ended.</h1><p>Start a new focused pass from your current queue.</p><a class="tonal-button interactive" href="/queue" data-route>Return to queue</a></div></section>';
    if (focus) document.querySelector("#page-title")?.focus({ preventScroll: true });
    return;
  }
  if (window.location.pathname.endsWith("/summary") || session.index >= session.keys.length || session.endedAt) {
    routeView.innerHTML = sessionSummaryMarkup();
    if (focus) document.querySelector("#session-summary-title")?.focus({ preventScroll: true });
    return;
  }
  const routeKey = sessionPathKey();
  if (session.keys.includes(routeKey)) session.index = routeIndex(session, routeKey);
  const key = session.keys[session.index];
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  if (!job) { endApplySession({ offerUndo: false }); return; }
  state.selectedKey = key;
  if (recordedAs(session, key) === "applied") session.checkedKeys.add(key);
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
    noticeHidden(response);
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't check this posting.");
    jobsWriteLanded(); // a closed posting gets archived
    const result = await response.json();
    if (state.applySession !== session || session.keys[session.index] !== key) return;
    const job = state.jobs.find((item) => item.dedupe_key === key);
    if (job) Object.assign(job, { liveness_status: result.status, liveness_evidence: result.evidence });
    if (result.status === "closed") {
      if (job) job.status = "archived";
      session.checkingKey = "";
      recordSessionStep(key, "skipped", { reason: "Posting closed", message: "Posting closed; moved to the next role.", offerUndo: false });
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

function showSessionJob(index) {
  const session = state.applySession;
  if (!session) return;
  Object.assign(session, { index, awaitingReturn: "", checkingKey: "", checkError: "" });
  if (index >= session.keys.length) { navigate("/queue/session/summary"); return; }
  state.selectedKey = session.keys[index];
  navigate(`/queue/session/${encodeURIComponent(state.selectedKey)}`);
}

// one undo entry per step
async function recordSessionStep(key, kind, { reason = "", message = "", offerUndo = true, job = null, status = "" } = {}) {
  const session = state.applySession;
  if (!session) return;
  const index = session.keys.indexOf(key);
  const step = stepAfter(session, index);
  const change = (lists) => ({ ...withRecord(lists, key, kind, reason), ended: step.ended });
  const outcome = await saveStep({ session, key, kind, change, write: writeSession, latest: () => state.applySession });
  if (outcome.ended || outcome.kept || outcome.error?.conflict) {
    const text = outcome.ended ? "The session was ended in another tab, so this step wasn't saved." : outcome.kept ? `That job was already marked ${outcome.kept} in another tab, so this tab now shows that session.` : "The session changed in another tab, so this tab now shows that version.";
    if (job && !outcome.kept) offerUndoSnackbar(`${text} The job is still applied.`, [key], () => undoUnsavedStep({ key, before: null, job, status }));
    else { assertiveRegion.textContent = text; showSnackbar(text); }
    if (outcome.ended) navigate("/queue/session/summary"); else renderApplySession({ focus: true });
    return;
  }
  if (!outcome.saved) {
    Object.assign(session, change(outcome.before), { endedAt: step.ended ? Date.now() : null });
    if (step.ended) stopSessionClock();
  }
  showSessionJob(step.index);
  const text = outcome.saved ? message : `${message} Couldn't save the session, so a reload may lose this step.`.trim();
  const undo = offerUndo ? stepUndo(outcome, Boolean(job)) : "";
  const { revision } = session; // read now, a refresh moves it
  if (undo === "session") offerUndoSnackbar(text, [sessionScope(session), key], () => undoSessionStep({ key, before: outcome.before, revision, job, status }), { latest: sessionScope(session) });
  else if (undo === "job") offerUndoSnackbar(text, [sessionScope(session), key], () => undoUnsavedStep({ key, before: outcome.before, revision, job, status }), { latest: sessionScope(session) });
  else if (text) showSnackbar(text);
}

// the job saved, the session didnt
async function undoUnsavedStep({ key, before, revision, job, status }) {
  const session = state.applySession;
  const outcome = await undoUnsaved({
    revertJob: async () => { job.status = (await patchJob(job, status, null, { expected: "applied" })).status; },
    saveSession: async () => {
      if (!session || !before) return;
      try {
        await restoreUnsaved({ session, before, revision, key, write: writeSession });
      } finally {
        if (!session.endedAt) startSessionClock();
      }
    },
  });
  if (!outcome.undone) {
    showSnackbar(outcome.error?.conflict ? `Couldn't undo. That job moved to ${statusLabel(outcome.error.conflict)} since.` : `${outcome.error?.message || "Couldn't undo."} It stays applied.`);
    return;
  }
  jobsWriteLanded();
  if (session && before) showSessionJob(Math.max(0, session.keys.indexOf(key)));
  else renderApplySession();
  showSnackbar(outcome.sessionError?.conflict ? "Job status undone. The session changed in another tab, so this tab now shows that version." : outcome.sessionError ? "Job status undone, but the session still couldn't be saved." : "Undone.");
}

async function undoSessionStep({ key, before, revision, job = null, status = "" }) {
  const session = state.applySession;
  if (!session) { showSnackbar("Couldn't undo. That session is gone."); return; }
  const outcome = await undoStep({
    saveSession: () => writeSession(session, before, revision),
    revertJob: job ? async () => { job.status = (await patchJob(job, status, null, { expected: "applied" })).status; } : null,
  });
  if (!outcome.undone) {
    showSnackbar(outcome.error?.conflict ? "Couldn't undo. The session changed in another tab, so nothing was changed." : `${outcome.error?.message || "Couldn't save the session."} The step stays.`);
    renderApplySession();
    return;
  }
  if (outcome.jobError) {
    const moved = outcome.jobError.conflict ? `it moved to ${statusLabel(outcome.jobError.conflict)} since` : outcome.jobError.message;
    const text = `The session step is undone, but the job is still applied: ${moved}. Reloaded the latest.`;
    assertiveRegion.textContent = text;
    showSnackbar(text);
    reloadJobs();
    showSessionJob(Math.max(0, session.keys.indexOf(key)));
    return;
  }
  showSessionJob(Math.max(0, session.keys.indexOf(key)));
  showSnackbar("Undone.");
}

function advanceApplySession() {
  const session = state.applySession;
  if (!session) return;
  const key = session.keys[session.index];
  recordSessionStep(key, "skipped", { reason: "Skipped in session", message: "Skipped." });
}

function stepApplySession(delta) {
  const session = state.applySession;
  if (!session) return;
  const index = session.index + delta;
  if (index < 0) return;
  if (index >= session.keys.length) endApplySession();
  else showSessionJob(index);
}

async function markSessionApplied() {
  const session = state.applySession;
  if (!session) return;
  const key = session.keys[session.index];
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  const previous = job?.status || "applying";
  const documentId = document.querySelector("#session-resume")?.value || null;
  try {
    const response = await secureWrite(`/api/v1/applications/${encodeURIComponent(key)}/applied`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ document_id: documentId }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The application could not be saved.");
    jobsWriteLanded();
    const snapshot = (await response.json()).snapshot;
    if (job) Object.assign(job, { status: "applied", applied_at: snapshot.captured_at, resume_document_id: snapshot.resume_document_id, resume_name: snapshot.resume_name, queue_position: null });
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "The application could not be saved.");
    return;
  }
  // a repeat mark has nothing to revert
  recordSessionStep(key, "applied", { message: "Marked applied.", ...(previous === "applied" ? {} : { job, status: previous }) });
}

async function endApplySession({ offerUndo = true } = {}) {
  const session = state.applySession;
  if (!session) return;
  if (session.endedAt) { navigate("/queue/session/summary"); return; }
  const key = session.keys[Math.min(session.index, session.keys.length - 1)];
  const before = sessionLists(session);
  try {
    await writeSession(session, { ...before, ended: true }, session.revision);
  } catch (error) {
    // still open, so stay here
    const text = error.conflict ? "The session changed in another tab, so this tab now shows that version." : `${error.message} The session is still open.`;
    assertiveRegion.textContent = text;
    showSnackbar(text);
    if (error.conflict || state.jobs.some((job) => job.dedupe_key === key)) renderApplySession();
    return;
  }
  navigate("/queue/session/summary");
  const { revision } = session;
  if (offerUndo) offerUndoSnackbar("Session ended.", [sessionScope(session)], () => undoSessionStep({ key, before, revision }), { latest: sessionScope(session) });
}

// another tab may have moved on
async function refreshApplySession() {
  const session = state.applySession;
  if (!session || !window.location.pathname.startsWith("/queue/session/")) return;
  const read = sessionLoads.start();
  try {
    const response = await fetch(`/api/v1/queue/session/${session.id}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const row = (await response.json()).session;
    if (!sessionLoads.current(read) || state.applySession !== session || staleRead(session, row)) return;
    adoptSession(row);
    renderApplySession();
  } catch {} finally {
    sessionLoads.finish(read);
  }
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
    // the h1 arrives with the data
    if (!state.companiesData) { state.companiesFocusPending ||= focus; loadCompanies(name); }
  } else if (root === "profile") {
    renderProfile({ focus });
    if (!state.loaded && !state.loading) loadInbox();
    if (!state.rules && !state.rulesLoading) loadRules();
  } else renderPlaceholder(root, { focus });
  mountBulkBars(); // a bar may have gone
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
  const read = jobLoads.start();
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
    if (jobLoads.current(read) || !state.loaded) adoptJobs(payload.jobs); // a queue reload already won
    adoptSavedViews(viewsPayload);
    state.refreshedAt = payload.refreshed_at || null;
    state.loaded = true;
    inboxLoadedAt = Date.now();
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
    jobLoads.finish(read);
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

const quickFillWords = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

// Every typed word has to match, in any order. Punctuation is ignored ("co-op",
// "coop", "co op"). Long answers are matched by name only, so a short query is
// not drowned in essay text.
function quickFillMatches(item, query) {
  const own = quickFillWords(`${item.label} ${item.group} ${item.detail || ""}`);
  const value = String(item.value).length <= 300 ? quickFillWords(item.value) : "";
  const haystack = `${own} ${value}`;
  const compact = haystack.replace(/ /g, "");
  return quickFillWords(query).split(" ").every((word) => word && (haystack.includes(word) || compact.includes(word)));
}

// Name matches come before value-only matches.
function quickFillRank(item, query) {
  const label = quickFillWords(item.label);
  const words = quickFillWords(query).split(" ");
  if (words.every((word) => label.split(" ").some((part) => part.startsWith(word)))) return 0;
  return words.every((word) => label.includes(word)) ? 1 : 2;
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
    const visible = query ? items.filter((item) => quickFillMatches(item, query)).sort((a, b) => quickFillRank(a, query) - quickFillRank(b, query)) : items;
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

// --- profile editor lists ---

const profileNames = { fields: (row) => row.label || row.key, documents: (row) => row.name, answer_templates: (row) => row.name, stories: (row) => row.title };
const profileAttrs = (kind, row, property) => `data-profile-kind="${kind}" data-profile-id="${escapeHtml(itemId(kind, row))}" data-profile-property="${property}"`;

function findProfileItem(kind, id) {
  const wanted = String(state.quickFillAliases[id] ?? id);
  return (state.quickFillProfile?.[kind] || []).find((row) => itemId(kind, row) === wanted) || null;
}

// unsaved ids resolve to saved ones
function canonicalRef(ref) {
  const [kind, ...rest] = String(ref).split(":");
  const id = rest.join(":");
  return `${kind}:${state.quickFillAliases[id] ?? id}`;
}

function profileItemMarkup(kind, row, body) {
  const name = profileNames[kind](row) || PROFILE_KINDS[kind].one;
  return `<article class="profile-item" data-profile-ref="${escapeHtml(`${kind}:${itemId(kind, row)}`)}"><button class="profile-drag-handle drag-handle interactive" type="button" data-drag-handle aria-label="Reorder ${escapeHtml(name)}" title="Reorder ${escapeHtml(name)}">${icons.queue}</button><div class="profile-item-body">${body}</div>${moreButtonMarkup(name)}</article>`;
}

function profileAddMarkup(kind, scope, { label, button, placeholder, maxlength, extra = "" }) {
  const reason = limitReason(kind, (state.quickFillProfile?.[kind] || []).length);
  const hint = `profile-limit-${scope}-${kind}`;
  return `<div class="profile-add"><label class="quick-fill-edit-field"><span>${escapeHtml(label)}</span><input type="text" maxlength="${maxlength}" placeholder="${escapeHtml(placeholder)}" data-profile-add-input="${kind}" ${reason ? `disabled aria-describedby="${hint}"` : ""}></label>${extra}<button class="tonal-button interactive" type="button" data-profile-add="${kind}" ${reason ? `disabled aria-describedby="${hint}"` : ""}>${escapeHtml(button)}</button>${reason ? `<p class="profile-limit" id="${hint}">${escapeHtml(reason)}</p>` : ""}</div>`;
}

function profileSectionMarkup(kind, title, rows, empty, add) {
  const error = state.quickFillProfileError?.kind === kind ? `<p class="profile-error" role="alert">${escapeHtml(state.quickFillProfileError.message)}</p>` : "";
  return `<section data-profile-section="${kind}"><h3>${escapeHtml(title)}</h3>${rows.length ? `<div class="profile-list" data-profile-list="${kind}">${rows.join("")}</div>` : `<p class="quick-fill-empty">${escapeHtml(empty)}</p>`}${error}${add}</section>`;
}

function storyMarkup(story) {
  const attrs = (property) => profileAttrs("stories", story, property);
  return profileItemMarkup("stories", story, `<details class="quick-fill-edit-card"><summary>${escapeHtml(story.title)}</summary>${editorField("Title", story.title || "", attrs("title"))}${editorField("Competencies", (story.competencies || []).join(", "), attrs("competencies"))}${["situation", "task", "action", "result", "reflection"].map((part) => editorField(statusLabel(part), story[part] || "", attrs(part), { multiline: true })).join("")}</details>`);
}

function templateMarkup(template) {
  const count = `${new Intl.NumberFormat().format(String(template.body || "").length)} characters before variables are replaced`;
  return profileItemMarkup("answer_templates", template, `<details class="quick-fill-edit-card"><summary>${escapeHtml(template.name)}</summary>${editorField("Name", template.name || "", `data-profile-rename="answer_templates" data-profile-id="${escapeHtml(itemId("answer_templates", template))}" data-profile-property="name" maxlength="120"`)}${editorField("Template", template.body || "", profileAttrs("answer_templates", template, "body"), { multiline: true })}<small>${count}</small></details>`);
}

function fieldMarkup(field, scope) {
  const rename = (property, max) => `data-profile-rename="fields" data-profile-id="${escapeHtml(itemId("fields", field))}" data-profile-property="${property}" maxlength="${max}"`;
  const value = editorField(field.label || field.key, field.value || "", `${profileAttrs("fields", field, "value")} maxlength="10000"`, {
    multiline: String(field.value || "").length > 120 || String(field.value || "").includes("\n"),
  });
  return profileItemMarkup("fields", field, `${value}<details class="quick-fill-edit-card"><summary>Label and group<span class="profile-badge">${escapeHtml(field.group || "")}</span></summary>${editorField("Label", field.label || "", rename("label", 120))}${editorField("Group", field.group || "", `${rename("group", 80)} list="profile-groups-${scope}"`)}</details>`);
}

function fieldAddMarkup(profile, scope) {
  const groups = [...new Set((profile.fields || []).map((field) => field.group).filter(Boolean))];
  const list = `<datalist id="profile-groups-${scope}">${groups.map((group) => `<option value="${escapeHtml(group)}"></option>`).join("")}</datalist>`;
  const group = `<label class="quick-fill-edit-field profile-add-group"><span>Group</span><input type="text" maxlength="80" placeholder="${escapeHtml(groups.at(-1) || "Other")}" list="profile-groups-${scope}" data-profile-add-extra="fields-group"></label>`;
  const setup = (profile.fields || []).length ? "" : `<button class="tonal-button interactive" type="button" data-quick-fill-setup ${state.quickFillContextPendingKey || state.quickFillSetupSaving ? "disabled" : ""}>Create blank fields</button>`;
  return `${setup}${profileAddMarkup("fields", scope, { label: "New field", button: "Add field", placeholder: "Portfolio", maxlength: 120, extra: group })}${list}`;
}

function documentMarkup(document) {
  const name = `data-profile-rename="documents" data-profile-id="${escapeHtml(itemId("documents", document))}" data-profile-property="name" maxlength="160"`;
  const badge = document.default ? '<span class="profile-badge">Default resume</span>' : "";
  return profileItemMarkup("documents", document, `<details class="quick-fill-edit-card"><summary>${escapeHtml(document.name)}${badge}</summary>${editorField("Name", document.name || "", name)}${editorField("Date", document.date || "", profileAttrs("documents", document, "date"), { type: "date" })}${editorField("Path or URL", document.url || "", `${profileAttrs("documents", document, "url")} maxlength="2000"`, { inputmode: "url", spellcheck: "false", autocapitalize: "off" })}</details>`);
}

function quickFillEditorMarkup(scope = "docked") {
  const profile = state.quickFillProfile || {};
  const job = currentJob();
  const fields = profileSectionMarkup("fields", "Profile fields", (profile.fields || []).map((field) => fieldMarkup(field, scope)), "No fields yet. Start with blank labels, then enter only what you want to reuse.", fieldAddMarkup(profile, scope));
  const templates = profileSectionMarkup("answer_templates", "Short answers", (profile.answer_templates || []).map(templateMarkup), "No templates yet.", profileAddMarkup("answer_templates", scope, { label: "New template", button: "Add template", placeholder: "Why this company", maxlength: 120 }));
  const documents = profileSectionMarkup("documents", "Documents", (profile.documents || []).map(documentMarkup), "No documents yet. Add a resume version to pick it when you mark an application.", profileAddMarkup("documents", scope, { label: "New document", button: "Add document", placeholder: "Platform resume", maxlength: 160 }));
  const stories = profileSectionMarkup("stories", "Story bank", (profile.stories || []).map(storyMarkup), "No stories yet.", profileAddMarkup("stories", scope, { label: "New story", button: "Add story", placeholder: "Stabilized a failing service", maxlength: 160 }));
  const overrides = (profile.answer_templates || []).map((template) => `<label class="quick-fill-edit-field"><span>${escapeHtml(template.name)} override</span><textarea rows="3" placeholder="Use the global template" data-context-kind="override" data-context-key="${escapeHtml(template.name)}">${escapeHtml(profile.answer_overrides?.[template.name] || "")}</textarea></label>`).join("");
  const account = profile.company_account || {};
  const accountEditor = job ? `<section><h3>${escapeHtml(job.company)} ATS account</h3><label class="quick-fill-edit-field"><span>Account exists</span><select data-context-kind="account" data-context-property="account_exists"><option value="" ${account.account_exists == null ? "selected" : ""}>Not recorded</option><option value="yes" ${account.account_exists === true ? "selected" : ""}>Yes</option><option value="no" ${account.account_exists === false ? "selected" : ""}>No</option></select></label>${editorField("Sign-in email", account.sign_in_email || "", 'data-context-kind="account" data-context-property="sign_in_email"', { type: "email", autocomplete: "email" })}${editorField("Password manager link", account.password_manager_url || "", 'data-context-kind="account" data-context-property="password_manager_url"', { type: "url", inputmode: "url", autocomplete: "off", spellcheck: "false", autocapitalize: "off" })}<p class="quick-fill-privacy">JobSeer never stores passwords.</p></section>` : "";
  return `<div class="quick-fill-editor">${accountEditor}${job ? `<section><h3>Answers for this job</h3>${overrides || '<p class="quick-fill-empty">No templates yet.</p>'}</section>` : ""}${fields}${documents}${templates}${stories}<div class="quick-fill-data-actions"><button class="outlined-button interactive" type="button" data-profile-export>Export JSON</button><button class="outlined-button interactive" type="button" data-profile-import-trigger>Import JSON</button><input class="visually-hidden" type="file" accept="application/json,.json" data-profile-import tabindex="-1"></div></div>`;
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
    ? quickFillEditorMarkup(popout ? "popout" : embedded ? "session" : "docked")
    : `<label class="quick-fill-search" for="quick-fill-search-${popout ? "popout" : embedded ? "session" : "docked"}">${icons.search}<input id="quick-fill-search-${popout ? "popout" : embedded ? "session" : "docked"}" type="search" value="${escapeHtml(state.quickFillQuery)}" placeholder="Search fields" aria-label="Search Quick-fill fields"></label>${guidance ? `<p class="quick-fill-guidance">${escapeHtml(guidance)}</p>` : ""}${passwordManagerLink}<div class="quick-fill-groups">${state.quickFillError ? `<div class="quick-fill-empty" role="alert">${escapeHtml(state.quickFillError)}</div>` : content || (!state.quickFillProfile ? '<p class="quick-fill-empty">Loading fields…</p>' : state.quickFillProfile.fields?.length ? '<p class="quick-fill-empty">No filled fields match this search. Use Edit to add a value.</p>' : emptySetup)}</div>`;
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
    const add = event.target.closest("[data-profile-add]");
    if (add) { addProfileItem(add.dataset.profileAdd, root); return; }
    if (event.target.closest("[data-profile-export]")) { exportQuickFillProfile(); return; }
    if (event.target.closest("[data-profile-import-trigger]")) { root.querySelector("[data-profile-import]")?.click(); return; }
    if (event.target.closest("[data-quick-fill-close]")) {
      if (popout) state.quickFillPopout?.close(); else closeQuickFill();
    }
  });
  root.addEventListener("focusin", (event) => {
    if (event.target.matches("[data-profile-kind]")) beginProfileTyping(event.target);
    else if (event.target.matches("[data-context-kind]")) beginContextTyping(event.target);
  });
  root.addEventListener("focusout", (event) => {
    if (event.target.matches("[data-profile-kind]")) settleProfileTyping(event.target);
    else if (event.target.matches("[data-context-kind]")) settleContextTyping(event.target);
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
    if (event.isComposing) return;
    state.quickFillQuery = event.target.value;
    const { id, selectionStart, selectionEnd } = event.target;
    if (id === "quick-fill-search-session") renderSessionQuickFill(); else renderQuickFill();
    const search = (popout ? state.quickFillPopout?.document : document)?.querySelector(`#${id}`);
    search?.focus(); search?.setSelectionRange(selectionStart, selectionEnd);
  });
  root.addEventListener("change", (event) => {
    if (event.target.matches("select[data-context-kind]")) { updateQuickFillContext(event.target); return; }
    if (event.target.matches("[data-profile-rename]")) { commitProfileProperty(event.target); return; }
    if (event.target.matches("[data-profile-import]") && event.target.files?.[0]) {
      const [file] = event.target.files; event.target.value = ""; importQuickFillProfile(file);
    }
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.matches("[data-profile-add-input]")) { event.preventDefault(); addProfileItem(event.target.dataset.profileAddInput, root); return; }
    if (event.target.matches("input[data-profile-rename]") && (event.key === "Enter" || event.key === "Escape")) {
      event.preventDefault();
      if (event.key === "Escape") event.target.value = findProfileItem(event.target.dataset.profileRename, event.target.dataset.profileId)?.[event.target.dataset.profileProperty] ?? event.target.value;
      else commitProfileProperty(event.target);
      return;
    }
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

// --- profile saves ---
// one in flight, latest revision
const profileSaves = createSaveQueue({ send: sendProfile, onConflict: reportProfileConflict });

function queueProfileSave(changes = 1) {
  return profileSaves.save(changes);
}

function cancelAutosave() {
  clearTimeout(state.quickFillSaveTimer);
  state.quickFillSaveTimer = null;
}

async function sendProfile() {
  const version = state.quickFillSaveVersion;
  const sent = { ...withOrder(profileDocument()), revision: state.quickFillProfile?.revision ?? 0 };
  const known = new Set(["documents", "answer_templates"].flatMap((kind) => (state.quickFillSavedProfile?.[kind] || []).map((row) => `${kind}:${row.id}`)));
  setQuickFillSaveState("Saving…");
  try {
    const response = await fetch("/api/v1/profile", {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf, Accept: "application/json" },
      body: JSON.stringify({ profile: sent, ...(version === state.quickFillImportVersion ? { import: true } : {}) }),
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 409 && body.profile) { adoptProfileConflict(body.profile); return Object.assign(new Error(body.error || "Your profile changed in another tab."), { conflict: true }); }
    if (!response.ok) throw new Error(body.error || "Quick-fill could not be saved.");
    const saved = Object.assign(body.profile, profileContextDocument());
    const aliases = adoptIds(sent, saved, known);
    Object.assign(state.quickFillAliases, aliases);
    state.quickFillSavedProfile = cloneJson(saved);
    if (version === state.quickFillSaveVersion) state.quickFillProfile = saved;
    else {
      // later edits need the real ids
      state.quickFillProfile.revision = saved.revision;
      for (const kind of ["documents", "answer_templates"]) for (const row of state.quickFillProfile[kind] || []) if (row.id in aliases) row.id = aliases[row.id];
    }
    state.eligibility.clear();
    if (state.selectedKey) loadEligibility(state.selectedKey);
    setQuickFillSaveState("Saved automatically");
    return null;
  } catch (error) {
    if (version === state.quickFillSaveVersion && state.quickFillSavedProfile) {
      state.quickFillProfile = cloneJson(state.quickFillSavedProfile);
      renderQuickFill();
      renderSessionQuickFill();
    }
    setQuickFillSaveState("Not saved");
    assertiveRegion.textContent = `${error.message} Changes rolled back.`;
    showSnackbar(`${error.message} Changes rolled back.`);
    return error;
  }
}

// never overwrite the other tab
function adoptProfileConflict(latest) {
  state.quickFillSaveVersion += 1;
  Object.assign(latest, profileContextDocument());
  state.quickFillProfile = latest;
  state.quickFillSavedProfile = cloneJson(latest);
  renderQuickFill();
  renderSessionQuickFill();
  setQuickFillSaveState("Reloaded");
}

// a waiting autosave is lost too
function reportProfileConflict(dropped) {
  const typing = state.quickFillSaveTimer ? 1 : 0;
  cancelAutosave();
  const message = conflictMessage(dropped + typing);
  assertiveRegion.textContent = message;
  showSnackbar(message);
}

async function setupBlankQuickFill() {
  if (!state.quickFillEnabled || !state.quickFillProfile || state.quickFillProfile.fields?.length || state.quickFillSetupSaving || state.quickFillContextPendingKey) return;
  state.quickFillSetupSaving = true;
  state.quickFillProfile.fields = cloneJson(blankQuickFillFields);
  state.quickFillSaveVersion += 1;
  const error = await queueProfileSave();
  state.quickFillSetupSaving = false;
  if (error) return;
  state.quickFillEdit = true;
  renderQuickFill();
  renderSessionQuickFill();
  requestAnimationFrame(() => document.querySelector("#quick-fill-sheet [data-profile-kind], #session-quick-fill [data-profile-kind]")?.focus());
  showSnackbar("Blank Quick-fill fields are ready. Add only details you want to reuse.");
}

// --- profile changes ---
// so undo reverts one action

function applyProfileOp(op) {
  const renamed = op.type === "update" && op.kind === "answer_templates" && op.patch.name !== undefined ? findProfileItem("answer_templates", op.id)?.name : undefined;
  const result = applyOp(state.quickFillProfile, op, state.quickFillAliases);
  // overrides are keyed by name
  if (result.inverse && renamed !== undefined && renamed !== op.patch.name) {
    state.quickFillProfile.answer_overrides = renameOverrides(state.quickFillProfile.answer_overrides, renamed, op.patch.name);
    if (state.quickFillSavedContext) state.quickFillSavedContext.answer_overrides = renameOverrides(state.quickFillSavedContext.answer_overrides, renamed, op.patch.name);
  }
  return result;
}

// pending typing rides this save
function saveProfileChange() {
  const typing = state.quickFillSaveTimer ? 1 : 0;
  state.quickFillSaveVersion += 1;
  cancelAutosave();
  return queueProfileSave(1 + typing);
}

function changeProfile(op, { message, undone, focus = null, flip: animate = false }) {
  if (!state.quickFillProfile) return false;
  const kind = op.kind || "documents";
  const keys = opKeys(state.quickFillProfile, op);
  const result = applyProfileOp(op);
  if (result.noop) return false;
  if (result.error) {
    state.quickFillProfileError = { kind, message: result.error };
    rerenderProfile();
    return false;
  }
  state.quickFillProfileError = null;
  rerenderProfile({ focus, animate: animate && focus?.surface });
  const entry = { message, keys, run: () => undoProfileChange(result.inverse, undone, focus?.surface, entry) };
  offerMoveSnackbar(message, `profile-${kind}`, entry, saveProfileChange());
  return true;
}

async function undoProfileChange(inverse, undone, surface, entry) {
  if (!state.quickFillProfile) return;
  const doomed = inverse.type === "remove" && findProfileItem(inverse.kind, inverse.id);
  if (doomed) {
    const trigger = surface && [...surface.querySelectorAll(".profile-item[data-profile-ref]")].find((node) => canonicalRef(node.dataset.profileRef) === canonicalRef(`${inverse.kind}:${inverse.id}`))?.querySelector("[data-menu-trigger]");
    // cancelled, so keep the undo
    if (!await confirmRemoval(inverse.kind, doomed, trigger)) { if (entry) undoStack.push(entry); return; }
  }
  const result = applyProfileOp(inverse);
  if (result.noop) return;
  if (result.error) { showSnackbar(`Couldn't undo. ${result.error}`); return; }
  state.quickFillProfileError = null;
  const ref = inverse.type === "add" ? `${inverse.kind}:${itemId(inverse.kind, inverse.item)}` : inverse.type === "default" ? "" : `${inverse.kind}:${inverse.id}`;
  rerenderProfile({ focus: ref && surface ? { surface, ref, role: "[data-menu-trigger]", soft: true } : null, animate: inverse.type === "move" && surface });
  saveProfileChange().then((error) => { if (!error) showSnackbar(undone); });
}

function focusRole(element) {
  if (element.matches("[data-drag-handle]")) return "[data-drag-handle]";
  if (element.matches("[data-menu-trigger]")) return "[data-menu-trigger]";
  if (element.matches("summary")) return "summary";
  return element.dataset.profileProperty ? `[data-profile-property="${element.dataset.profileProperty}"]` : "";
}

function rerenderProfile({ focus = null, animate = null } = {}) {
  const render = () => { renderQuickFill(); renderSessionQuickFill(); };
  if (animate) flipKeyed(`#${animate.id} .profile-item[data-profile-ref]`, (node) => canonicalRef(node.dataset.profileRef), render);
  else render();
  if (focus?.surface?.isConnected) focusProfileTarget(focus.surface, focus);
}

const keptAttrs = ["data-profile-add-input", "data-profile-add", "data-profile-add-extra", "data-context-key", "data-context-property", "data-quick-fill-edit"];

function keptSelector(element) {
  if (element.id) return `#${CSS.escape(element.id)}`;
  const attr = keptAttrs.find((name) => element.hasAttribute(name));
  return attr ? `[${attr}="${CSS.escape(element.getAttribute(attr))}"]` : "";
}

// keep open cards, scroll and focus
function keepSurface(surface, render) {
  if (!surface) { render(); return; }
  const doc = surface.ownerDocument;
  const active = surface.contains(doc.activeElement) ? doc.activeElement : null;
  const item = active?.closest(".profile-item");
  const open = new Set([...surface.querySelectorAll(".profile-item details[open]")].map((node) => canonicalRef(node.closest(".profile-item").dataset.profileRef)));
  const scroll = surface.querySelector(".quick-fill-editor")?.scrollTop || 0;
  const adds = [...surface.querySelectorAll("[data-profile-add-input], [data-profile-add-extra]")].map((input) => [keptSelector(input), input.value]);
  const was = active && (item ? { ref: item.dataset.profileRef, role: focusRole(active) } : { selector: keptSelector(active) });
  const range = active && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
  render();
  for (const node of surface.querySelectorAll(".profile-item[data-profile-ref]")) if (open.has(canonicalRef(node.dataset.profileRef))) node.querySelector("details")?.setAttribute("open", "");
  for (const [selector, value] of adds) { const input = selector && surface.querySelector(selector); if (input && !input.disabled) input.value = value; }
  const editor = surface.querySelector(".quick-fill-editor");
  if (editor) editor.scrollTop = scroll;
  if (!was || surface.contains(doc.activeElement)) return;
  if (was.ref) focusProfileTarget(surface, was);
  else if (was.selector) surface.querySelector(was.selector)?.focus({ preventScroll: true });
  if (range && doc.activeElement?.setSelectionRange && doc.activeElement.type !== "email" && doc.activeElement.type !== "url") doc.activeElement.setSelectionRange(...range);
}

function focusProfileTarget(surface, target) {
  const doc = surface.ownerDocument;
  if (target.soft && surface.contains(doc.activeElement)) return;
  if (target.add) {
    const add = surface.querySelector(target.button ? `[data-profile-add="${target.add}"]:not(:disabled)` : `[data-profile-add-input="${target.add}"]:not(:disabled)`) || surface.querySelector(`[data-profile-section="${target.add}"] h3`);
    if (add && !add.matches("h3")) add.focus({ preventScroll: false });
    else { const heading = surface.querySelector(".quick-fill-header h2"); heading?.setAttribute("tabindex", "-1"); heading?.focus(); }
    return;
  }
  const wanted = canonicalRef(target.ref);
  const item = [...surface.querySelectorAll(".profile-item[data-profile-ref]")].find((node) => canonicalRef(node.dataset.profileRef) === wanted);
  if (!item) return;
  if (target.open) item.querySelector("details")?.setAttribute("open", "");
  const control = (target.role && item.querySelector(target.role)) || item.querySelector("[data-menu-trigger]");
  if (control === doc.activeElement) return;
  control?.focus({ preventScroll: true });
  control?.scrollIntoView({ block: "nearest" });
}

function newProfileItem(kind, text, root) {
  const taken = (state.quickFillProfile?.[kind] || []).map((row) => itemId(kind, row));
  if (kind === "stories") return { id: newId("story", taken), title: text, situation: "", task: "", action: "", result: "", reflection: "", competencies: [] };
  if (kind === "answer_templates") return { id: newId("new-template", taken), name: text, body: "" }; // the server assigns the real id
  if (kind === "documents") return { id: newId("new-document", taken), name: text, date: "", url: "", default: false };
  if (kind === "fields") {
    const group = root.querySelector('[data-profile-add-extra="fields-group"]');
    return { key: fieldKey(text, taken), label: text, group: group?.value.trim() || group?.placeholder || "Other", value: "", pinned: false };
  }
  return null;
}

function addProfileItem(kind, root) {
  const input = root.querySelector(`[data-profile-add-input="${kind}"]`);
  if (!input || input.disabled) return;
  const { one } = PROFILE_KINDS[kind];
  const text = input.value.trim();
  const surface = root.closest("#quick-fill-sheet, #session-quick-fill") || root;
  if (!text) {
    state.quickFillProfileError = { kind, message: `Enter a name for the new ${one} first.` };
    rerenderProfile({ focus: { surface, add: kind } });
    return;
  }
  const item = newProfileItem(kind, text, root);
  const noun = `${one.slice(0, 1).toUpperCase()}${one.slice(1)}`;
  const ref = `${kind}:${itemId(kind, item)}`;
  const value = input.value;
  input.value = "";
  const added = changeProfile({ type: "add", kind, item }, { message: `${noun} added.`, undone: `${noun} removed.`, focus: { surface, ref, role: profileFirstInput[kind], open: true } });
  if (!added) { const again = surface.querySelector(`[data-profile-add-input="${kind}"]`); if (again) again.value = value; }
}

const profileFirstInput = { stories: '[data-profile-property="situation"]', answer_templates: '[data-profile-property="body"]', documents: '[data-profile-property="url"]', fields: '[data-profile-property="value"]' };

const renameCopy = {
  "answer_templates:name": { empty: "A template needs a name.", message: "Template renamed.", undone: "Rename undone." },
  "documents:name": { empty: "A document needs a name.", message: "Document renamed.", undone: "Rename undone." },
  "fields:label": { empty: "A field needs a label.", message: "Field renamed.", undone: "Rename undone." },
  "fields:group": { empty: "A field needs a group.", message: (group) => `Moved to ${group}.`, undone: "Group change undone." },
};

// one undo entry per rename
function commitProfileProperty(control) {
  const kind = control.dataset.profileRename;
  const property = control.dataset.profileProperty;
  const item = findProfileItem(kind, control.dataset.profileId);
  const copy = renameCopy[`${kind}:${property}`];
  if (!item || !copy) return;
  const value = control.value.trim();
  if (value === String(item[property] ?? "")) { control.value = value; return; }
  const surface = control.closest("#quick-fill-sheet, #session-quick-fill");
  const focus = surface && { surface, ref: `${kind}:${itemId(kind, item)}`, role: `[data-profile-property="${property}"]` };
  if (!value) {
    state.quickFillProfileError = { kind, message: copy.empty };
    rerenderProfile({ focus });
    return;
  }
  const changed = changeProfile({ type: "update", kind, id: itemId(kind, item), patch: { [property]: value } }, { message: typeof copy.message === "function" ? copy.message(value) : copy.message, undone: copy.undone, focus });
  if (changed || !focus) return;
  // keep their text beside the error
  const again = [...surface.querySelectorAll(`[data-profile-rename="${kind}"]`)].find((input) => input.dataset.profileProperty === property && canonicalRef(`${kind}:${input.dataset.profileId}`) === canonicalRef(focus.ref));
  if (again) { again.value = control.value; again.closest("details")?.setAttribute("open", ""); again.focus(); }
}

function profileContext(el) {
  const [kind, ...rest] = el.dataset.profileRef.split(":");
  const id = rest.join(":");
  const rows = state.quickFillProfile?.[kind];
  const item = findProfileItem(kind, id);
  if (!item || !rows) return null;
  return { el, kind, id: itemId(kind, item), item, index: rows.indexOf(item), count: rows.length, name: profileNames[kind](item), surface: el.closest("#quick-fill-sheet, #session-quick-fill") };
}

// pointer drops let the spring settle
function moveProfileItem(c, to, { role = "[data-menu-trigger]", pointer = false } = {}) {
  const target = Math.max(0, Math.min(c.count - 1, to));
  const focus = c.surface && !pointer ? { surface: c.surface, ref: `${c.kind}:${c.id}`, role } : null;
  const moved = changeProfile({ type: "move", kind: c.kind, id: c.id, to: target }, { message: moveAnnouncement(target, c.count), undone: "Move undone.", focus, flip: true });
  if (moved) announce(moveAnnouncement(target, c.count));
  return moved;
}

// the loaded count may be stale
async function documentReferences(id) {
  const wanted = String(state.quickFillAliases[id] ?? id);
  if (!(state.quickFillSavedProfile?.documents || []).some((row) => String(row.id) === wanted)) return 0; // unsaved, nothing points here
  const response = await fetch("/api/v1/profile", { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error("Couldn't check which applications use this document.");
  const latest = (await response.json()).profile;
  return Number(latest.documents?.find((row) => String(row.id) === wanted)?.application_count) || 0;
}

async function removalWarning(kind, item) {
  if (kind === "documents") {
    const count = await documentReferences(itemId(kind, item));
    return { count, text: referenceWarning(count) };
  }
  if (kind === "fields") return { count: 0, text: eligibilityWarning(String(item.key).toLowerCase(), state.quickFillProfile.eligibility_checks) };
  return { count: 0, text: "" };
}

// other records change, so carrel confirms
async function confirmRemoval(kind, item, trigger) {
  let warning;
  try {
    warning = await removalWarning(kind, item);
  } catch (error) {
    showSnackbar(`${error.message} Nothing was deleted.`);
    return null;
  }
  if (warning.text && !await confirmDelete({ title: `Delete “${profileNames[kind](item) || PROFILE_KINDS[kind].one}”?`, body: warning.text, trigger })) return null;
  return warning;
}

async function deleteProfileItem(c) {
  const checked = await confirmRemoval(c.kind, c.item, c.el.querySelector("[data-menu-trigger]"));
  if (!checked) return;
  const item = findProfileItem(c.kind, c.id);
  if (!item) return; // a reload removed it first
  const rows = state.quickFillProfile[c.kind];
  const index = rows.indexOf(item);
  const next = rows[index + 1] || rows[index - 1];
  const noun = `${PROFILE_KINDS[c.kind].one.slice(0, 1).toUpperCase()}${PROFILE_KINDS[c.kind].one.slice(1)}`;
  // carrel: next item, else add box
  const focus = c.surface && (next ? { surface: c.surface, ref: `${c.kind}:${itemId(c.kind, next)}`, role: "[data-menu-trigger]" } : { surface: c.surface, add: c.kind });
  const undone = c.kind === "documents" ? restoredMessage(checked.count) : `${noun} restored.`;
  changeProfile({ type: "remove", kind: c.kind, id: c.id }, { message: `${noun} deleted.`, undone, focus });
}

function setDefaultDocument(c, id) {
  const focus = c.surface && { surface: c.surface, ref: `documents:${c.id}`, role: "[data-menu-trigger]" };
  const copy = id == null
    ? { message: "No default resume. Apply sessions start with none picked.", undone: "Default resume restored." }
    : { message: `${c.name} is now the default resume.`, undone: "Default resume change undone." };
  changeProfile({ type: "default", id }, { ...copy, focus });
}

let confirmResolve = null;

// cancel is the default focus
function confirmDelete({ title, body, action = "Delete", cancel = "Cancel", trigger }) {
  if (confirmDialog.open) return Promise.resolve(false);
  confirmDialog.querySelector("#confirm-title").textContent = title;
  confirmDialog.querySelector("#confirm-body").textContent = body;
  confirmDialog.querySelector("[data-confirm-accept]").textContent = action;
  confirmDialog.querySelector("[data-confirm-cancel]").textContent = cancel;
  confirmDialog.returnValue = "";
  rememberDialogTrigger(trigger);
  confirmDialog.showModal();
  confirmDialog.querySelector("[data-confirm-cancel]").focus();
  return new Promise((resolve) => { confirmResolve = resolve; });
}

// --- record editors ---
// one dialog serves every record

function editFieldMarkup(field) {
  const id = `edit-${field.name}`;
  const attrs = [
    field.required ? "required" : "", field.maxlength ? `maxlength="${field.maxlength}"` : "",
    field.inputmode ? `inputmode="${field.inputmode}"` : "", `autocomplete="${field.autocomplete || "off"}"`,
    field.inputmode === "url" ? 'spellcheck="false" autocapitalize="off"' : "",
    field.min ? `min="${field.min}"` : "", field.max ? `max="${field.max}"` : "",
    field.help ? `aria-describedby="${id}-help"` : "",
  ].filter(Boolean).join(" ");
  const control = field.options
    ? `<select id="${id}" name="${field.name}" ${attrs}>${field.options.map(([value, label]) => `<option value="${escapeHtml(value)}" ${value === (field.value ?? "") ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select>`
    : field.rows
    ? `<textarea id="${id}" name="${field.name}" rows="${field.rows}" ${attrs}>${escapeHtml(field.value ?? "")}</textarea>`
    : `<input id="${id}" name="${field.name}" type="${field.type || "text"}" value="${escapeHtml(field.value ?? "")}" ${attrs}>`;
  return `<label class="capture-field" for="${id}"><span>${escapeHtml(field.label)}${field.optional ? " <small>(optional)</small>" : ""}</span>${control}${field.help ? `<small class="edit-help" id="${id}-help">${escapeHtml(field.help)}</small>` : ""}</label>`;
}

function openEditor({ title, intro = "", fields, save, key = "", trigger = null, focusBack = null, focus = "", submit = "Save", always = false }) {
  if (editDialog.open || confirmDialog.open) return;
  editor = {
    key, fields, save, focusBack, always, saving: false,
    trigger: trigger instanceof HTMLElement ? trigger : document.activeElement,
    original: Object.fromEntries(fields.map((field) => [field.name, field.value ?? ""])),
  };
  editDialog.querySelector("#edit-title").textContent = title;
  const introNode = editDialog.querySelector("#edit-intro");
  introNode.textContent = intro;
  introNode.hidden = !intro;
  editDialog.querySelector(".dialog-heading [data-edit-cancel]").setAttribute("aria-label", `Close ${title}`);
  editDialog.querySelector("#edit-fields").innerHTML = fields.map(editFieldMarkup).join("");
  editDialog.querySelector("#edit-submit").textContent = submit;
  showEditError("");
  editDialog.showModal();
  editForm.elements[focus || fields[0].name]?.focus();
}

function showEditError(message) {
  const node = editDialog.querySelector("#edit-error");
  node.textContent = message;
  node.hidden = !message;
}

function editorChanges() {
  const draft = Object.fromEntries(editor.fields.map((field) => [field.name, editForm.elements[field.name].value]));
  return changedFields(editor.original, draft);
}

async function submitEditor() {
  if (!editor || editor.saving) return;
  const changes = editorChanges();
  // a confirm can save as is
  if (!Object.keys(changes).length && !editor.always) { closeEditor(); return; }
  const button = editDialog.querySelector("#edit-submit");
  editor.saving = true;
  button.setAttribute("aria-disabled", "true");
  const error = await editor.save(changes);
  if (!editor) return;
  editor.saving = false;
  button.removeAttribute("aria-disabled");
  if (error) { showEditError(error); return; }
  closeEditor();
}

// the trigger may have rerendered
function closeEditor() {
  const current = editor;
  editor = null;
  if (editDialog.open) editDialog.close();
  if (current?.trigger?.isConnected) current.trigger.focus({ preventScroll: true });
  else current?.focusBack?.();
}

async function cancelEditor() {
  if (!editor || editor.saving) return;
  if (!Object.keys(editorChanges()).length) { closeEditor(); return; }
  const keep = !await confirmDelete({
    title: "Discard changes?", body: "Your edits here haven't been saved.",
    action: "Discard", cancel: "Keep editing", trigger: document.activeElement,
  });
  if (!keep) closeEditor();
}

// rows rerender after a save
function recordFocus(selector, fallback) {
  return () => {
    const node = document.querySelector(selector);
    const target = node ? (node.matches(".job-row") ? node : node.querySelector("button[data-menu-trigger]")) : null;
    (target || document.querySelector(fallback))?.focus({ preventScroll: true });
  };
}

function trackedJob(key) {
  return state.jobs.find((job) => job.dedupe_key === key) || state.trackerData?.applications?.find((job) => job.dedupe_key === key);
}

function editPosting(key, { trigger = null, focusBack = null, focus = "" } = {}) {
  const job = trackedJob(key);
  if (!job || state.pending.has(key)) return;
  const text = state.descriptions.get(key)?.value?.description_text || "";
  openEditor({
    title: "Edit posting", intro: "Fix what the capture got wrong. Scout refreshes keep your changes.",
    key, trigger, focusBack, focus,
    fields: [
      { name: "company", label: "Company", value: job.posting_company ?? job.company, required: true, maxlength: 500, autocomplete: "organization", help: job.posting_company && job.posting_company !== job.company ? `Shown as ${job.company} because that company was renamed or merged.` : "" },
      { name: "title", label: "Role title", value: job.title, required: true, maxlength: 500 },
      { name: "url", label: "Posting URL", type: "url", inputmode: "url", value: job.url, required: Boolean(job.url), optional: !job.url, maxlength: 4096 },
      { name: "description", label: "Description", value: text, rows: 10, optional: true, maxlength: 100000, help: "Correct the posting text. Sections and highlights are read from it." },
    ],
    save: (changes) => savePostingEdit(key, changes),
  });
  // a late load still prefills
  if (!state.descriptions.has(key)) loadDescription(key).then(() => {
    // the dialog may hold another posting now
    const field = editor?.key === key && editForm.elements.description;
    const loaded = state.descriptions.get(key)?.value?.description_text || "";
    if (field && !field.value && loaded) { field.value = loaded; editor.original.description = loaded; }
  });
}

async function savePostingEdit(key, changes) {
  try {
    const response = await secureWrite(`/api/v1/jobs/${encodeURIComponent(key)}/posting`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(changes),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) return body.error || "Couldn't save the posting. Retry shortly.";
    applyPostingResult(key, body);
    if (body.edit_id) offerUndoSnackbar("Posting updated.", [key, `posting:${key}`], () => undoPostingEdit(body.edit_id, key), { latest: `posting:${key}` });
    return "";
  } catch (error) {
    return error instanceof Error ? error.message : "Couldn't save the posting. Retry shortly.";
  }
}

function applyPostingResult(key, { job, description }) {
  if (job) for (const found of [state.jobs.find((item) => item.dedupe_key === key), state.trackerData?.applications?.find((item) => item.dedupe_key === key)]) {
    if (found) Object.assign(found, { company: job.company, posting_company: job.posting_company, company_key: job.company_key, title: job.title, url: job.url });
  }
  if (description) {
    state.descriptions.set(key, { loading: false, showLoader: false, value: description, error: description.description_error || "" });
    const found = state.jobs.find((item) => item.dedupe_key === key);
    if (found) { found.pay = description.pay || ""; found.pay_checked = Boolean(description.description_text); }
    state.overviews.delete(key);
    state.overviewCacheAttempted.delete(key);
    showCachedOverview(key);
  }
  if (state.companiesData) state.companiesData = null;
  jobsWriteLanded(); trackerWriteLanded();
  if (routeRoot() === "companies") loadCompanies();
  renderJobSurface();
}

async function undoPostingEdit(id, key) {
  try {
    const response = await secureWrite(`/api/v1/posting-edits/${id}/undo`, { method: "POST", headers: { Accept: "application/json" } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || "Couldn't undo the edit.");
    applyPostingResult(key, body);
  } catch (error) {
    showSnackbar(error instanceof Error ? error.message : "Couldn't undo the edit.");
    return;
  }
  showSnackbar("Posting edit undone.");
}

const jsonHeaders = { "Content-Type": "application/json", Accept: "application/json" };
const revisionAliases = new Map();

// our own undo isnt a conflict
function currentRevision(scope, revision) {
  let known = revision;
  while (revisionAliases.has(`${scope}@${known}`)) known = revisionAliases.get(`${scope}@${known}`);
  return known;
}

// keeps focus through a rerender
function rerenderRecords(focusBack) {
  if (routeRoot() === "tracker") renderTracker();
  else if (routeRoot() === "companies") renderCompanies();
  else if (routeRoot() === "inbox" && state.loaded) renderSelectedJob();
  if (focusBack && (!document.activeElement || document.activeElement === document.body)) focusBack();
}

function interviewFocus(id) {
  return recordFocus(`.interview-list article[data-interview-id="${id}"]`, "#interviews-title");
}

function editInterview(interview) {
  openEditor({
    title: `Edit ${interview.company} interview`,
    focusBack: interviewFocus(interview.id),
    fields: [
      { name: "starts_at", label: "Starts", type: "datetime-local", value: toLocalInput(interview.starts_at), required: true },
      { name: "ends_at", label: "Ends", type: "datetime-local", value: toLocalInput(interview.ends_at), optional: true },
      { name: "location", label: "Location", value: interview.location, optional: true, maxlength: 500 },
      { name: "notes", label: "Notes", value: interview.notes, optional: true, rows: 4, maxlength: 20000 },
    ],
    save(changes) {
      const payload = { ...changes };
      for (const name of ["starts_at", "ends_at"]) {
        if (!(name in payload)) continue;
        payload[name] = fromLocalInput(payload[name]);
        if (payload[name] === undefined) return Promise.resolve("Enter a valid interview date and time.");
      }
      return saveInterview(interview.id, payload, { message: "Interview updated.", undone: "Interview edit undone." });
    },
  });
}

async function setInterviewCancelled(interview, cancelled) {
  const error = await saveInterview(interview.id, { cancelled }, cancelled
    ? { message: "Interview cancelled.", undone: "Interview is scheduled again." }
    : { message: "Interview is scheduled again.", undone: "Interview cancelled again." });
  if (error) { assertiveRegion.textContent = error; showSnackbar(error); }
}

// undo sends the old values back
async function saveInterview(id, payload, { message = "", undone = "", offer = true, aliasFrom = null } = {}) {
  let body;
  const list = state.trackerData?.interviews;
  const place = (interview) => {
    const index = list ? list.findIndex((item) => item.id === id) : -1;
    if (index >= 0) list[index] = interview;
    rerenderRecords(interviewFocus(id));
  };
  try {
    const response = await secureWrite(`/api/v1/interviews/${id}`, { method: "PATCH", headers: jsonHeaders, body: JSON.stringify(payload) });
    body = await response.json().catch(() => ({}));
    if (response.status === 404) throw new Error("This interview was deleted in another tab.");
    // show what the other tab saved
    if (response.status === 409 && body.interview) { place(body.interview); throw new Error("It changed in another tab, so it now shows that version."); }
    if (!response.ok) throw new Error(body.error || "Couldn't save the interview. Retry shortly.");
  } catch (error) {
    return error instanceof Error ? error.message : "Couldn't save the interview. Retry shortly.";
  }
  place(body.interview);
  if (aliasFrom !== null) revisionAliases.set(`interview:${id}@${aliasFrom}`, body.interview.revision);
  if (offer) {
    const restore = Object.fromEntries(Object.keys(payload).map((name) => [name, name === "cancelled" ? Boolean(body.previous.cancelled_at) : body.previous[name]]));
    const after = body.interview.revision;
    offerUndoSnackbar(message, [`interview:${id}`], async () => {
      restore.expected_revision = currentRevision(`interview:${id}`, after);
      const error = await saveInterview(id, restore, { offer: false, aliasFrom: body.previous.revision });
      showSnackbar(error ? `Couldn't undo. ${error}` : undone);
    }, { latest: `interview:${id}` });
  }
  return "";
}

async function deleteInterview(interview) {
  const when = formatAbsolute(interview.starts_at);
  if (!await confirmDelete({
    title: `Delete the ${interview.company} interview on ${when}?`,
    body: "It leaves the tracker and the calendar export. You can undo only right after. To keep a record of it, cancel it instead.",
    trigger: document.activeElement,
  })) return;
  const list = state.trackerData?.interviews || [];
  const index = list.findIndex((item) => item.id === interview.id);
  if (index < 0) return;
  const { upcoming, past } = splitInterviews(list);
  const focus = focusAfterRemoval([...upcoming, ...past].map((item) => String(item.id)), [String(interview.id)], ".interview-list article", "data-interview-id", "#interviews-title");
  const [removed] = list.splice(index, 1);
  rerenderRecords();
  focus();
  try {
    const response = await secureWrite(`/api/v1/interviews/${interview.id}`, { method: "DELETE", headers: { Accept: "application/json" } });
    // another tab got there first
    if (response.status === 404) { showSnackbar("Already deleted in another tab."); return; }
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't delete the interview.");
  } catch (error) {
    list.splice(index, 0, removed);
    rerenderRecords(interviewFocus(interview.id));
    assertiveRegion.textContent = `${error.message} Change undone.`;
    showSnackbar(`${error.message} Change undone.`);
    return;
  }
  offerUndoSnackbar("Interview deleted.", [`interview:${interview.id}`], () => restoreInterview(interview.id, index));
}

async function restoreInterview(id, index) {
  try {
    const response = await secureWrite(`/api/v1/interviews/${id}/restore`, { method: "POST", headers: { Accept: "application/json" } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || "Couldn't restore the interview.");
    const list = state.trackerData?.interviews;
    if (list && !list.some((item) => item.id === id)) list.splice(Math.min(index, list.length), 0, body.interview);
  } catch (error) {
    showSnackbar(`Couldn't undo. ${error instanceof Error ? error.message : ""}`.trim());
    return;
  }
  rerenderRecords(interviewFocus(id));
  showSnackbar("Interview restored.");
}

function contactFocus(id) {
  return recordFocus(`.contact-row[data-contact-id="${id}"]`, "#company-contacts-title");
}

function companyContacts() {
  return state.companiesData?.company?.contacts || null;
}

function editContact(contact) {
  openEditor({
    title: `Edit ${contact.name}`,
    focusBack: contactFocus(contact.id),
    fields: [
      { name: "name", label: "Name", value: contact.name, required: true, maxlength: 500 },
      { name: "title", label: "Role", value: contact.title, optional: true, maxlength: 500 },
      { name: "email", label: "Email", type: "email", value: contact.email, optional: true, maxlength: 500 },
      { name: "linkedin_url", label: "LinkedIn URL", type: "url", inputmode: "url", value: contact.linkedin_url, optional: true, maxlength: 2000 },
    ],
    save: (changes) => saveContact(contact.id, changes, { message: "Contact updated.", undone: "Contact edit undone." }),
  });
}

async function saveContact(id, payload, { message = "", undone = "", offer = true, aliasFrom = null } = {}) {
  let body;
  const list = companyContacts();
  const place = (contact) => {
    const index = list ? list.findIndex((item) => item.id === id) : -1;
    if (index >= 0) list[index] = contact;
    rerenderRecords(contactFocus(id));
  };
  try {
    const response = await secureWrite(`/api/v1/contacts/${id}`, { method: "PATCH", headers: jsonHeaders, body: JSON.stringify(payload) });
    body = await response.json().catch(() => ({}));
    if (response.status === 404) throw new Error("This contact was deleted in another tab.");
    // show what the other tab saved
    if (response.status === 409 && body.contact) { place(body.contact); throw new Error("It changed in another tab, so it now shows that version."); }
    if (!response.ok) throw new Error(body.error || "Couldn't save the contact. Retry shortly.");
  } catch (error) {
    return error instanceof Error ? error.message : "Couldn't save the contact. Retry shortly.";
  }
  place(body.contact);
  if (aliasFrom !== null) revisionAliases.set(`contact:${id}@${aliasFrom}`, body.contact.revision);
  if (offer) {
    const restore = pickFields(body.previous, Object.keys(payload));
    const after = body.contact.revision;
    offerUndoSnackbar(message, [`contact:${id}`], async () => {
      restore.expected_revision = currentRevision(`contact:${id}`, after);
      const error = await saveContact(id, restore, { offer: false, aliasFrom: body.previous.revision });
      showSnackbar(error ? `Couldn't undo. ${error}` : undone);
    }, { latest: `contact:${id}` });
  }
  return "";
}

// counts come from linkedin rows
function shiftConnections(contact, delta) {
  const company = state.companiesData?.company;
  if (company && contact.source === "linkedin_csv") company.connections_count = Math.max(0, (company.connections_count || 0) + delta);
  if (contact.source === "linkedin_csv") jobsWriteLanded();
}

async function deleteContact(contact) {
  const list = companyContacts();
  const index = list ? list.findIndex((item) => item.id === contact.id) : -1;
  if (index < 0) return;
  const focus = focusAfterRemoval(list.map((item) => String(item.id)), [String(contact.id)], ".contact-row", "data-contact-id", ".contact-add summary");
  const [removed] = list.splice(index, 1);
  shiftConnections(removed, -1);
  rerenderRecords();
  focus();
  try {
    const response = await secureWrite(`/api/v1/contacts/${contact.id}`, { method: "DELETE", headers: { Accept: "application/json" } });
    // another tab got there first
    if (response.status === 404) { showSnackbar("Already deleted in another tab."); return; }
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Couldn't delete the contact.");
  } catch (error) {
    if (companyContacts() === list) list.splice(index, 0, removed);
    shiftConnections(removed, 1);
    rerenderRecords(contactFocus(contact.id));
    assertiveRegion.textContent = `${error.message} Change undone.`;
    showSnackbar(`${error.message} Change undone.`);
    return;
  }
  offerUndoSnackbar(`${contact.name} deleted.`, [`contact:${contact.id}`], () => restoreContact(removed, index, list));
}

async function restoreContact(contact, index, list) {
  try {
    const response = await secureWrite(`/api/v1/contacts/${contact.id}/restore`, { method: "POST", headers: { Accept: "application/json" } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || "Couldn't restore the contact.");
    // another company may be open now
    if (companyContacts() === list && !list.some((item) => item.id === contact.id)) list.splice(Math.min(index, list.length), 0, body.contact);
  } catch (error) {
    showSnackbar(`Couldn't undo. ${error instanceof Error ? error.message : ""}`.trim());
    return;
  }
  shiftConnections(contact, 1);
  rerenderRecords(contactFocus(contact.id));
  showSnackbar(`${contact.name} restored.`);
}

// --- companies ---

function companyPath(company) {
  return `/api/v1/companies/${encodeURIComponent(company.key)}`;
}

async function companyWrite(url, init, fallback) {
  try {
    const response = await secureWrite(url, { headers: jsonHeaders, ...init });
    const body = await response.json().catch(() => ({}));
    return { status: response.status, body, error: response.ok ? "" : body.error || fallback };
  } catch (error) {
    return { status: 0, body: {}, error: error instanceof Error ? error.message : fallback };
  }
}

// job cards show company names
function companiesChanged() {
  jobsWriteLanded(); trackerWriteLanded();
  if (routeRoot() === "companies") loadCompanies();
  else state.companiesData = null;
}

function findCompany(key) {
  const data = state.companiesData;
  return data?.company?.key === key ? data.company : data?.companies?.find((item) => item.key === key) || null;
}

async function companyIndex() {
  if (state.companiesData?.companies) return state.companiesData.companies;
  try {
    const response = await fetch("/api/v1/companies", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    return (await response.json()).companies;
  } catch { return null; }
}

function addCompany(trigger) {
  openEditor({
    title: "Add company", intro: "Keep contacts and notes before the scout finds a job there. Its jobs join it once found.",
    trigger,
    fields: [{ name: "name", label: "Company name", value: "", required: true, maxlength: 500, autocomplete: "organization" }],
    async save({ name }) {
      const { body, error } = await companyWrite("/api/v1/companies", { method: "POST", body: JSON.stringify({ name }) }, "Couldn't add the company. Retry shortly.");
      if (error) return error;
      // after the editor hands focus back
      setTimeout(() => {
        state.companiesData = null;
        navigate(`/companies/${encodeURIComponent(body.company.name)}`);
        showSnackbar(body.created ? `${body.company.name} added.` : `${body.company.name} is already a company, so it's open now.`);
      }, 0);
      return "";
    },
  });
}

function companyFocus(key) {
  return recordFocus(`[data-company-key="${CSS.escape(key)}"]`, "#companies-title, #company-title");
}

function renameCompany(company) {
  openEditor({
    title: `Rename ${company.name}`, intro: "The scout keeps matching its jobs, even under the old name.",
    focusBack: companyFocus(company.key),
    fields: [{ name: "name", label: "Company name", value: company.name, required: true, maxlength: 500, autocomplete: "organization" }],
    save: ({ name }) => saveCompanyName(company.key, name, { message: `Renamed to ${name}.`, undone: "Rename undone." }),
  });
}

function placeCompany(key, company) {
  const found = findCompany(key);
  if (found) Object.assign(found, { name: company.name, revision: company.revision, hidden: company.hidden });
  // the url names the company
  if (state.companiesData?.company?.key === key) {
    state.companyLoadedName = company.name;
    history.replaceState(history.state, "", `/companies/${encodeURIComponent(company.name)}`);
  }
  if (routeRoot() === "companies") renderCompanies();
}

// undo sends the old name back
async function saveCompanyName(key, name, { message = "", undone = "", offer = true, expected = null, aliasFrom = null } = {}) {
  const payload = expected === null ? { name } : { name, expected_revision: expected };
  const { status, body, error } = await companyWrite(`/api/v1/companies/${encodeURIComponent(key)}`, { method: "PATCH", body: JSON.stringify(payload) }, "Couldn't rename the company. Retry shortly.");
  if (status === 404) return "This company was merged into another in another tab.";
  // show what the other tab saved
  if (status === 409 && body.company) { placeCompany(key, body.company); return "It changed in another tab, so it now shows that version."; }
  if (error) return error;
  placeCompany(key, body.company);
  companiesChanged();
  if (aliasFrom !== null) revisionAliases.set(`company:${key}@${aliasFrom}`, body.company.revision);
  if (offer) {
    const after = body.company.revision;
    offerUndoSnackbar(message, [`company:${key}`], async () => {
      const failed = await saveCompanyName(key, body.previous.name, { offer: false, expected: currentRevision(`company:${key}`, after), aliasFrom: body.previous.revision });
      showSnackbar(failed ? `Couldn't undo. ${failed}` : undone);
    }, { latest: `company:${key}` });
  }
  return "";
}

async function pickMergeTarget(c) {
  const companies = await companyIndex();
  if (!companies) { showSnackbar("Couldn't load companies. Retry shortly."); return; }
  const source = companies.find((item) => item.key === c.company.key) || c.company;
  const targets = mergeTargets(companies, source);
  if (!targets.length) { showSnackbar("There's no other company to merge into."); return; }
  openEditor({
    title: `Merge ${source.name}`, intro: "The company you pick keeps its name, ATS account and logo.",
    focusBack: companyFocus(source.key),
    fields: [{ name: "target", label: "Merge into", value: "", required: true, options: [["", "Pick a company"], ...targets.map((item) => [item.key, item.name])] }],
    save({ target }) {
      const picked = targets.find((item) => item.key === target);
      if (!picked) return Promise.resolve("Pick a company to merge into.");
      setTimeout(() => confirmMerge(source, picked, c), 0);
      return Promise.resolve("");
    },
  });
}

async function confirmMerge(source, target, c) {
  const trigger = document.activeElement;
  if (!await confirmDelete({ ...mergeCopy(source, target), action: "Merge", trigger })) return;
  const list = state.companiesData?.companies;
  const order = list ? filterCompanies(list, { query: state.companyQuery, show: state.companyShow }).map((item) => item.key) : [];
  const { status, body, error } = await companyWrite(`${companyPath(source)}/merge`, { method: "POST", body: JSON.stringify({ target: target.key }) }, "Couldn't merge. Retry shortly.");
  if (error) {
    const message = status === 404 ? "One of these was merged into another company in another tab. Reload and retry." : error;
    assertiveRegion.textContent = message;
    showSnackbar(message);
    return;
  }
  if (c.detail) {
    state.companiesData = null;
    navigate(`/companies/${encodeURIComponent(body.company.name)}`, { replace: true });
  } else if (list) {
    // carrel: next card, else previous
    const focus = focusAfterRemoval(order, [source.key], ".company-card", "data-company-key", "[data-add-company]");
    list.splice(list.findIndex((item) => item.key === source.key), 1);
    renderCompanies();
    focus();
  }
  companiesChanged();
  const after = body.company.revision;
  offerUndoSnackbar(`Merged ${source.name} into ${target.name}.`, [`company:${target.key}`, `company:${source.key}`],
    () => undoMerge(body, source, target, after), { latest: `company:${target.key}` });
}

async function undoMerge(merged, source, target, after) {
  const expected = currentRevision(`company:${target.key}`, after);
  const { body, error } = await companyWrite(`/api/v1/company-merges/${merged.merge_id}/undo`, { method: "POST", body: JSON.stringify({ expected_revision: expected }) }, "Couldn't undo the merge.");
  if (error) { showSnackbar(`Couldn't undo. ${error}`); return; }
  // both match their pre-merge state
  revisionAliases.set(`company:${target.key}@${after - 1}`, body.target.revision);
  revisionAliases.set(`company:${source.key}@${merged.source.revision}`, body.company.revision);
  companiesChanged();
  showSnackbar(`${source.name} is its own company again.`);
}

async function setCompanyHidden(c, hidden, { offer = true } = {}) {
  const company = findCompany(c.company.key) || c.company;
  const list = state.companiesData?.companies;
  const leaving = !c.detail && list && state.companyShow !== (hidden ? "hidden" : "all");
  const order = leaving ? filterCompanies(list, { query: state.companyQuery, show: state.companyShow }).map((item) => item.key) : [];
  const focus = leaving ? focusAfterRemoval(order, [company.key], ".company-card", "data-company-key", "#company-search") : null;
  company.hidden = hidden;
  renderCompanies();
  focus?.();
  const { status, error } = await companyWrite(`${companyPath(company)}/${hidden ? "hide" : "restore"}`, { method: "POST", body: "{}" }, "Couldn't save the change.");
  // another tab got there first
  if (status === 404) { showSnackbar(hidden ? "Already hidden in another tab." : "Already shown in another tab."); return; }
  if (error) {
    company.hidden = !hidden;
    renderCompanies();
    companyFocus(company.key)();
    assertiveRegion.textContent = `${error} Change undone.`;
    showSnackbar(`${error} Change undone.`);
    return;
  }
  if (!offer) return;
  offerUndoSnackbar(hidden ? `${company.name} hidden from Companies.` : `${company.name} is back in Companies.`, [`company-hidden:${company.key}`], async () => {
    await setCompanyHidden({ ...c, company }, !hidden, { offer: false });
    if (document.activeElement === document.body) companyFocus(company.key)();
  });
}

function linkFocus(id) {
  return recordFocus(`.company-link-row[data-link-id="${id}"]`, "[data-add-company-link]");
}

function companyLinks() {
  return state.companiesData?.company?.saved_links || null;
}

function linkFields(link) {
  return [
    { name: "url", label: "URL", type: "url", inputmode: "url", value: link.url, required: true, maxlength: 2000, help: "Only https:// addresses are saved." },
    { name: "label", label: "Label", value: link.label, optional: true, maxlength: 200, help: "Shown instead of the address." },
  ];
}

function addCompanyLink(trigger) {
  const company = state.companiesData?.company;
  if (!company) return;
  openEditor({
    title: `Add a ${company.name} link`, trigger, fields: linkFields({ url: "", label: "" }),
    async save(changes) {
      const { body, error } = await companyWrite(`${companyPath(company)}/links`, { method: "POST", body: JSON.stringify({ url: changes.url, label: changes.label || "" }) }, "Couldn't add the link. Retry shortly.");
      if (error) return error;
      const list = companyLinks();
      if (list && state.companiesData.company.key === company.key) list.push(body.link);
      rerenderRecords();
      // carrel: added items take focus
      setTimeout(() => linkFocus(body.link.id)(), 0);
      offerUndoSnackbar("Link added.", [`link:${body.link.id}`], () => deleteLink(body.link, { offer: false }));
      return "";
    },
  });
}

function editLink(link) {
  openEditor({
    title: "Edit link", focusBack: linkFocus(link.id), fields: linkFields(link),
    save: (changes) => saveLink(link.id, changes, { message: "Link updated.", undone: "Link edit undone." }),
  });
}

async function saveLink(id, payload, { message = "", undone = "", offer = true, aliasFrom = null } = {}) {
  const list = companyLinks();
  const place = (link) => {
    const index = list ? list.findIndex((item) => item.id === id) : -1;
    if (index >= 0) list[index] = link;
    rerenderRecords(linkFocus(id));
  };
  const { status, body, error } = await companyWrite(`/api/v1/company-links/${id}`, { method: "PATCH", body: JSON.stringify(payload) }, "Couldn't save the link. Retry shortly.");
  if (status === 404) return "This link was deleted in another tab.";
  // show what the other tab saved
  if (status === 409 && body.link) { place(body.link); return "It changed in another tab, so it now shows that version."; }
  if (error) return error;
  place(body.link);
  if (aliasFrom !== null) revisionAliases.set(`link:${id}@${aliasFrom}`, body.link.revision);
  if (offer) {
    const restore = pickFields(body.previous, Object.keys(payload));
    const after = body.link.revision;
    offerUndoSnackbar(message, [`link:${id}`], async () => {
      restore.expected_revision = currentRevision(`link:${id}`, after);
      const failed = await saveLink(id, restore, { offer: false, aliasFrom: body.previous.revision });
      showSnackbar(failed ? `Couldn't undo. ${failed}` : undone);
    }, { latest: `link:${id}` });
  }
  return "";
}

async function deleteLink(link, { offer = true } = {}) {
  const list = companyLinks();
  const index = list ? list.findIndex((item) => item.id === link.id) : -1;
  if (index < 0) return;
  const focus = focusAfterRemoval(list.map((item) => String(item.id)), [String(link.id)], ".company-link-row", "data-link-id", "[data-add-company-link]");
  const [removed] = list.splice(index, 1);
  rerenderRecords();
  focus();
  const { status, error } = await companyWrite(`/api/v1/company-links/${link.id}`, { method: "DELETE" }, "Couldn't delete the link.");
  // another tab got there first
  if (status === 404) { showSnackbar("Already deleted in another tab."); return; }
  if (error) {
    if (companyLinks() === list) list.splice(index, 0, removed);
    rerenderRecords(linkFocus(link.id));
    assertiveRegion.textContent = `${error} Change undone.`;
    showSnackbar(`${error} Change undone.`);
    return;
  }
  if (offer) offerUndoSnackbar("Link deleted.", [`link:${link.id}`], () => restoreLink(removed, index, list));
  else showSnackbar("Link removed.");
}

async function restoreLink(link, index, list) {
  const { body, error } = await companyWrite(`/api/v1/company-links/${link.id}/restore`, { method: "POST" }, "Couldn't restore the link.");
  if (error) { showSnackbar(`Couldn't undo. ${error}`); return; }
  // another company may be open now
  if (companyLinks() === list && !list.some((item) => item.id === link.id)) list.splice(Math.min(index, list.length), 0, body.link);
  rerenderRecords(linkFocus(link.id));
  showSnackbar("Link restored.");
}

function openProfileItem(c, role) {
  if (!c.surface) return;
  focusProfileTarget(c.surface, { ref: `${c.kind}:${c.id}`, role, open: true });
}

function dropProfileItem({ key, item: dropped, to, keyboard }) {
  const item = dropped?.isConnected ? dropped : document.querySelector(`.profile-item[data-profile-ref="${CSS.escape(key)}"]`);
  const c = item && profileContext(item);
  if (!c) return null;
  moveProfileItem(c, to.index, keyboard ? { role: "[data-drag-handle]" } : { pointer: true });
  return c.surface?.querySelector(`.profile-item[data-profile-ref="${CSS.escape(key)}"]`) || null;
}

function updateQuickFillProfile(control) {
  const item = findProfileItem(control.dataset.profileKind, control.dataset.profileId);
  const property = control.dataset.profileProperty;
  if (!item || !property) return;
  item[property] = property === "competencies"
    ? control.value.split(",").map((value) => value.trim()).filter(Boolean)
    : control.value;
  const counter = control.closest(".quick-fill-edit-card")?.querySelector("small");
  if (counter && property === "body") counter.textContent = `${new Intl.NumberFormat().format(control.value.length)} characters before variables are replaced`;
  state.quickFillSaveVersion += 1;
  cancelAutosave();
  setQuickFillSaveState("Unsaved changes");
  state.quickFillSaveTimer = setTimeout(() => { state.quickFillSaveTimer = null; queueProfileSave(); }, 600);
}

function profileTyping(control) {
  const { profileKind: kind, profileId: id, profileProperty: property } = control.dataset;
  const ref = canonicalRef(`${kind}:${id}`);
  return { kind, id, property, ref, scope: `typing:${ref}:${property}` };
}

function beginProfileTyping(control) {
  const { kind, id, property, scope } = profileTyping(control);
  const item = property && findProfileItem(kind, id);
  if (item) textSessions.begin(scope, cloneJson(item[property] ?? ""));
}

// one undo per settled edit
function settleProfileTyping(control) {
  const { kind, id, property, ref, scope } = profileTyping(control);
  const before = textSessions.end(scope);
  const item = findProfileItem(kind, id);
  const change = item && textChange(before, cloneJson(item[property] ?? ""));
  if (!change) return;
  const saved = state.quickFillSaveTimer ? (cancelAutosave(), queueProfileSave()) : profileSaves.busy ? queueProfileSave(0) : Promise.resolve(null);
  const key = `profile:${ref}`;
  const entry = { message: "Quick-fill edit saved.", keys: [key], latest: key, run: () => undoProfileTyping(kind, id, property, change) };
  offerMoveSnackbar("Quick-fill edit saved.", "", entry, saved);
}

function undoProfileTyping(kind, id, property, { before, after }) {
  const item = findProfileItem(kind, id);
  if (!item) { showSnackbar("Couldn't undo. That item was deleted."); return; }
  // a newer edit owns it now
  if (textChange(after, item[property] ?? "")) { showSnackbar("Couldn't undo. It changed again since."); return; }
  const result = applyProfileOp({ type: "update", kind, id, patch: { [property]: cloneJson(before) } });
  if (result.error) { showSnackbar(`Couldn't undo. ${result.error}`); return; }
  rerenderProfile();
  saveProfileChange().then((error) => { if (!error) showSnackbar("Quick-fill edit undone."); });
}

// --- job and company details ---
// each field saves alone, in order
const contextSaves = new Map();
const contextPending = new Map();
const contextCommitted = new Map();
const contextLast = new Map();
const contextDrafts = new Map();

function commitContext(field, value) { contextCommitted.set(field.scope, { record: field.record, value }); }

function contextField(control, job) {
  const kind = control.dataset.contextKind;
  const name = kind === "override" ? control.dataset.contextKey : control.dataset.contextProperty;
  const record = kind === "override" ? `job:${job.dedupe_key}` : `company:${job.company.trim().toLowerCase()}`;
  return { kind, name, record, scope: `context:${record}:${kind}:${name}` };
}

function controlValue(control) {
  if (control.dataset.contextProperty !== "account_exists") return control.value;
  return control.value === "" ? null : control.value === "yes";
}

function contextValueIn(doc, { kind, name }) {
  if (kind === "override") return doc?.answer_overrides?.[name] ?? "";
  return doc?.company_account?.[name] ?? (name === "account_exists" ? null : "");
}

function setContextValue(doc, { kind, name }, value) {
  if (!doc) return;
  if (kind === "override") {
    doc.answer_overrides ||= {};
    if (value) doc.answer_overrides[name] = value; else delete doc.answer_overrides[name];
    return;
  }
  doc.company_account ||= { account_exists: null, sign_in_email: "", password_manager_url: "" };
  doc.company_account[name] = value;
}

const contextKeyOf = (job) => `${job.dedupe_key}\u0000${job.company}`;

// only the details on screen
function placeContextValue(job, field, value, { saved = true } = {}) {
  if (state.quickFillContextKey !== contextKeyOf(job)) return;
  setContextValue(state.quickFillProfile, field, value);
  if (saved) setContextValue(state.quickFillSavedContext, field, value);
}

function updateQuickFillContext(control) {
  if (!state.quickFillProfile) return;
  const job = currentJob();
  if (!job) return;
  const field = contextField(control, job);
  const value = controlValue(control);
  if (!contextCommitted.has(field.scope)) commitContext(field, contextValueIn(state.quickFillSavedContext, field));
  placeContextValue(job, field, value, { saved: false });
  clearTimeout(contextDrafts.get(field.scope)?.timer);
  const run = () => { contextDrafts.delete(field.scope); return queueContextField(job, field, value); };
  contextDrafts.set(field.scope, { timer: setTimeout(run, 600), run });
  setQuickFillSaveState("Unsaved changes");
}

// resolves to the outcome
function queueContextField(job, field, value, expected = undefined) {
  contextPending.set(field.record, (contextPending.get(field.record) || 0) + 1);
  const run = (contextSaves.get(field.record) || Promise.resolve()).then(() => sendContextField(job, field, value, expected));
  contextSaves.set(field.record, run);
  contextLast.set(field.scope, run);
  run.then(() => contextPending.set(field.record, contextPending.get(field.record) - 1));
  return run;
}

async function sendContextField(job, field, value, expected) {
  // the last committed value
  const prior = expected === undefined ? contextCommitted.get(field.scope)?.value : expected;
  const undoing = expected !== undefined;
  setQuickFillSaveState("Saving…");
  const { status, body, error } = await companyWrite("/api/v1/profile/context", { method: "PUT", body: JSON.stringify({
    dedupe_key: job.dedupe_key, company: job.company,
    field: { kind: field.kind, name: field.name, value, ...(prior === undefined ? {} : { expected: prior }) },
  }) }, "Job-specific Quick-fill details could not be saved.");
  if (status === 409) {
    commitContext(field, body.value);
    placeContextValue(job, field, body.value);
    rerenderProfile();
    setQuickFillSaveState("Reloaded");
    if (!undoing) showSnackbar("That detail changed elsewhere, so it now shows that version.");
    return { conflict: true, value: body.value };
  }
  if (error) {
    if (!undoing) placeContextValue(job, field, prior);
    rerenderProfile();
    setQuickFillSaveState("Not saved");
    if (!undoing) { assertiveRegion.textContent = `${error} Change undone.`; showSnackbar(`${error} Change undone.`); }
    return { error };
  }
  // write echoes it, skip reread
  const saved = body.value;
  commitContext(field, saved);
  placeContextValue(job, field, saved);
  setQuickFillSaveState("Saved automatically");
  return { saved };
}

function beginContextTyping(control) {
  const job = currentJob();
  if (job) textSessions.begin(contextField(control, job).scope, controlValue(control));
}

// one undo per settled field
async function settleContextTyping(control) {
  const job = currentJob();
  if (!job) return;
  const field = contextField(control, job);
  const before = textSessions.end(field.scope);
  const draft = contextDrafts.get(field.scope);
  if (draft) { clearTimeout(draft.timer); draft.run(); }
  const outcome = await contextLast.get(field.scope);
  if (!outcome || outcome.conflict || outcome.error) return;
  const change = textChange(before, contextCommitted.get(field.scope)?.value);
  if (!change) return;
  offerUndoSnackbar("Quick-fill detail saved.", [field.scope], () => undoContextField(job, field, change), { latest: field.scope });
}

async function undoContextField(job, field, { before, after }) {
  const outcome = await queueContextField(job, field, before, after);
  if (outcome.conflict) showSnackbar("Couldn't undo. That detail changed elsewhere, so it now shows that version.");
  else if (outcome.error) showSnackbar(`Couldn't undo. ${outcome.error}`);
  else { rerenderProfile(); showSnackbar("Quick-fill detail change undone."); }
}

// a reload knows the latest
function forgetCommittedContext() {
  for (const [scope, { record }] of [...contextCommitted]) if (!contextPending.get(record)) contextCommitted.delete(scope);
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
  let candidate;
  try {
    const parsed = JSON.parse(await file.text());
    candidate = profileDocument(parsed.profile && typeof parsed.profile === "object" ? parsed.profile : parsed);
    candidate.documents = mergeDocuments(previous.documents, candidate.documents.filter((row) => row && typeof row === "object"));
  } catch {
    setQuickFillSaveState("Import failed");
    showSnackbar("The profile file isn't valid JSON.");
    return;
  }
  Object.assign(state.quickFillProfile, candidate);
  state.quickFillSaveVersion += 1;
  state.quickFillImportVersion = state.quickFillSaveVersion; // later edits send a plain save
  cancelAutosave();
  if (await queueProfileSave()) return;
  renderQuickFill();
  offerUndoSnackbar("Quick-fill JSON imported.", ["quick-fill-profile"], async () => {
    Object.assign(state.quickFillProfile, previous);
    state.quickFillSaveVersion += 1;
    renderQuickFill();
    if (!await queueProfileSave()) showSnackbar("Import undone.");
  });
}

function renderQuickFill() {
  if (!state.quickFillEnabled) return;
  keepSurface(quickFillSheet, () => { quickFillSheet.innerHTML = quickFillInnerMarkup(); });
  quickFillSheet.setAttribute("aria-hidden", state.quickFillOpen ? "false" : "true");
  quickFillTrigger.setAttribute("aria-expanded", String(state.quickFillOpen));
  quickFillTrigger.setAttribute("aria-label", `${state.quickFillOpen ? "Close" : "Open"} Quick-fill`);
  if (state.quickFillPopout && !state.quickFillPopout.closed) {
    const popoutSheet = state.quickFillPopout.document.querySelector("#quick-fill-sheet");
    if (popoutSheet) keepSurface(popoutSheet, () => { popoutSheet.innerHTML = quickFillInnerMarkup({ popout: true }); });
  }
}

function renderSessionQuickFill() {
  const surface = document.querySelector("#session-quick-fill");
  if (!surface || !state.quickFillEnabled || !state.quickFillProfile) return;
  keepSurface(surface, () => { surface.innerHTML = quickFillInnerMarkup({ embedded: true }); });
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
    forgetCommittedContext();
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

// The async Clipboard API refuses without a secure context or a focused
// document. execCommand("copy") on a selected textarea still works from a click.
function copyTextFallback(text) {
  const area = document.createElement("textarea");
  area.className = "clipboard-fallback";
  area.value = text;
  area.setAttribute("readonly", "");
  document.body.append(area);
  area.select();
  let copied = false;
  try { copied = document.execCommand("copy"); } catch { /* Fall through to manual copy. */ }
  if (!copied) return area;
  area.remove();
  return null;
}

async function copyQuickFillItem(index) {
  const item = state.quickFillItems[index];
  if (!item) return;
  try {
    await navigator.clipboard.writeText(item.value);
  } catch {
    const manual = copyTextFallback(item.value);
    if (manual) {
      manual.setAttribute("aria-label", `${item.label}; press Control C to copy`);
      showSnackbar("Couldn't copy. Text selected.");
      setTimeout(() => manual.remove(), 6000);
      return;
    }
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
      noticeHidden(response);
      if (response.ok) return;
      state.copiedByJob.get(jobKey)?.delete(item.key); renderQuickFill();
      showSnackbar((await response.json().catch(() => ({}))).error || "Copy marker could not be saved.");
    }).catch(() => {
      state.copiedByJob.get(jobKey)?.delete(item.key); renderQuickFill(); showSnackbar("Copy marker could not be saved.");
    });
  }
  setTimeout(() => { if (state.copiedKey === item.key) { state.copiedKey = ""; renderQuickFill(); } }, 1500);
}

const popoutBound = new WeakSet();

async function popOutQuickFill() {
  if (!state.quickFillEnabled) return;
  let target;
  try {
    if (window.documentPictureInPicture?.requestWindow) target = await window.documentPictureInPicture.requestWindow({ width: 380, height: 640 });
    else target = window.open("", "jobseer-quick-fill", "popup,width=380,height=640");
  } catch { target = window.open("", "jobseer-quick-fill", "popup,width=380,height=640"); }
  if (!target) { showSnackbar("The browser blocked the Quick-fill window."); return; }
  target.document.head.innerHTML = `<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Quick-fill · JobSeer</title><link rel="stylesheet" href="/static/tokens.css"><link rel="stylesheet" href="/static/app.css">`;
  target.document.body.className = "quick-fill-popout";
  target.document.body.innerHTML = `<aside class="quick-fill-sheet" id="quick-fill-sheet" aria-label="Quick-fill" aria-hidden="false">${quickFillInnerMarkup({ popout: true })}</aside>`;
  state.quickFillPopout = target;
  bindQuickFillSurface(target.document.body, { popout: true });
  // a reused window keeps its listeners
  if (!popoutBound.has(target.document)) {
    popoutBound.add(target.document);
    bindObjectMenus(resolveObjectMenu, target.document);
    bindProfileDrag(target.document);
  }
  target.addEventListener("pagehide", () => { if (state.quickFillPopout === target) state.quickFillPopout = null; }, { once: true });
}

function renderCommands() {
  commandMatches = rankCommands(paletteEntries, commandInput.value);
  commandSelection = Math.min(commandSelection, Math.max(0, commandMatches.length - 1));
  commandResults.innerHTML = commandMatches.length ? commandMatches.map((command, index) => `<button class="command-item interactive" type="button" role="option" aria-selected="${index === commandSelection}" data-command="${escapeHtml(command.id)}">${icons[command.icon || command.id] || icons.shortcuts}<span><strong>${escapeHtml(command.label)}</strong><small>${escapeHtml(command.detail)}</small></span>${command.shortcut ? `<kbd>${escapeHtml(command.shortcut)}</kbd>` : ""}</button>`).join("") : '<p class="command-empty">No matching commands</p>';
  commandResults.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function rememberDialogTrigger(trigger) { dialogTrigger = trigger instanceof HTMLElement ? trigger : document.activeElement; }
function restoreDialogTrigger() { if (dialogTrigger instanceof HTMLElement && dialogTrigger.isConnected) dialogTrigger.focus(); dialogTrigger = null; }

function openCommand(trigger) {
  if (commandDialog.open) return;
  if (shortcutDialog.open) shortcutDialog.close();
  if (filterDialog.open) filterDialog.close();
  rememberDialogTrigger(trigger);
  // closures bind the subject now
  paletteEntries = [...subjectCommands(dialogTrigger), ...commands];
  commandInput.value = ""; commandSelection = 0; renderCommands(); commandDialog.showModal(); commandInput.focus();
}

// the focused object, else the open job
function subjectCommands(trigger) {
  let found = trigger instanceof Element ? objectMenuAt(trigger) : null;
  const job = routeRoot() === "inbox" && state.jobs.find((item) => item.dedupe_key === state.selectedKey);
  if (!found && job) {
    const el = document.querySelector(`#job-list-layer .job-row[data-job-key="${CSS.escape(job.dedupe_key)}"]`);
    found = { type: objectMenus.find((type) => type.selector.startsWith(".job-row")), context: { el, key: job.dedupe_key, job, bulk: false } };
  }
  if (!found) return [];
  return menuCommands(menuItemsFor(found.type, found.context, { palette: true }), { prefix: "subject", detail: found.type.label(found.context), format: formatShortcut });
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
  const tags = tagOptions(state.jobs);
  tagGroup.hidden = !tags.length && !state.tag;
  tagOptionsList.innerHTML = `<label class="check-row"><input type="radio" name="tag" value="" ${state.tag ? "" : "checked"}><span>Any tag</span></label>${tags.map((tag) => `<label class="check-row"><input type="radio" name="tag" value="${escapeHtml(tag)}" ${hasTag({ tags: [tag] }, state.tag) ? "checked" : ""}><span>${escapeHtml(tag)}</span></label>`).join("")}`;
  savedViewName.value = "";
  filterDialog.showModal();
  sourceOptions.querySelector("input:checked")?.focus();
}

function runCommand(id) {
  const command = paletteEntries.find((item) => item.id === id);
  if (!command) return;
  // after focus returns to the trigger
  commandDialog.addEventListener("close", () => {
    if (command.run) command.run();
    else if (command.route) navigate(command.route);
    else if (command.action === "shortcuts") openShortcuts(document.querySelector("[data-open-shortcuts]"));
    else if (command.action === "capture") openCapture();
    else if (command.action === "new-view") newSavedView();
  }, { once: true });
  commandDialog.close();
}

function newSavedView() {
  if (routeRoot() !== "inbox") navigate(inboxUrl(""));
  openFilters(document.querySelector("[data-open-filters]"));
  savedViewName.focus();
}

async function copyJobField(name, value) {
  try {
    await navigator.clipboard.writeText(value);
    showSnackbar(`Copied the ${name}.`);
  } catch {
    showSnackbar("Couldn't copy. Check clipboard permission and retry.");
  }
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
    jobsWriteLanded();
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
  const reduceMotion = document.documentElement.dataset.motion === "reduce";
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
  const key = row.dataset.jobKey;
  if (event.shiftKey && state.rangeAnchor) {
    state.selectedKeys = rangeSelection(filteredJobs().map((job) => job.dedupe_key), state.rangeBase, state.rangeAnchor, key);
    state.selectedKey = key; syncInboxUrl(); renderInbox(); return;
  }
  if (state.copySelectionMode || event.ctrlKey || event.metaKey) {
    toggleInboxKey(key);
    state.selectedKey = key; syncInboxUrl(); renderInbox(); return;
  }
  state.selectedKeys.clear(); anchorInbox(key); syncVisibleJobSelection(); selectJob(key);
}

// shift extends from here
function anchorInbox(key) {
  state.rangeAnchor = key;
  state.rangeBase = new Set(state.selectedKeys);
}

function toggleInboxKey(key) {
  state.selectedKeys = toggleKey(state.selectedKeys, key);
  anchorInbox(key);
}

// virtual rows mount on scroll
function revealRow(key) {
  const viewport = document.querySelector("#job-viewport");
  const entry = state.entries.find((item) => item.type === "job" && item.job.dedupe_key === key);
  if (!viewport || !entry) return null;
  if (entry.offset < viewport.scrollTop) viewport.scrollTop = entry.offset;
  else if (entry.offset + entry.height > viewport.scrollTop + viewport.clientHeight) viewport.scrollTop = entry.offset + entry.height - viewport.clientHeight;
  state.scrollTop = viewport.scrollTop;
  renderVirtualRows();
  return viewport.querySelector(`.job-row[data-job-key="${CSS.escape(key)}"]`);
}

function listHasFocus() {
  const active = document.activeElement;
  return !active || active === document.body || Boolean(active.closest?.(".job-row, #job-viewport"));
}

function extendInbox(delta) {
  const keys = filteredJobs().map((job) => job.dedupe_key);
  const key = stepKey(keys, state.selectedKey, delta);
  if (!key) return;
  if (!state.rangeAnchor || !keys.includes(state.rangeAnchor)) anchorInbox(state.selectedKey || key);
  state.selectedKeys = rangeSelection(keys, state.rangeBase, state.rangeAnchor, key);
  state.selectedKey = key; syncInboxUrl({ replace: true }); renderSelectedJob();
  revealRow(key)?.focus({ preventScroll: true });
}

function toggleFocusedInbox() {
  if (!state.selectedKey) return;
  toggleInboxKey(state.selectedKey);
  syncVisibleJobSelection();
}

function selectAllInbox() {
  const keys = filteredJobs().map((job) => job.dedupe_key);
  if (!keys.length) return;
  state.selectedKeys = selectAllVisible(state.selectedKeys, keys);
  syncVisibleJobSelection();
  announce(`${new Intl.NumberFormat().format(state.selectedKeys.size)} selected.`);
}

// --- queue and tracker selection ---

const rowLists = {
  queue: { rows: ".queue-list > .queue-card[data-queue-key]", attr: "queueKey", after: ".queue-list" },
  // board walks column by column
  tracker: { rows: ".tracker-table tbody tr[data-tracker-key], .board-card[data-tracker-key]", attr: "trackerKey", after: ".tracker-table-wrap, .tracker-board" },
};

function rowSelectMarkup(name, job) {
  return `<label class="row-select"><input type="checkbox" data-row-select="${name}" value="${escapeHtml(job.dedupe_key)}" aria-label="Select ${escapeHtml(job.title)} at ${escapeHtml(job.company)}" tabindex="-1"></label>`;
}

function rowKeys(name) {
  return [...document.querySelectorAll(rowLists[name].rows)].map((row) => row.dataset[rowLists[name].attr]);
}

// in queue order, not click order
function selectedRows(name) {
  const picked = state.rowSelection[name].keys;
  return rowKeys(name).filter((key) => picked.has(key));
}

function activeRowList() {
  if (routeRoot() === "queue" && document.querySelector(".queue-list")) return "queue";
  if (routeRoot() === "tracker" && document.querySelector(".tracker-table, .tracker-board")) return "tracker";
  return "";
}

// a rerender keeps the focused box
function heldRowSelect() {
  const active = document.activeElement;
  return active?.matches?.("[data-row-select]") ? active.value : "";
}

function focusRowSelect(name, key) {
  const box = [...document.querySelectorAll(`[data-row-select="${name}"]`)].find((item) => item.value === key);
  box?.focus({ preventScroll: true });
  box?.closest(rowLists[name].rows)?.scrollIntoView({ block: "nearest" });
}

function syncRowSelection(name) {
  const selection = state.rowSelection[name];
  const keys = rowKeys(name);
  selection.keys = pruneKeys(selection.keys, keys); // filters hide rows
  if (!keys.includes(selection.focus)) selection.focus = keys[0] || "";
  for (const row of document.querySelectorAll(rowLists[name].rows)) {
    const key = row.dataset[rowLists[name].attr];
    const box = row.querySelector("[data-row-select]");
    const on = selection.keys.has(key);
    row.classList.toggle("is-selected", on);
    if (box) { box.checked = on; box.tabIndex = key === selection.focus ? 0 : -1; }
  }
  const all = document.querySelector(`[data-row-select-all="${name}"]`);
  if (all) { all.checked = keys.length > 0 && selection.keys.size === keys.length; all.indeterminate = selection.keys.size > 0 && !all.checked; }
  const old = document.querySelector(`[data-bulk-bar="${name}"]`);
  const markup = selection.keys.size ? bulkBarShell(name, selection.keys.size, name === "queue"
    ? ["queue-apply", "queue-save", "queue-top", "queue-bottom", "queue-tags", "queue-hide"]
    : ["tracker-status", "tracker-tags", "tracker-hide"]) : "";
  if (old && markup) old.outerHTML = markup;
  else if (old) old.remove();
  else if (markup) document.querySelector(rowLists[name].after)?.insertAdjacentHTML("afterend", markup);
  mountBulkBars();
}

function setRowSelection(name, keys, focus) {
  const selection = state.rowSelection[name];
  selection.keys = keys;
  if (focus) selection.focus = focus;
  syncRowSelection(name);
}

function anchorRows(name, key) {
  const selection = state.rowSelection[name];
  selection.anchor = key;
  selection.base = new Set(selection.keys);
}

function pickRow(name, key, event, fromBox) {
  const selection = state.rowSelection[name];
  const keys = rowKeys(name);
  if (event.shiftKey && keys.includes(selection.anchor)) setRowSelection(name, rangeSelection(keys, selection.base, selection.anchor, key), key);
  else {
    const toggle = fromBox || event.ctrlKey || event.metaKey;
    setRowSelection(name, toggle ? toggleKey(selection.keys, key) : new Set([key]), key);
    anchorRows(name, key);
  }
  if (!fromBox) focusRowSelect(name, key);
}

function stepRow(name, move, extend) {
  const selection = state.rowSelection[name];
  const keys = rowKeys(name);
  const next = move === "start" ? keys[0] : move === "end" ? keys.at(-1) : stepKey(keys, selection.focus, move);
  if (!next) return;
  if (extend) {
    if (!keys.includes(selection.anchor)) anchorRows(name, selection.focus || next);
    setRowSelection(name, rangeSelection(keys, selection.base, selection.anchor, next), next);
  } else {
    selection.focus = next;
    syncRowSelection(name);
    anchorRows(name, next);
  }
  focusRowSelect(name, next);
}

function selectAllRows(name) {
  setRowSelection(name, selectAllVisible(state.rowSelection[name].keys, rowKeys(name)));
  announce(`${new Intl.NumberFormat().format(state.rowSelection[name].keys.size)} selected.`);
}

function clearRows(name) {
  const selection = state.rowSelection[name];
  if (!name || !selection.keys.size) return false;
  const inBar = document.querySelector(`[data-bulk-bar="${name}"]`)?.contains(document.activeElement);
  setRowSelection(name, new Set());
  anchorRows(name, selection.focus);
  if (inBar) focusRowSelect(name, selection.focus); // the bar is gone
  return true;
}

function setStatusEverywhere(key, status) {
  for (const job of [state.jobs.find((item) => item.dedupe_key === key), state.trackerData?.applications?.find((item) => item.dedupe_key === key)]) {
    if (job) job.status = status;
  }
}

// one undo entry for the batch
async function bulkStatus(keys, status, { write = patchJob, message, undone, after = null }) {
  const jobs = keys.map(trackedJob).filter((job) => job && job.status !== status && !state.pending.has(job.dedupe_key));
  if (!jobs.length) return false;
  const previous = jobs.map((job) => ({ job, status: job.status, now: status }));
  jobs.forEach((job) => { state.pending.add(job.dedupe_key); state.statusSaving.add(job.dedupe_key); setStatusEverywhere(job.dedupe_key, status); });
  renderJobSurface();
  const results = await Promise.allSettled(jobs.map((job) => write(job, status)));
  jobs.forEach((job) => { state.pending.delete(job.dedupe_key); state.statusSaving.delete(job.dedupe_key); });
  results.forEach((result, index) => { if (result.status === "fulfilled") previous[index].now = result.value?.status || status; });
  const failed = results.find((result) => result.status === "rejected");
  if (failed) {
    const landed = previous.filter((entry, index) => results[index].status === "fulfilled");
    previous.forEach((entry, index) => { if (results[index].status === "rejected") setStatusEverywhere(entry.job.dedupe_key, entry.status); });
    const { restored, kept } = await restoreStatuses(landed);
    const slotError = await placeRestored(restored, after);
    trackerWriteLanded(); renderJobSurface();
    const reason = failed.reason?.message || "Couldn't save the change.";
    // a half-undone batch must say so
    const gaps = [kept.length ? `Couldn't put back ${jobNames(kept)}.` : "", slotError ? "Their statuses are back but their queue order isn't." : ""].filter(Boolean);
    const text = gaps.length ? `${reason} ${gaps.join(" ")} Reloaded the latest.` : `${reason} Change undone.`;
    if (gaps.length) reloadJobs();
    assertiveRegion.textContent = text; showSnackbar(text);
    return false;
  }
  previous.forEach((entry) => setStatusEverywhere(entry.job.dedupe_key, entry.now));
  trackerWriteLanded(); renderJobSurface();
  offerUndoSnackbar(message(jobs.length), jobs.map((job) => job.dedupe_key), () => undoBulkStatus(previous, undone, after));
  return true;
}

// only jobs nobody moved since
async function restoreStatuses(entries) {
  // status only, newer notes stay
  const results = await Promise.allSettled(entries.map((entry) => patchJob(entry.job, entry.status, null, { expected: entry.now })));
  const restored = [], changed = [], kept = [];
  results.forEach((result, index) => {
    const entry = entries[index];
    if (result.status === "fulfilled") { restored.push(entry); setStatusEverywhere(entry.job.dedupe_key, result.value.status); }
    else if (result.reason?.conflict) { changed.push(entry); kept.push(entry); setStatusEverywhere(entry.job.dedupe_key, result.reason.conflict); }
    else { kept.push(entry); setStatusEverywhere(entry.job.dedupe_key, entry.now); }
  });
  return { restored, changed, kept };
}

// resolves to the save error
async function placeRestored(restored, after) {
  if (!restored.length || !after) return null;
  return after(restored.map((entry) => entry.job.dedupe_key));
}

function jobNames(entries) {
  return companyLabel(entries.map((entry) => `“${entry.job.title}”`));
}

async function undoBulkStatus(previous, undone, after) {
  previous.forEach((entry) => setStatusEverywhere(entry.job.dedupe_key, entry.status));
  renderJobSurface();
  const { restored, changed, kept } = await restoreStatuses(previous);
  trackerWriteLanded();
  const slotError = await placeRestored(restored, after);
  renderJobSurface();
  const failed = kept.filter((entry) => !changed.includes(entry));
  if (failed.length || slotError) reloadJobs();
  const notes = [
    changed.length ? `Skipped ${jobNames(changed)}, ${changed.length === 1 ? "it" : "they"} changed since.` : "",
    failed.length ? `Couldn't put back ${jobNames(failed)}.` : "",
    slotError ? "Their old queue order didn't save." : "",
    failed.length || slotError ? "Reloaded the latest." : "",
  ].filter(Boolean).join(" ");
  if (!notes) { showSnackbar(undone); return; }
  const text = restored.length ? `${undone} ${notes}` : `Nothing undone. ${notes}`;
  assertiveRegion.textContent = text; showSnackbar(text);
}

function rolesLabel(count) {
  return count === 1 ? "1 role" : `${new Intl.NumberFormat().format(count)} roles`;
}

function dequeueRows(keys) {
  const order = queueJobs().map((job) => job.dedupe_key);
  return bulkStatus(keys, "saved", {
    message: (count) => `Saved ${rolesLabel(count)} for later.`,
    undone: "Back in the queue.",
    after: (restored) => restoreQueueSlots(order, restored),
  });
}

// leaving the queue drops its slot
function restoreQueueSlots(order, restored) {
  const current = queueJobs().map((job) => job.dedupe_key);
  const next = restoreSlots(order, current, new Set(restored.filter((key) => current.includes(key))));
  if (!queueSaver.busy) queueSaver.reset(current);
  applyQueueOrder(next);
  arrangeQueue();
  return queueSaver.save(next);
}

function moveRowsToEdge(keys, edge) {
  const order = queueJobs().map((job) => job.dedupe_key);
  const next = moveGroup(order, new Set(keys), edge);
  const touched = order.filter((key, index) => next[index] !== key);
  if (!touched.length) { showSnackbar(`Already at the ${edge}.`); return; }
  if (!queueSaver.busy) queueSaver.reset(order);
  applyQueueOrder(next);
  arrangeQueue();
  syncRowSelection("queue");
  const message = `Moved ${rolesLabel(keys.length)} to the ${edge}.`;
  announce(message);
  offerMoveSnackbar(message, "queue-move", { message: "Queue move", keys: touched, run: () => undoQueueMove(order, touched) }, queueSaver.save(next));
}

function hideRows(name, trigger) {
  const keys = selectedRows(name);
  const order = rowKeys(name);
  const focus = name === "queue"
    ? focusAfterRemoval(order, keys, ".queue-card", "data-queue-key", "#queue-title")
    : focusAfterRemoval(order, keys, state.trackerView === "board" ? ".board-card" : ".tracker-table tr", "data-tracker-key", "#tracker-title");
  hideJobs(keys, { trigger, focus });
}

const eligibilityGated = new Set(["queued", "applying"]);

function trackerStatusItems(keys) {
  const jobs = keys.map(trackedJob).filter(Boolean);
  return statusOptions.map((status) => ({
    label: `Move to ${statusLabel(status)}`,
    disabled: jobs.every((job) => job.status === status),
    run: () => bulkTrackerStatus(keys, status),
  }));
}

async function bulkTrackerStatus(keys, status) {
  let targets = keys;
  let note = "";
  // same split as inbox queueing
  if (eligibilityGated.has(status)) {
    await Promise.all(keys.map((key) => loadEligibility(key, { force: Boolean(state.eligibility.get(key)?.error) })));
    const { eligible, blocked, unknown } = splitEligible(keys, (key) => eligibilityVerdict(state.eligibility.get(key), state.quickFillEnabled));
    const format = (value) => new Intl.NumberFormat().format(value);
    note = [blocked.length ? `Skipped ${format(blocked.length)} that failed the eligibility check.` : "", unknown.length ? `Skipped ${format(unknown.length)} that couldn't be checked.` : ""].filter(Boolean).join(" ");
    if (!eligible.length) { showSnackbar(`Nothing moved. ${note}`); return; }
    targets = eligible;
  }
  const write = (job, next) => writeTrackerStatus(job.dedupe_key, state.jobs.find((item) => item.dedupe_key === job.dedupe_key) || job, state.trackerData?.applications?.find((item) => item.dedupe_key === job.dedupe_key), next);
  const moved = await bulkStatus(targets, status, {
    write,
    message: (count) => `Moved ${rolesLabel(count)} to ${statusLabel(status)}.${note ? ` ${note}` : ""}`,
    undone: "Status change undone.",
  });
  if (moved) await loadTrackerData({ background: true });
}

function clearInboxSelection() {
  if (!state.selectedKeys.size && !state.copySelectionMode) return false;
  state.selectedKeys.clear(); state.copySelectionMode = false; anchorInbox(state.selectedKey);
  renderInbox();
  (document.querySelector('#job-list-layer .job-row[tabindex="0"]') || document.querySelector(".inbox-list h1"))?.focus({ preventScroll: true });
  return true;
}

// null status or notes keeps it
async function patchJob(job, status, notes = null, { notesOnly = false, expected = null, expectedNotes = null } = {}) {
  const response = await secureWrite(`/api/v1/jobs/${encodeURIComponent(job.dedupe_key)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      ...(status === null ? {} : { status }), ...(notes === null ? {} : { notes }),
      ...(expected ? { expected_status: expected } : {}), ...(expectedNotes === null ? {} : { expected_notes: expectedNotes }),
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    // lets undo skip newer moves
    throw Object.assign(new Error(body.error || "The change could not be saved."), {
      conflict: expected && response.status === 409 ? body.status : "",
      newerNotes: expectedNotes !== null && response.status === 409 ? body.notes ?? null : null,
    });
  }
  if (!notesOnly) jobsWriteLanded(); // notes dont move jobs
  return response.json();
}

function nextKeyAfter(keys) {
  const before = filteredJobs();
  const current = Math.max(0, before.findIndex((job) => job.dedupe_key === state.selectedKey));
  const remaining = before.filter((job) => !keys.includes(job.dedupe_key));
  return remaining[Math.min(current, remaining.length - 1)]?.dedupe_key || "";
}

async function performDecision(status, label, targets = null, { note = "", scope = "" } = {}) {
  const keys = targets || (state.selectedKeys.size ? [...state.selectedKeys] : [state.selectedKey]);
  const jobs = keys.map((key) => state.jobs.find((job) => job.dedupe_key === key)).filter(Boolean);
  if (!jobs.length || jobs.some((job) => state.pending.has(job.dedupe_key))) return;
  const previous = jobs.map((job) => ({ key: job.dedupe_key, status: job.status }));
  const nextKey = nextKeyAfter(keys);
  jobs.forEach((job) => { state.pending.add(job.dedupe_key); job.status = status; });
  state.selectedKeys.clear(); state.selectedKey = nextKey || filteredJobs()[0]?.dedupe_key || ""; syncInboxUrl(); renderInbox();
  const results = await Promise.allSettled(jobs.map((job) => patchJob(job, status)));
  const failed = results.some((result) => result.status === "rejected");
  if (failed) {
    previous.forEach((snapshot) => { const job = state.jobs.find((candidate) => candidate.dedupe_key === snapshot.key); if (job) job.status = snapshot.status; });
    await Promise.allSettled(results.map((result, index) => result.status === "fulfilled" ? patchJob(jobs[index], previous[index].status, null, { expected: result.value.status }) : Promise.resolve()));
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
    const undo = { previous, next: jobs.map((job) => ({ key: job.dedupe_key, status: job.status })) };
    const noun = jobs.length === 1 ? "role" : `${new Intl.NumberFormat().format(jobs.length)} roles`;
    const closed = results.filter((result) => result.status === "fulfilled" && result.value.status === "archived" && result.value.liveness?.status === "closed").length;
    const message = closed ? `${closed === 1 ? "Posting" : `${closed} postings`} closed and skipped.` : `${label} ${noun}${scope}.`;
    offerUndoSnackbar(note ? `${message} ${note}` : message, previous.map((snapshot) => snapshot.key), () => undoDecision(undo));
  }
  jobs.forEach((job) => state.pending.delete(job.dedupe_key));
  if (routeRoot() === "inbox") renderInbox();
}

async function queueSelection() {
  const jobs = state.jobs.filter((job) => state.selectedKeys.has(job.dedupe_key));
  await Promise.all(jobs.map((job) => loadEligibility(job.dedupe_key, { force: Boolean(state.eligibility.get(job.dedupe_key)?.error) })));
  const { eligible, blocked, unknown } = splitEligible(jobs, (job) => eligibilityVerdict(state.eligibility.get(job.dedupe_key), state.quickFillEnabled));
  const format = (value) => new Intl.NumberFormat().format(value);
  const note = [blocked.length ? `Skipped ${format(blocked.length)} that failed the eligibility check.` : "", unknown.length ? `Skipped ${format(unknown.length)} that couldn't be checked.` : ""].filter(Boolean).join(" ");
  if (!eligible.length) { showSnackbar(`Nothing queued. ${note}`); return; }
  await performDecision("queued", "Queued", eligible.map((job) => job.dedupe_key), { note });
}

function dismissSelectedCompanies() {
  const { companies, targets } = companyDismissTargets(state.jobs, state.selectedKeys);
  const where = companyLabel(companies);
  if (!targets.length) { showSnackbar(`No unapplied roles to dismiss at ${where}.`); return; }
  performDecision("archived", "Dismissed", targets.map((job) => job.dedupe_key), { scope: ` at ${where}` });
}

function applyToJob(job) {
  if (jobHasActiveBlocker(job)) { showSnackbar("Override the eligibility verdict before applying."); return; }
  window.open(applicationUrl(job), "_blank", "noopener");
  performDecision("applying", "Opened", [job.dedupe_key]);
}

// undo can fire from any view
function refreshJobView() {
  if (routeRoot() === "inbox") renderInbox();
  else if (routeRoot() === "queue" && !window.location.pathname.startsWith("/queue/session/")) renderQueue();
  else if (routeRoot() === "tracker") loadTrackerData({ background: true });
}

// same conditional write as bulk undo
async function undoDecision(undo) {
  const landed = new Map(undo.next.map((snapshot) => [snapshot.key, snapshot.status]));
  const entries = undo.previous.map((snapshot) => ({ job: state.jobs.find((candidate) => candidate.dedupe_key === snapshot.key), status: snapshot.status, now: landed.get(snapshot.key) })).filter((entry) => entry.job);
  if (routeRoot() === "inbox") { state.selectedKey = undo.previous[0]?.key || state.selectedKey; syncInboxUrl(); }
  await undoBulkStatus(entries, "Decision undone.", null);
}

// in order, last typing wins
const noteSaves = new Map();

function queueNotes(key, value) {
  const run = (noteSaves.get(key) || Promise.resolve()).then(() => saveNotes(key, value));
  noteSaves.set(key, run);
  return run;
}

async function saveNotes(key, value) {
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  if (!job) return;
  const oldNotes = job.notes || "";
  job.notes = value;
  const notesState = document.querySelector("#notes-state");
  if (notesState) notesState.textContent = "Saving…";
  try {
    // notes only, newer moves stay
    await patchJob(job, null, value, { notesOnly: true });
    if (state.selectedKey === key && document.querySelector("#notes-state")) document.querySelector("#notes-state").textContent = "Saved automatically";
  } catch (error) {
    job.notes = oldNotes; assertiveRegion.textContent = `${error.message} Note restored.`; showSnackbar(`${error.message} Note restored.`);
    if (state.selectedKey === key) { renderInbox(); document.querySelector("#job-notes")?.focus(); }
  }
}

// a blur commits the typing
async function settleNotes(key) {
  const before = textSessions.end(`notes:${key}`);
  if (state.notesDraft?.key === key) {
    clearTimeout(state.notesTimer);
    queueNotes(key, state.notesDraft.value);
    state.notesDraft = null;
  }
  await noteSaves.get(key);
  const change = textChange(before, state.jobs.find((job) => job.dedupe_key === key)?.notes || "");
  if (!change) return;
  offerUndoSnackbar(change.after ? "Notes saved." : "Notes cleared.", [`notes:${key}`], () => undoNotes(key, change), { latest: `notes:${key}` });
}

async function undoNotes(key, { before, after }) {
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  if (!job) { showSnackbar("Couldn't undo. That job is no longer listed."); return; }
  try {
    // another tab may have typed since
    await patchJob(job, null, before, { notesOnly: true, expectedNotes: after });
    placeNotes(key, before);
    showSnackbar("Notes change undone.");
  } catch (error) {
    if (error.newerNotes === null || error.newerNotes === undefined) { showSnackbar(`Couldn't undo. ${error.message}`); return; }
    placeNotes(key, error.newerNotes);
    showSnackbar("Couldn't undo. The notes changed in another tab, so they now show that version.");
  }
}

function placeNotes(key, value) {
  const job = state.jobs.find((candidate) => candidate.dedupe_key === key);
  if (job) job.notes = value;
  const field = document.querySelector("#job-notes");
  if (field && field !== document.activeElement && document.querySelector("#inbox-reading-pane")?.dataset.jobKey === key) field.value = value;
}

function moveSelection(delta) {
  const jobs = filteredJobs();
  if (!jobs.length) return;
  const current = jobs.findIndex((job) => job.dedupe_key === state.selectedKey);
  const next = Math.min(jobs.length - 1, Math.max(0, (current < 0 ? 0 : current) + delta));
  const key = jobs[next].dedupe_key;
  const follow = listHasFocus();
  anchorInbox(key); selectJob(key);
  const row = revealRow(key);
  if (follow) row?.focus({ preventScroll: true });
}

function clearAllFilters() {
  state.query = ""; state.status = "all"; state.source = ""; state.remote = false; state.tag = ""; state.scrollTop = 0; syncInboxUrl(); renderInbox();
}

function applySavedView(index) {
  const view = state.savedViews[index];
  if (!view) return;
  const filters = view.filters || {};
  state.query = filters.query || ""; state.status = allowedStatuses.has(filters.status) ? filters.status : "all"; state.sort = allowedSorts.has(view.sort) ? view.sort : "score"; state.source = filters.source || ""; state.remote = Boolean(filters.remote); state.tag = filters.tag || ""; state.scrollTop = 0; syncInboxUrl(); renderInbox();
}

function currentViewFilters() {
  return { query: state.query, status: state.status, source: state.source, remote: state.remote, tag: state.tag };
}

// a rerender rebuilds the chips
function savedViewFocus(id, role = "button[data-menu-trigger]") {
  return () => {
    const chip = document.querySelector(`.saved-view-wrap[data-saved-view-id="${id}"]`);
    (chip?.querySelector(role) || document.querySelector("[data-open-filters]"))?.focus({ preventScroll: true });
  };
}

function placeSavedView(view) {
  const index = state.savedViews.findIndex((item) => item.id === view.id);
  if (index >= 0) state.savedViews[index] = view; else state.savedViews.push(view);
  if (routeRoot() === "inbox") renderInbox();
}

async function loadSavedViews() {
  try {
    const response = await fetch("/api/v1/saved-views", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) return;
    adoptSavedViews(await response.json());
    if (routeRoot() === "inbox") renderInbox();
  } catch { /* the next load retries */ }
}

function adoptSavedViews(payload) {
  state.savedViews = Array.isArray(payload.saved_views) ? payload.saved_views : [];
  state.savedViewsOrderRevision = payload.order_revision ?? 0;
}

async function saveCurrentView() {
  const name = savedViewName.value.trim();
  if (!name) { savedViewName.focus(); savedViewName.setAttribute("aria-invalid", "true"); return; }
  savedViewName.removeAttribute("aria-invalid");
  const existing = state.savedViews.find((view) => view.name === name);
  if (existing) { await replaceSavedView(existing); return; }
  const { status, body, error } = await companyWrite("/api/v1/saved-views", { method: "POST", body: JSON.stringify({ name, filters: currentViewFilters(), sort: state.sort }) }, "The view could not be saved.");
  // another tab took the name
  if (status === 409 && body.saved_view) { placeSavedView(body.saved_view); await replaceSavedView(body.saved_view); return; }
  if (error) { showSnackbar(error); return; }
  state.savedViews.push(body.saved_view);
  filterDialog.close(); renderInbox(); showSnackbar(`Saved view “${name}”.`);
}

// carrel names the object
async function replaceSavedView(view) {
  const trigger = document.querySelector("#save-view-button");
  const replace = await confirmDelete({
    title: `Replace the “${view.name}” view?`,
    body: "A view with this name already exists. Replacing it swaps in the current filters and sort. You can undo right after. To keep both, pick another name.",
    action: "Replace", cancel: "Pick another name", trigger,
  });
  if (!replace) { savedViewName.focus(); savedViewName.select(); return; }
  const error = await saveViewEdit(view.id, { filters: currentViewFilters(), sort: state.sort }, { message: `Replaced the “${view.name}” view.`, undone: "Replace undone." });
  if (error) { showSnackbar(error); return; }
  savedViewName.value = "";
  filterDialog.close();
}

function renameSavedView(view) {
  openEditor({
    title: `Rename ${view.name}`, focusBack: savedViewFocus(view.id),
    fields: [{ name: "name", label: "Name", value: view.name, required: true, maxlength: 40 }],
    save: (changes) => saveViewEdit(view.id, changes, { message: "View renamed.", undone: "Rename undone." }),
  });
}

// undo sends the old view back
async function saveViewEdit(id, payload, { message = "", undone = "", offer = true, aliasFrom = null } = {}) {
  const known = state.savedViews.find((item) => item.id === id);
  // a stale tab gets a 409
  const sent = "expected_revision" in payload || !known ? payload : { ...payload, expected_revision: known.revision };
  const { status, body, error } = await companyWrite(`/api/v1/saved-views/${id}`, { method: "PATCH", body: JSON.stringify(sent) }, "Couldn't save the view. Retry shortly.");
  if (status === 404) { loadSavedViews(); return "This view was deleted in another tab."; }
  if (status === 409 && body.view) { placeSavedView(body.view); return "It changed in another tab, so it now shows that version."; }
  if (error) return error;
  placeSavedView(body.view);
  if (aliasFrom !== null) revisionAliases.set(`view:${id}@${aliasFrom}`, body.view.revision);
  if (offer) {
    const restore = pickFields(body.previous, Object.keys(payload).filter((name) => name !== "expected_revision"));
    const after = body.view.revision;
    offerUndoSnackbar(message, [`view:${id}`], async () => {
      const failed = await saveViewEdit(id, { ...restore, expected_revision: currentRevision(`view:${id}`, after) }, { offer: false, aliasFrom: body.previous.revision });
      showSnackbar(failed ? `Couldn't undo. ${failed}` : undone);
    }, { latest: `view:${id}` });
  }
  return "";
}

async function deleteSavedView(viewId) {
  const order = state.savedViews.map((view) => String(view.id));
  const removed = state.savedViews.find((view) => view.id === viewId);
  if (!removed) return;
  const { status, error } = await companyWrite(`/api/v1/saved-views/${viewId}`, { method: "DELETE" }, "The view could not be deleted.");
  if (error && status !== 404) { showSnackbar(error); return; }
  const focus = focusAfterRemoval(order, [String(viewId)], ".saved-view-wrap", "data-saved-view-id", "[data-open-filters]");
  state.savedViews = state.savedViews.filter((view) => view.id !== viewId);
  renderInbox();
  requestAnimationFrame(focus);
  if (status === 404) { showSnackbar("That view was already deleted in another tab."); return; }
  offerUndoSnackbar(`Deleted the “${removed.name}” view.`, [`view:${viewId}`], () => restoreSavedView(viewId), { latest: `view:${viewId}` });
}

// comes back in its old slot
async function restoreSavedView(viewId) {
  const { status, error } = await companyWrite(`/api/v1/saved-views/${viewId}/restore`, { method: "POST" }, "The view could not be restored.");
  if (error) {
    if (status === 404) loadSavedViews();
    showSnackbar(`Couldn't undo. ${status === 404 ? "It was already restored in another tab." : error}`);
    return;
  }
  await loadSavedViews();
  if (routeRoot() === "inbox") savedViewFocus(viewId)();
  showSnackbar("View restored.");
}

async function saveViewOrder(order, before, view, { offer = true, role = "button[data-menu-trigger]" } = {}) {
  const byId = new Map(state.savedViews.map((item) => [item.id, item]));
  const position = order.indexOf(view.id) + 1;
  state.savedViews = order.map((id) => byId.get(id)).filter(Boolean);
  if (routeRoot() === "inbox") { renderInbox(); savedViewFocus(view.id, role)(); }
  announce(`${view.name} moved to position ${position} of ${order.length}.`);
  const { status, body, error } = await companyWrite("/api/v1/saved-views/order", { method: "PUT", body: JSON.stringify({ ids: order, expected_revision: state.savedViewsOrderRevision }) }, "Couldn't reorder the views.");
  if (error) {
    const text = status === 409 ? "The views changed in another tab, so they've reloaded." : `${error} Change undone.`;
    if (status === 409) await loadSavedViews();
    else {
      state.savedViews = before.map((id) => byId.get(id)).filter(Boolean);
      if (routeRoot() === "inbox") { renderInbox(); savedViewFocus(view.id, role)(); }
    }
    assertiveRegion.textContent = text;
    showSnackbar(text);
    return false;
  }
  state.savedViewsOrderRevision = body.order_revision;
  if (offer) offerUndoSnackbar(`Moved ${view.name} to position ${position}.`, ["views-order"], async () => {
    if (await saveViewOrder(before, order, view, { offer: false })) showSnackbar("Move undone.");
  }, { latest: "views-order" });
  return true;
}

function moveSavedView(view, to, options) {
  const before = state.savedViews.map((item) => item.id);
  const order = moveId(before, view.id, to);
  if (order.join() !== before.join()) saveViewOrder(order, before, view, options);
}

// --- snackbars and undo ---
// carrel timing, wcag 2.2.1

function showSnackbar(message, { action = "", onAction = null, undoId = 0, merge = "" } = {}) {
  const now = performance.now();
  if (snackbarCurrent && !action && !snackbarCurrent.action && snackbarCurrent.message === message) { armSnackbar(snackbarCurrent, new Countdown(snackbarDuration(message, false)), now); return; }
  // carrel merges same kind messages
  const next = { message, action, onAction, undoId };
  if (merge && snackbarCurrent?.merge === merge && !snackbarCurrent.leaving) {
    Object.assign(snackbarCurrent, mergeSnackbar(snackbarCurrent, next));
    syncSnackbar(snackbarCurrent);
    armSnackbar(snackbarCurrent, new Countdown(snackbarDuration(message, Boolean(action))), now);
    return;
  }
  const queued = merge && snackbarQueue.find((item) => item.merge === merge);
  if (queued) { Object.assign(queued, mergeSnackbar(queued, next)); return; }
  snackbarQueue.push({ ...mergeSnackbar({}, next), merge });
  if (!snackbarCurrent) showNextSnackbar(); else if (!snackbarCurrent.leaving) scheduleSnackbarExit(snackbarCurrent);
}

function showNextSnackbar() {
  const next = snackbarQueue.shift();
  if (!next) { snackbarCurrent = null; return; }
  snackbarRegion.innerHTML = `<div class="snackbar" role="status"><span class="snackbar-message">${escapeHtml(next.message)}</span><button class="snackbar-close" type="button" data-snackbar-close aria-label="Dismiss">${icons.close}</button></div>`;
  const bar = snackbarRegion.firstElementChild;
  const item = { ...next, bar, shownAt: performance.now(), timer: 0, clock: null };
  snackbarCurrent = item;
  syncSnackbar(item);
  const clock = new Countdown(snackbarDuration(next.message, Boolean(next.action)));
  if (document.hidden) clock.hold("hidden", item.shownAt);
  if (!document.hasFocus()) clock.hold("blur", item.shownAt);
  bar.addEventListener("mouseenter", () => holdSnackbar("hover", true));
  bar.addEventListener("mouseleave", () => holdSnackbar("hover", false));
  bar.addEventListener("focusin", () => holdSnackbar("focus", true));
  bar.addEventListener("focusout", (event) => { if (!bar.contains(event.relatedTarget)) holdSnackbar("focus", false); });
  bar.querySelector("[data-snackbar-close]").addEventListener("click", () => exitSnackbar(item));
  armSnackbar(item, clock, item.shownAt);
}

// merges and withdrawals change both
function syncSnackbar(item) {
  if (!item.bar) return;
  const text = item.bar.querySelector(".snackbar-message");
  text.textContent = item.message;
  const button = item.bar.querySelector("[data-snackbar-action]");
  if (!item.action) { button?.remove(); return; }
  if (button) { button.textContent = item.action; return; }
  const added = document.createElement("button");
  Object.assign(added, { className: "snackbar-action", type: "button", textContent: item.action, title: `${item.action} (${formatShortcut("Mod+Z")})` });
  added.dataset.snackbarAction = "";
  added.setAttribute("aria-keyshortcuts", ariaKeyshortcuts(["Mod+Z", "u"]));
  // take before exit drops it
  added.addEventListener("click", () => { item.onAction?.(); exitSnackbar(item); });
  text.after(added);
}

function armSnackbar(item, clock, now) {
  clearTimeout(item.timer);
  for (const reason of item.clock?.holds || []) clock.hold(reason, now);
  item.clock = clock;
  clock.start(now);
  scheduleSnackbarExit(item);
}

// a reader holding it keeps it
function scheduleSnackbarExit(item) {
  clearTimeout(item.timer);
  const { clock } = item;
  const wait = exitDelay({ shownAt: item.shownAt, resumedAt: clock.resumedAt, deadline: clock.deadline, queued: snackbarQueue.length > 0, held: clock.holds.size > 0 }, performance.now());
  if (wait >= 0) item.timer = setTimeout(() => exitSnackbar(item), wait);
}

function holdSnackbar(reason, on) {
  const item = snackbarCurrent;
  if (!item || item.leaving) return;
  const now = performance.now();
  if (on) { if (item.clock.hold(reason, now)) clearTimeout(item.timer); return; }
  if (item.clock.release(reason, now) >= 0) scheduleSnackbarExit(item);
}

function exitSnackbar(item) {
  if (item.leaving || snackbarCurrent !== item) return;
  item.leaving = true;
  for (const id of closedUndoIds(item, closingUndos)) { undoStack.remove(id); closingUndos.delete(id); }
  clearTimeout(item.timer);
  if (item.bar.contains(document.activeElement)) document.activeElement.blur();
  item.bar.classList.add("leaving");
  const reduce = document.documentElement.dataset.motion === "reduce";
  setTimeout(() => { if (item.bar.isConnected) item.bar.remove(); showNextSnackbar(); }, reduce ? 100 : 150);
}

// undo works now, the bar waits
function offerMoveSnackbar(message, merge, entry, saved) {
  const undoId = undoStack.push(entry);
  saved.then((error) => {
    if (error) { withdrawUndo(undoId); return; }
    if (undoStack.has(undoId)) showSnackbar(message, { action: "Undo", undoId, merge, onAction: () => runUndo(undoStack.take(undoId)) });
  });
}

// a reverted move cant be undone
function withdrawUndo(undoId) {
  undoStack.remove(undoId);
  for (const item of [snackbarCurrent, ...snackbarQueue]) {
    const patch = item && withdrawSnackbar(item, undoId, (id) => undoStack.has(id));
    if (!patch) continue;
    Object.assign(item, patch);
    syncSnackbar(item);
  }
}

// lets undo spot stale entries
function offerUndoSnackbar(message, keys, run, { latest = "", untilClosed = false } = {}) {
  const undoId = undoStack.push({ message, keys, run, latest });
  if (untilClosed) closingUndos.add(undoId);
  showSnackbar(message, { action: "Undo", undoId, onAction: () => runUndo(undoStack.take(undoId)) });
}

function runUndo(taken) {
  if (!taken) { showSnackbar("Nothing to undo."); return; }
  // the newer undo stays offered
  if (taken.blocked) { showSnackbar("Couldn't undo. It changed again since, so undo the newer change first."); return; }
  // their snackbars are stale now
  const stale = new Set([taken.entry, ...taken.dropped].map((entry) => entry.id));
  for (let index = snackbarQueue.length - 1; index >= 0; index -= 1) if (stale.has(snackbarQueue[index].undoId)) snackbarQueue.splice(index, 1);
  if (stale.has(snackbarCurrent?.undoId)) exitSnackbar(snackbarCurrent);
  else if (snackbarCurrent && !snackbarCurrent.leaving) scheduleSnackbarExit(snackbarCurrent); // queue may be empty now
  taken.entry.run();
}

// --- object menus ---
// keeps later actions out of handlers

const copyFieldItems = [
  { label: "Copy title", palette: true, run: (c) => copyJobField("title", c.job.title) },
  { label: "Copy company", palette: true, run: (c) => copyJobField("company", c.job.company) },
  { label: "Copy posting link", palette: true, when: (c) => c.job.url, run: (c) => copyJobField("posting link", c.job.url) },
];

const trackerItems = [
  { label: "Move up", when: (c) => !c.table, disabled: (c) => c.index === 0, run: (c) => moveOnBoard(c, c.index - 1) },
  { label: "Move down", when: (c) => !c.table, disabled: (c) => c.index === c.column.length - 1, run: (c) => moveOnBoard(c, c.index + 1) },
  { label: "Move to top", when: (c) => !c.table, disabled: (c) => c.index === 0, run: (c) => moveOnBoard(c, 0) },
  { label: "Move to bottom", when: (c) => !c.table, disabled: (c) => c.index === c.column.length - 1, run: (c) => moveOnBoard(c, c.column.length - 1) },
  { label: "Open company", divider: true, run: (c) => navigate(`/companies/${encodeURIComponent(c.job.company)}`) },
  { label: "Edit next step", when: (c) => c.table, run: (c) => c.el.querySelector("[data-next-step]")?.focus() },
  { label: "Edit posting…", run: (c) => editPosting(c.key, { focusBack: recordFocus(`${c.table ? ".tracker-table tr" : ".board-card"}[data-tracker-key="${CSS.escape(c.key)}"]`, "#tracker-title") }) },
  { label: "Add follow-up…", run: (c) => addFollowUp(c.key) },
  ...copyFieldItems,
  { label: "Add tag…", run: (c) => addTag([c.key]) },
  { label: "Remove tag…", when: (c) => (c.job.manual_tags || []).length > 0, run: (c) => removeTag([c.key]) },
  ...statusOptions.map((status, index) => ({ label: statusLabel(status), divider: index === 0, checked: (c) => c.job.status === status, run: (c) => setTrackerStatus(c.key, status) })),
  { label: "Hide permanently…", danger: true, divider: true, run: (c) => hideJobs([c.key], {
    trigger: c.el.querySelector("button[data-menu-trigger]"),
    focus: focusAfterRemoval(c.table ? trackerApplications().map((job) => job.dedupe_key) : c.column, [c.key], c.table ? ".tracker-table tr" : ".board-card", "data-tracker-key", "#tracker-title"),
  }) },
];

function trackerContext(el, table) {
  const key = el.dataset.trackerKey;
  const job = state.trackerData?.applications?.find((item) => item.dedupe_key === key);
  if (!job) return null;
  const column = table ? [] : trackerApplications().filter((item) => item.status === job.status).map((item) => item.dedupe_key);
  return { el, key, job, table, column, index: column.indexOf(key) };
}

const profileMenuItems = [
  { label: "Move up", disabled: (c) => c.index === 0, run: (c) => moveProfileItem(c, c.index - 1) },
  { label: "Move down", disabled: (c) => c.index === c.count - 1, run: (c) => moveProfileItem(c, c.index + 1) },
  { label: "Move to top", disabled: (c) => c.index === 0, run: (c) => moveProfileItem(c, 0) },
  { label: "Move to bottom", disabled: (c) => c.index === c.count - 1, run: (c) => moveProfileItem(c, c.count - 1) },
  { label: "Edit", divider: true, when: (c) => c.kind === "stories", run: (c) => openProfileItem(c, '[data-profile-property="title"]') },
  { label: "Edit", divider: true, when: (c) => c.kind === "documents", run: (c) => openProfileItem(c, '[data-profile-property="name"]') },
  { label: "Use as default resume", when: (c) => c.kind === "documents" && !c.item.default, run: (c) => setDefaultDocument(c, c.id) },
  { label: "Stop using as default", when: (c) => c.kind === "documents" && c.item.default, run: (c) => setDefaultDocument(c, null) },
  { label: "Rename", divider: true, when: (c) => c.kind === "answer_templates", run: (c) => openProfileItem(c, '[data-profile-property="name"]') },
  { label: "Rename", divider: true, when: (c) => c.kind === "fields", run: (c) => openProfileItem(c, '[data-profile-property="label"]') },
  { label: "Change group", when: (c) => c.kind === "fields", run: (c) => openProfileItem(c, '[data-profile-property="group"]') },
  { label: "Delete", danger: true, divider: true, run: (c) => deleteProfileItem(c) },
];

const ruleMenuItems = [
  { label: "Edit…", run: (c) => editRule(c.rule) },
  { label: (c) => c.rule.enabled ? "Pause" : "Enable", run: (c) => toggleRule(c.rule.id, !c.rule.enabled) },
  { label: "Move up", divider: true, when: (c) => c.count > 1, disabled: (c) => c.index === 0, run: (c) => moveRule(c, c.index - 1) },
  { label: "Move down", when: (c) => c.count > 1, disabled: (c) => c.index === c.count - 1, run: (c) => moveRule(c, c.index + 1) },
  { label: "Move to top", when: (c) => c.count > 1, disabled: (c) => c.index === 0, run: (c) => moveRule(c, 0) },
  { label: "Move to bottom", when: (c) => c.count > 1, disabled: (c) => c.index === c.count - 1, run: (c) => moveRule(c, c.count - 1) },
  { label: "Delete", danger: true, divider: true, run: (c) => deleteRule(c.rule) },
];

const companyMenuItems = [
  { label: "Open", when: (c) => !c.detail, run: (c) => navigate(`/companies/${encodeURIComponent(c.company.name)}`) },
  { label: "Rename…", run: (c) => renameCompany(c.company) },
  { label: "Merge into…", when: (c) => !c.company.hidden, run: (c) => pickMergeTarget(c) },
  { label: "Add link…", when: (c) => c.detail, run: () => addCompanyLink(document.querySelector("[data-add-company-link]")) },
  { label: (c) => c.company.hidden ? "Show in Companies" : "Hide from Companies", divider: true, run: (c) => setCompanyHidden(c, !c.company.hidden) },
];

const objectMenus = [
  {
    selector: ".saved-view-wrap[data-saved-view-id]", keyAttr: "data-saved-view-id", fallback: "[data-open-filters]",
    label: (c) => `${c.view.name} view`,
    context(el) {
      const index = state.savedViews.findIndex((view) => String(view.id) === el.dataset.savedViewId);
      return index < 0 ? null : { view: state.savedViews[index], index, count: state.savedViews.length };
    },
    items: [
      { label: "Apply", run: (c) => applySavedView(c.index) },
      { label: "Rename…", run: (c) => renameSavedView(c.view) },
      { label: "Move left", divider: true, when: (c) => c.count > 1, disabled: (c) => c.index === 0, run: (c) => moveSavedView(c.view, c.index - 1) },
      { label: "Move right", when: (c) => c.count > 1, disabled: (c) => c.index === c.count - 1, run: (c) => moveSavedView(c.view, c.index + 1) },
      { label: "Move to start", when: (c) => c.count > 1, disabled: (c) => c.index === 0, run: (c) => moveSavedView(c.view, 0) },
      { label: "Move to end", when: (c) => c.count > 1, disabled: (c) => c.index === c.count - 1, run: (c) => moveSavedView(c.view, c.count - 1) },
      { label: "Delete", danger: true, divider: true, run: (c) => deleteSavedView(c.view.id) },
    ],
  },
  {
    selector: ".rule-card[data-rule-id]", keyAttr: "data-rule-id", fallback: "#rules-title",
    label: (c) => c.rule.name,
    context(el) {
      const list = state.rules || [];
      const index = list.findIndex((item) => String(item.id) === el.dataset.ruleId);
      return index < 0 ? null : { rule: list[index], index, count: list.length };
    },
    items: ruleMenuItems,
  },
  {
    selector: ".profile-item[data-profile-ref]", keyAttr: "data-profile-ref", fallback: ".quick-fill-header h2",
    label: (c) => c.name, context: profileContext, items: profileMenuItems,
  },
  {
    selector: ".job-row[data-job-key]", keyAttr: "data-job-key", fallback: "#job-list-layer .job-row[tabindex='0'], .inbox-list h1",
    label: (c) => c.bulk ? `${new Intl.NumberFormat().format(state.selectedKeys.size)} selected jobs` : `${c.job.title}, ${c.job.company}`,
    context(el) {
      const key = el.dataset.jobKey;
      const job = state.jobs.find((item) => item.dedupe_key === key);
      if (!job) return null;
      const bulk = state.selectedKeys.size > 1 && state.selectedKeys.has(key);
      return { el, key, job, bulk };
    },
    items: [
      { label: "Open", when: (c) => !c.bulk, run: (c) => selectJob(c.key, { focusDetail: true }) },
      { label: "Apply now", shortcut: "a", when: (c) => !c.bulk && applicationUrl(c.job), disabled: (c) => jobHasActiveBlocker(c.job), run: (c) => applyToJob(c.job) },
      { label: "Open original posting", when: (c) => !c.bulk && c.job.url, run: (c) => window.open(c.job.url, "_blank", "noopener") },
      { label: "Open company", when: (c) => !c.bulk, run: (c) => navigate(`/companies/${encodeURIComponent(c.job.company)}`) },
      { label: "Edit posting…", when: (c) => !c.bulk, run: (c) => editPosting(c.key, { focusBack: recordFocus(`#job-list-layer .job-row[data-job-key="${CSS.escape(c.key)}"]`, ".inbox-list h1") }) },
      { label: "Add follow-up…", when: (c) => !c.bulk, run: (c) => addFollowUp(c.key) },
      { label: "Override eligibility…", when: (c) => !c.bulk && jobHasActiveBlocker(c.job), run: (c) => overrideEligibility(c.key) },
      { label: "Revoke eligibility override", when: (c) => !c.bulk && (state.eligibility.get(c.key)?.value?.overrides || []).length > 0 && !jobHasActiveBlocker(c.job), run: (c) => revokeOverride(c.key) },
      { label: "Save", shortcut: "s", divider: true, when: (c) => c.bulk || c.job.status !== "saved", run: (c) => performDecision("saved", "Saved", c.bulk ? null : [c.key]) },
      { label: "Queue", shortcut: "q", when: (c) => c.bulk || c.job.status !== "queued", disabled: (c) => !c.bulk && jobHasActiveBlocker(c.job), run: (c) => c.bulk ? queueSelection() : performDecision("queued", "Queued", [c.key]) },
      { label: "Dismiss", shortcut: "x", when: (c) => c.bulk || c.job.status !== "archived", run: (c) => performDecision("archived", "Dismissed", c.bulk ? null : [c.key]) },
      { label: "Dismiss company", when: (c) => c.bulk, run: () => dismissSelectedCompanies() },
      { label: "Restore", when: (c) => c.bulk ? selectionArchived() : c.job.status === "archived", run: (c) => performDecision("new", "Restored", c.bulk ? null : [c.key]) },
      { label: "Add tag…", divider: true, run: (c) => addTag(c.bulk ? [...state.selectedKeys] : [c.key]) },
      { label: "Remove tag…", when: (c) => removableTags(c.bulk ? [...state.selectedKeys].map(trackedJob).filter(Boolean) : [c.job]).length > 0, run: (c) => removeTag(c.bulk ? [...state.selectedKeys] : [c.key]) },
      { label: "Copy", divider: true, run: (c) => copyInboxJobs(c.bulk ? filteredJobs().filter((job) => state.selectedKeys.has(job.dedupe_key)) : [c.job]) },
      ...copyFieldItems.map((item) => ({ ...item, when: (c) => !c.bulk && (!item.when || item.when(c)) })),
      { label: (c) => state.selectedKeys.has(c.key) ? "Deselect" : "Select", run: (c) => { if (state.selectedKeys.has(c.key)) state.selectedKeys.delete(c.key); else state.selectedKeys.add(c.key); syncVisibleJobSelection(); } },
      { label: "Hide permanently…", danger: true, divider: true, run: (c) => hideInboxJobs(c.bulk ? [...state.selectedKeys] : [c.key], c.el) },
    ],
  },
  {
    selector: ".queue-card[data-queue-key]", keyAttr: "data-queue-key", fallback: "#queue-title, .empty-state h2",
    label: (c) => `${c.job.title}, ${c.job.company}`,
    context(el) {
      const jobs = queueJobs();
      const index = jobs.findIndex((job) => job.dedupe_key === el.dataset.queueKey);
      return index < 0 ? null : { key: jobs[index].dedupe_key, job: jobs[index], index, count: jobs.length };
    },
    items: [
      { label: "Move up", disabled: (c) => c.index === 0, run: (c) => reorderQueue(c.key, c.index - 1) },
      { label: "Move down", disabled: (c) => c.index === c.count - 1, run: (c) => reorderQueue(c.key, c.index + 1) },
      { label: "Move to top", disabled: (c) => c.index === 0, run: (c) => reorderQueue(c.key, 0) },
      { label: "Move to bottom", disabled: (c) => c.index === c.count - 1, run: (c) => reorderQueue(c.key, c.count - 1) },
      { label: "Open original posting", when: (c) => c.job.url, run: (c) => window.open(c.job.url, "_blank", "noopener") },
      { label: "Open company", run: (c) => navigate(`/companies/${encodeURIComponent(c.job.company)}`) },
      { label: "Edit posting…", run: (c) => editPosting(c.key, { focusBack: recordFocus(`.queue-card[data-queue-key="${CSS.escape(c.key)}"]`, "#queue-title") }) },
      ...copyFieldItems,
      { label: "Save for later", divider: true, run: (c) => removeFromQueue(c.key) },
      { label: "Hide permanently…", danger: true, divider: true, run: (c) => hideJobs([c.key], {
        trigger: document.querySelector(`.queue-card[data-queue-key="${CSS.escape(c.key)}"] button[data-menu-trigger]`),
        focus: focusAfterRemoval(queueJobs().map((job) => job.dedupe_key), [c.key], ".queue-card", "data-queue-key", "#queue-title, .empty-state h2"),
      }) },
    ],
  },
  {
    selector: ".board-card[data-tracker-key]", keyAttr: "data-tracker-key", fallback: "#tracker-title",
    label: (c) => `${c.job.title}, ${c.job.company}`, context: (el) => trackerContext(el, false), items: trackerItems,
  },
  {
    selector: ".tracker-table tr[data-tracker-key]", keyAttr: "data-tracker-key", fallback: "#tracker-title",
    label: (c) => `${c.job.title}, ${c.job.company}`, context: (el) => trackerContext(el, true), items: trackerItems,
  },
  {
    selector: ".reminder-row[data-reminder-id]", keyAttr: "data-reminder-id", fallback: "#followups-title, #tracker-title",
    label: (c) => `${c.reminder.company} follow-up`,
    context(el) {
      const reminder = findReminder(Number(el.dataset.reminderId));
      return reminder && { reminder, done: reminder.status === "done" };
    },
    items: [
      { label: "Done", when: (c) => !c.done, run: (c) => updateReminder(c.reminder.id, { action: "done" }) },
      { label: "Reopen", when: (c) => c.done, run: (c) => updateReminder(c.reminder.id, { action: "reopen" }) },
      ...[0, 1, 2].map((index) => ({
        label: () => ["Snooze until tomorrow", "Snooze 3 days", "Snooze until next week"][index], divider: index === 0,
        hint: () => new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(snoozeChoices()[index].until),
        when: (c) => !c.done, run: (c) => updateReminder(c.reminder.id, { action: "snooze", until: snoozeChoices()[index].until.toISOString() }),
      })),
      { label: "Pick a snooze date…", when: (c) => !c.done, run: (c) => pickSnoozeDate(c.reminder) },
      { label: "Clear snooze", when: (c) => c.reminder.status === "snoozed", run: (c) => updateReminder(c.reminder.id, { action: "unsnooze" }) },
      { label: "Open company", divider: true, run: (c) => navigate(`/companies/${encodeURIComponent(c.reminder.company)}`) },
    ],
  },
  {
    selector: ".interview-list article[data-interview-id]", keyAttr: "data-interview-id", fallback: "#interviews-title",
    label: (c) => `${c.interview.company} interview`,
    context(el) {
      const interview = state.trackerData?.interviews?.find((item) => String(item.id) === el.dataset.interviewId);
      return interview && { interview };
    },
    items: [
      { label: "Edit…", run: (c) => editInterview(c.interview) },
      { label: (c) => c.interview.cancelled_at ? "Mark as scheduled" : "Cancel interview", run: (c) => setInterviewCancelled(c.interview, !c.interview.cancelled_at) },
      { label: "Export .ics", divider: true, when: (c) => !c.interview.cancelled_at, run: (c) => { window.location.href = `/api/v1/interviews.ics?id=${c.interview.id}`; } },
      { label: "Open company", divider: (c) => Boolean(c.interview.cancelled_at), run: (c) => navigate(`/companies/${encodeURIComponent(c.interview.company)}`) },
      { label: "Delete…", danger: true, divider: true, run: (c) => deleteInterview(c.interview) },
    ],
  },
  {
    selector: ".contact-row[data-contact-id]", keyAttr: "data-contact-id", fallback: "#company-contacts-title",
    label: (c) => c.contact.name,
    context(el) {
      const contact = state.companiesData?.company?.contacts?.find((item) => String(item.id) === el.dataset.contactId);
      return contact && { contact };
    },
    items: [
      { label: "Email", when: (c) => c.contact.email, run: (c) => { window.location.href = `mailto:${encodeURIComponent(c.contact.email)}`; } },
      { label: "Open LinkedIn", when: (c) => safeHttpsUrl(c.contact.linkedin_url), run: (c) => window.open(safeHttpsUrl(c.contact.linkedin_url), "_blank", "noopener") },
      { label: "Edit…", divider: true, run: (c) => editContact(c.contact) },
      { label: "Delete", danger: true, divider: true, run: (c) => deleteContact(c.contact) },
    ],
  },
  {
    selector: ".company-card[data-company-key]", keyAttr: "data-company-key", fallback: "#companies-title",
    label: (c) => c.company.name,
    context(el) {
      const company = state.companiesData?.companies?.find((item) => item.key === el.dataset.companyKey);
      return company && { company, detail: false };
    },
    items: companyMenuItems,
  },
  {
    selector: ".company-detail-heading[data-company-key]", keyAttr: "data-company-key", fallback: "#company-title",
    label: (c) => c.company.name,
    context() {
      const company = state.companiesData?.company;
      return company && { company, detail: true };
    },
    items: companyMenuItems,
  },
  {
    selector: ".company-link-row[data-link-id]", keyAttr: "data-link-id", fallback: "#company-links-title",
    label: (c) => linkLabel(c.link),
    context(el) {
      const link = companyLinks()?.find((item) => String(item.id) === el.dataset.linkId);
      return link && { link };
    },
    items: [
      { label: "Open link", when: (c) => linkHref(c.link.url), run: (c) => window.open(linkHref(c.link.url), "_blank", "noopener") },
      { label: "Edit…", run: (c) => editLink(c.link) },
      { label: "Delete", danger: true, divider: true, run: (c) => deleteLink(c.link) },
    ],
  },
];

// palette rows stay out of menus
function menuItemsFor(type, context, { palette = false } = {}) {
  return type.items.filter((item) => (palette || !item.palette) && (!item.when || item.when(context))).map((item) => ({
    label: typeof item.label === "function" ? item.label(context) : item.label,
    shortcut: item.shortcut, divider: typeof item.divider === "function" ? item.divider(context) : item.divider, danger: item.danger,
    hint: typeof item.hint === "function" ? item.hint(context) : item.hint,
    checked: item.checked?.(context), disabled: Boolean(item.disabled?.(context)),
    run: () => item.run(context),
  }));
}

function objectMenuAt(target) {
  for (const type of objectMenus) {
    const el = target.closest(type.selector);
    if (!el) continue;
    const context = type.context(el);
    return context ? { type, el, context } : null;
  }
  return null;
}

function resolveObjectMenu(target) {
  // popout nodes have another Element
  if (target?.nodeType !== 1) return null;
  for (const type of objectMenus) {
    const el = target.closest(type.selector);
    if (!el) continue;
    const context = type.context(el);
    if (!context) return null;
    const items = menuItemsFor(type, context);
    const value = el.getAttribute(type.keyAttr);
    const focusTarget = (node) => node.matches(".job-row") ? node : node.querySelector("button[data-menu-trigger]");
    const doc = el.ownerDocument;
    const inside = el.contains(doc.activeElement) && doc.activeElement !== doc.body ? doc.activeElement : null;
    // the object may rerender or leave
    const refocus = () => {
      const again = doc.querySelector(`${type.selector}[${type.keyAttr}="${CSS.escape(value)}"]`) || doc.querySelector(type.selector);
      (again ? focusTarget(again) : doc.querySelector(type.fallback))?.focus({ preventScroll: true });
    };
    const trigger = el.querySelector("[data-menu-trigger]");
    return { label: type.label(context), items, anchor: trigger || el, align: trigger ? "end" : "start", returnTo: inside || focusTarget(el), refocus };
  }
  return null;
}

// native select-all elsewhere
function selectAllShortcut() {
  if (routeRoot() === "inbox" && state.loaded) { selectAllInbox(); return true; }
  const name = activeRowList();
  const active = document.activeElement;
  if (!name || !(active === document.body || active?.closest?.(".queue-page, .applications-section"))) return false;
  selectAllRows(name);
  return true;
}

const rowControls = "a, button, input, select, textarea, label, summary, [data-menu-trigger]";

let liftFrame = 0;
// sticky bars move on scroll
document.addEventListener("scroll", () => {
  if (!liftFrame) liftFrame = requestAnimationFrame(() => { liftFrame = 0; liftSnackbars(); });
}, { capture: true, passive: true });

// shift-click shouldnt select text
document.addEventListener("mousedown", (event) => {
  if (event.shiftKey && event.target.closest(".queue-card[data-queue-key], .tracker-table tbody tr[data-tracker-key], .board-card[data-tracker-key]") && !event.target.closest(rowControls)) event.preventDefault();
});

function shortcutScopeAllows(event) {
  return !isTextEntry(event.target);
}

function handleGlobalKeydown(event) {
  if (confirmDialog.open || editDialog.open) return; // escape must reach the dialog
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openCommand(document.activeElement); return; }
  if (matchShortcut(event, "Mod+Z") && shortcutScopeAllows(event)) { event.preventDefault(); runUndo(undoStack.pop()); return; }
  if (matchShortcut(event, "Mod+A") && shortcutScopeAllows(event) && !commandDialog.open && !shortcutDialog.open && !filterDialog.open && selectAllShortcut()) { event.preventDefault(); return; }
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (commandDialog.open || shortcutDialog.open || filterDialog.open || !shortcutScopeAllows(event)) return;
  if (event.key === "?") { event.preventDefault(); openShortcuts(document.activeElement); return; }
  if (matchShortcut(event, "u")) { event.preventDefault(); runUndo(undoStack.pop()); return; }
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
  const rowList = activeRowList();
  if (rowList) {
    const box = event.target.closest?.("[data-row-select]");
    const moves = { ArrowDown: 1, ArrowUp: -1, Home: "start", End: "end" };
    if (box && event.key in moves) { event.preventDefault(); stepRow(rowList, moves[event.key], event.shiftKey); return; }
    if (key === "j" || key === "k") { event.preventDefault(); stepRow(rowList, key === "j" ? 1 : -1, event.shiftKey); return; }
    if (event.key === "Escape" && !event.target.closest?.("[data-drag-handle]") && clearRows(rowList)) { event.preventDefault(); return; }
    if (rowList === "queue" && key === "s" && !event.shiftKey && state.rowSelection.queue.keys.size) { event.preventDefault(); runBulk("queue-save"); return; }
  }
  if (routeRoot() === "inbox" && state.loaded) {
    if (key === "/") { event.preventDefault(); document.querySelector("#job-search")?.focus(); return; }
    const arrow = (event.key === "ArrowDown" || event.key === "ArrowUp") && listHasFocus();
    if (key === "j" || key === "k" || arrow) {
      event.preventDefault();
      const delta = key === "j" || event.key === "ArrowDown" ? 1 : -1;
      if (event.shiftKey) extendInbox(delta); else moveSelection(delta);
      return;
    }
    if (event.key === " " && listHasFocus()) { event.preventDefault(); toggleFocusedInbox(); return; }
    if (event.key === "Escape" && clearInboxSelection()) { event.preventDefault(); return; }
    if (event.key === "Enter" && !event.target.closest("button:not(.job-row), a, summary")) { event.preventDefault(); document.querySelector("#job-title")?.focus({ preventScroll: true }); return; }
    if (key === "f") { event.preventDefault(); state.focus = !state.focus; syncInboxUrl(); renderInbox({ focus: true }); return; }
    if (key === "s") { event.preventDefault(); performDecision("saved", "Saved"); return; }
    if (key === "x") { event.preventDefault(); performDecision("archived", "Dismissed"); return; }
    if (key === "q") {
      event.preventDefault();
      if (state.selectedKeys.size) { queueSelection(); return; } // same eligibility split as the bar
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
  }
  if (key === "g") { goChordUntil = now + 1000; return; }
  if (now <= goChordUntil) {
    const routes = { i: "/inbox", q: "/queue", t: "/tracker", c: "/companies", p: "/profile" };
    goChordUntil = 0;
    if (routes[key]) { event.preventDefault(); navigate(routes[key]); }
  }
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-refresh-quick-fill-access]")) {
    loadInbox().then(() => showSnackbar(state.quickFillEnabled ? "Quick-fill is ready." : state.error || "Quick-fill is not enabled for this session."));
    return;
  }
  const copyMenu = event.target.closest("[data-copy-menu]");
  if (copyMenu) { toggleMenu(copyMenu, copyMenuItems(), { label: "Copy jobs" }); return; }
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
  if (reminderAction) { updateReminder(Number(reminderAction.dataset.reminderId), { action: reminderAction.dataset.reminderAction }); return; }
  const snoozeButton = event.target.closest("[data-reminder-snooze]");
  if (snoozeButton) {
    const reminder = findReminder(Number(snoozeButton.dataset.reminderSnooze));
    if (reminder) toggleMenu(snoozeButton, snoozeItems(reminder), { label: `Snooze ${reminder.company} follow-up` });
    return;
  }
  if (event.target.closest("[data-retry-tracker]")) { state.trackerData = null; state.trackerError = ""; loadTrackerData(); return; }
  if (event.target.closest("[data-retry-companies]")) { state.companiesData = null; state.companiesError = ""; loadCompanies(); return; }
  const addCompanyButton = event.target.closest("[data-add-company]");
  if (addCompanyButton) { addCompany(addCompanyButton); return; }
  const addLinkButton = event.target.closest("[data-add-company-link]");
  if (addLinkButton) { addCompanyLink(addLinkButton); return; }
  if (event.target.closest("[data-restore-company]") && state.companiesData?.company) { setCompanyHidden({ company: state.companiesData.company, detail: true }, false); return; }
  if (event.target.closest("[data-clear-company-filters]")) {
    state.companyQuery = ""; state.companyShow = "all"; syncCompaniesUrl(); renderCompanies();
    document.querySelector("#company-search")?.focus(); return;
  }
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
    if (section) { section.open = true; section.scrollIntoView({ behavior: document.documentElement.dataset.motion === "reduce" ? "auto" : "smooth", block: "start" }); }
    return;
  }
  if (event.target.closest("[data-session-applied]")) { markSessionApplied(); return; }
  if (event.target.closest("[data-session-not-yet]")) { if (state.applySession) state.applySession.awaitingReturn = ""; renderApplySession({ focus: true }); return; }
  if (event.target.closest("[data-session-skip]")) { advanceApplySession(); return; }
  if (event.target.closest("[data-session-previous]")) { stepApplySession(-1); return; }
  if (event.target.closest("[data-session-next]")) { stepApplySession(1); return; }
  if (event.target.closest("[data-session-reload]")) { state.sessionLoad = ""; renderApplySession({ focus: true }); return; }
  if (event.target.closest("[data-session-end]")) { endApplySession(); return; }
  const row = event.target.closest(".job-row[data-job-key]");
  if (row) { handleRowSelection(row, event); return; }
  const companyRole = event.target.closest(".company-role[data-job-key]");
  if (companyRole) { selectJob(companyRole.dataset.jobKey); return; }
  const overrideButton = event.target.closest("[data-override-eligibility]");
  if (overrideButton) { overrideEligibility(state.selectedKey, overrideButton); return; }
  if (event.target.closest("[data-revoke-override]")) { revokeOverride(); return; }
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
  const rowBox = event.target.closest("[data-row-select]");
  if (rowBox) { pickRow(rowBox.dataset.rowSelect, rowBox.value, event, true); return; }
  const allBox = event.target.closest("[data-row-select-all]");
  if (allBox) { if (allBox.checked) selectAllRows(allBox.dataset.rowSelectAll); else clearRows(allBox.dataset.rowSelectAll); return; }
  const pickable = event.target.closest(".queue-card[data-queue-key], .tracker-table tbody tr[data-tracker-key], .board-card[data-tracker-key]");
  if (pickable && !event.target.closest(rowControls) && !window.getSelection()?.toString()) {
    pickRow(pickable.matches(".queue-card") ? "queue" : "tracker", pickable.dataset.queueKey || pickable.dataset.trackerKey, event, false);
    return;
  }
  const bulk = event.target.closest("[data-bulk]");
  if (bulk) { runBulk(bulk.dataset.bulk, bulk); return; }
  const bulkMore = event.target.closest("[data-bulk-more]");
  if (bulkMore) { toggleMenu(bulkMore, bulkMoreItems(bulkMore.closest(".bulk-bar"), bulkMore), { label: "More bulk actions" }); return; }
  const addTagButton = event.target.closest("[data-add-tag]");
  if (addTagButton && state.selectedKey) { addTag([state.selectedKey], addTagButton); return; }
  const clearSelection = event.target.closest("[data-clear-selection]");
  if (clearSelection) {
    if (clearSelection.dataset.clearSelection === "inbox") clearInboxSelection(); else clearRows(clearSelection.dataset.clearSelection);
    return;
  }
  const tab = event.target.closest("[data-status-tab]");
  if (tab) { state.status = tab.dataset.statusTab; state.scrollTop = 0; state.selectedKeys.clear(); syncInboxUrl(); renderInbox(); return; }
  if (event.target.closest("[data-open-filters]")) { openFilters(event.target.closest("[data-open-filters]")); return; }
  const chip = event.target.closest("[data-clear-filter]");
  if (chip) {
    if (chip.dataset.clearFilter === "query") state.query = "";
    if (chip.dataset.clearFilter === "source") state.source = "";
    if (chip.dataset.clearFilter === "remote") state.remote = false;
    if (chip.dataset.clearFilter === "tag") state.tag = "";
    syncInboxUrl(); renderInbox(); return;
  }
  if (event.target.closest("[data-clear-all-filters]")) { clearAllFilters(); return; }
  const savedView = event.target.closest("[data-saved-view]");
  if (savedView) { applySavedView(Number(savedView.dataset.savedView)); return; }
  if (event.target.closest("[data-exit-focus]")) { state.focus = false; syncInboxUrl(); renderInbox({ focus: true }); return; }
  if (event.target.closest("[data-back-to-list]")) { state.focus = false; navigate(inboxUrl("")); return; }
  if (event.target.closest("[data-retry-jobs]")) { state.loaded = false; state.error = ""; renderInbox(); loadInbox(); return; }
  if (event.target.closest("[data-retry-description]")) { state.descriptions.delete(state.selectedKey); loadDescription(state.selectedKey, { force: true }); renderSelectedJob(); return; }
  if (event.target.closest("[data-save-manual-description]")) { saveManualDescription(); return; }
  const editButton = event.target.closest("[data-edit-posting]");
  if (editButton) {
    const field = editButton.dataset.editPosting;
    if (editButton.getAttribute("aria-disabled") !== "true") editPosting(state.selectedKey, { trigger: editButton, focus: field, focusBack: () => document.querySelector(`[data-edit-posting="${field}"]`)?.focus() });
    return;
  }
  if (event.target.closest("[data-dismiss-overview]") && state.selectedKey) { setOverviewDismissed(state.selectedKey, true); return; }
  if (event.target.closest("[data-restore-overview]") && state.selectedKey) { setOverviewDismissed(state.selectedKey, false); return; }
  if (event.target.closest("[data-regenerate-overview]") && state.selectedKey) { regenerateOverview(state.selectedKey); return; }
  const dismissActivity = event.target.closest("[data-dismiss-activity]");
  if (dismissActivity) {
    const entry = state.trackerData?.history?.find((item) => String(item.id) === dismissActivity.dataset.dismissActivity);
    const order = [...document.querySelectorAll(".activity-timeline [data-activity-id]")].map((row) => row.dataset.activityId);
    if (entry) setActivityDismissed(entry, true, { order });
    return;
  }
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

function boardStatusOf(list) {
  return list.closest("[data-board-status]").dataset.boardStatus;
}

function boardApplication(item) {
  return state.trackerData?.applications?.find((job) => job.dedupe_key === item.dataset.trackerKey);
}

// a column hop shouldnt force manual
function boardSlot({ list, item, origin }) {
  const job = boardApplication(item);
  if (!job || list === origin?.list) return origin?.index ?? 0;
  const status = boardStatusOf(list);
  return sortedInsertIndex(trackerApplications().filter((app) => app.status === status), job, status, state.trackerSort);
}

async function putBoardOrder(status, keys) {
  const response = await secureWrite("/api/v1/tracker/board-order", {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ status, dedupe_keys: keys }),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "The board order could not be saved.");
}

// card status may still be old
function applyBoardOrder(status, keys) {
  const positions = new Map(keys.map((key, index) => [key, index]));
  for (const app of state.trackerData?.applications || []) {
    if (positions.has(app.dedupe_key)) app.board_position = positions.get(app.dedupe_key);
    else if (app.status === status) app.board_position = null;
  }
}

const boardSavers = new Map();

function boardSaver(status) {
  if (!boardSavers.has(status)) boardSavers.set(status, createOrderSaver({
    send: (keys) => putBoardOrder(status, keys),
    onLanded: trackerWriteLanded,
    // other clients may have moved cards
    onRevert: () => loadTrackerData({ background: true }),
  }));
  return boardSavers.get(status);
}

const boardOrderError = (error) => `${error.message.replace(/\.?$/, ".")} Reloading the board.`; // server errors lack a stop

// a reload mustnt drop unsaved moves
function overlayUnsavedOrders() {
  const apps = state.trackerData?.applications || [];
  for (const [status, saver] of boardSavers) {
    if (!saver.unsaved) continue;
    const here = new Set(apps.filter((app) => app.status === status).map((app) => app.dedupe_key));
    saver.prune((key) => here.has(key));
    applyBoardOrder(status, saver.unsaved);
  }
}

// every write queues behind its column
function saveBoardOrder(status, keys, confirmed) {
  const saver = boardSaver(status);
  if (!saver.busy) saver.reset(confirmed);
  return saver.save(keys);
}

async function saveBoardOrders(writes, confirmed) {
  const errors = (await Promise.all(writes.map(({ status, keys }) => saveBoardOrder(status, keys, confirmed.get(status) || [])))).filter(Boolean);
  if (errors.length) throw errors[0];
}

function renderBoardMove(animate) {
  if (routeRoot() !== "tracker") return;
  if (animate) flipKeyed(".board-card[data-tracker-key]", (card) => card.dataset.trackerKey, () => renderTracker());
  else renderTracker();
}

// dont switch sorts needlessly
function placeOnBoard(key, status, visible, index, { animate = true } = {}) {
  const apps = state.trackerData?.applications || [];
  const job = apps.find((app) => app.dedupe_key === key);
  if (!job || state.statusSaving.has(key)) return;
  const from = job.status;
  const previousSort = state.trackerSort;
  const base = columnKeys(apps, status, previousSort);
  const order = placeKey(base, visible, key, index);
  if (from === status && order.every((item, at) => item === base[at])) return;
  const columns = [...new Set([from, status])].filter((column) => boardStatuses.includes(column));
  const before = new Map(columns.map((column) => [column, positionedKeys(apps, column)]));
  const undoOrders = boardUndoOrders(apps, key, from, status, previousSort);
  const restore = (writes) => {
    if (state.trackerSort !== previousSort) { state.trackerSort = previousSort; syncTrackerUrl(); }
    [...writes].reverse().forEach(({ status: column, keys }) => applyBoardOrder(column, keys)); // null the card before placing it
  };
  const exactly = [...before].map(([column, keys]) => ({ status: column, keys }));
  const undo = () => {
    const current = new Map(columns.map((column) => [column, positionedKeys(apps, column)]));
    restore(undoOrders);
    return setTrackerStatus(key, from, { offerUndo: false, fallback: job, afterSave: () => saveBoardOrders(undoOrders, current) });
  };
  if (from !== status) {
    const natural = base.toSpliced(sortedInsertIndex(apps.filter((app) => app.status === status), job, status, previousSort), 0, key);
    if (order.every((item, at) => item === natural[at])) { setTrackerStatus(key, status, { undo, undoKeys: [key] }); return; }
  }
  // the origin would reshuffle under manual
  const fromOrder = from !== status && boardStatuses.includes(from) ? columnKeys(apps, from, previousSort).filter((item) => item !== key) : null;
  const writes = [{ status, keys: order }, ...(fromOrder ? [{ status: from, keys: fromOrder }] : [])];
  const touched = [...new Set([...order, ...(fromOrder || [])])];
  writes.forEach(({ status: column, keys }) => applyBoardOrder(column, keys));
  if (previousSort !== "manual") { state.trackerSort = "manual"; syncTrackerUrl(); }
  if (from === status) {
    const saved = saveBoardOrder(status, order, before.get(status));
    renderBoardMove(animate);
    const shown = trackerApplications().filter((app) => app.status === status).map((app) => app.dedupe_key);
    offerMoveSnackbar(moveAnnouncement(shown.indexOf(key), shown.length), "board-move", { message: "Board move", keys: touched, run: () => {
      restore(exactly);
      saveBoardOrder(status, before.get(status), order).then((error) => showSnackbar(error ? boardOrderError(error) : "Board move undone."));
      renderBoardMove(true);
    } }, saved);
    saved.then((error) => { if (error) showSnackbar(boardOrderError(error)); });
    return;
  }
  setTrackerStatus(key, status, { afterSave: () => saveBoardOrders(writes, before), onFail: () => restore(exactly), undoKeys: touched, undo });
}

function dropOnBoard({ key, to, keyboard }) {
  const visible = [...to.list.querySelectorAll(":scope > .board-card[data-tracker-key]")].map((card) => card.dataset.trackerKey);
  placeOnBoard(key, boardStatusOf(to.list), visible, visible.indexOf(key), { animate: Boolean(keyboard) });
  return document.querySelector(`.board-card[data-tracker-key="${CSS.escape(key)}"]`);
}

function moveOnBoard(c, index) {
  placeOnBoard(c.key, c.job.status, c.column, index);
}

bindDrag({
  handle: ".queue-drag-handle", item: ".queue-card[data-queue-key]",
  lists: () => [...document.querySelectorAll(".queue-list")],
  key: (item) => item.dataset.queueKey,
  drop: ({ key, to }) => reorderQueue(key, to.index),
  announce,
  lifted: (at) => liftAnnouncement(at.index, at.count),
  moved: (at) => moveAnnouncement(at.index, at.count),
  dropped: (at) => `Dropped, position ${at.index + 1} of ${at.count}.`,
  cancelled: (at) => cancelAnnouncement(at.index, at.count),
});

bindDrag({
  handle: ".saved-view-handle", item: ".saved-view-wrap[data-saved-view-id]", axis: "x",
  lists: () => [...document.querySelectorAll(".saved-view-list")],
  key: (item) => item.dataset.savedViewId,
  find: (key) => document.querySelector(`.saved-view-wrap[data-saved-view-id="${CSS.escape(key)}"]`),
  drop: ({ key, to, keyboard }) => {
    const view = state.savedViews.find((item) => String(item.id) === key);
    if (view) moveSavedView(view, to.index, { role: keyboard ? ".saved-view-handle" : "button[data-menu-trigger]" });
    return document.querySelector(`.saved-view-wrap[data-saved-view-id="${CSS.escape(key)}"]`);
  },
  announce,
  lifted: (at) => `Picked up, position ${at.index + 1} of ${at.count}. Use the left and right arrow keys to move, Space to drop, Escape to cancel.`,
  moved: (at) => moveAnnouncement(at.index, at.count),
  dropped: (at) => `Dropped, position ${at.index + 1} of ${at.count}.`,
  cancelled: (at) => cancelAnnouncement(at.index, at.count),
});

function bindProfileDrag(doc) {
  bindDrag({
    doc, handle: ".profile-drag-handle", item: ".profile-item[data-profile-ref]",
    // only the lifted items own list
    lists: () => { const list = doc.querySelector(".profile-item.is-lifted")?.parentElement; return list ? [list] : []; },
    key: (item) => item.dataset.profileRef,
    find: (key) => doc.querySelector(`.profile-item[data-profile-ref="${CSS.escape(key)}"]`),
    drop: dropProfileItem,
    announce,
    lifted: (at) => liftAnnouncement(at.index, at.count),
    moved: (at) => moveAnnouncement(at.index, at.count),
    dropped: (at) => `Dropped, position ${at.index + 1} of ${at.count}.`,
    cancelled: (at) => cancelAnnouncement(at.index, at.count),
  });
}

bindProfileDrag(document);

bindDrag({
  handle: ".board-drag-handle", item: ".board-card[data-tracker-key]",
  lists: () => [...document.querySelectorAll(".board-column > div")],
  zone: (list) => list.closest(".board-column"),
  key: (item) => item.dataset.trackerKey,
  find: (key) => document.querySelector(`.board-card[data-tracker-key="${CSS.escape(key)}"]`),
  keyboardSlot: boardSlot,
  drop: dropOnBoard,
  announce,
  lifted: (at) => `Picked up, ${statusLabel(boardStatusOf(at.list))}, position ${at.index + 1} of ${at.count}. Use the arrow keys to move, Space to drop, Escape to cancel.`,
  moved: (at) => `${statusLabel(boardStatusOf(at.list))}, position ${at.index + 1} of ${at.count}.`,
  dropped: (at) => `Dropped, still ${statusLabel(boardStatusOf(at.list))}, position ${at.index + 1} of ${at.count}.`,
  cancelled: (at) => `Move cancelled, back in ${statusLabel(boardStatusOf(at.list))}.`,
});

document.addEventListener("input", (event) => {
  if (event.target.id === "job-search") {
    state.query = event.target.value; state.scrollTop = 0; syncInboxUrl({ replace: true }); renderInbox();
    const search = document.querySelector("#job-search"); search?.focus(); search?.setSelectionRange(state.query.length, state.query.length);
  } else if (event.target.id === "job-notes") {
    const key = state.selectedKey;
    const value = event.target.value;
    clearTimeout(state.notesTimer); document.querySelector("#notes-state").textContent = "Unsaved changes";
    state.notesDraft = { key, value };
    state.notesTimer = setTimeout(() => { state.notesDraft = null; queueNotes(key, value); }, 600);
  } else if (event.target.id === "manual-description-text") {
    state.manualDescriptionDrafts.set(state.selectedKey, event.target.value);
  } else if (event.target.id === "tracker-search") {
    state.trackerQuery = event.target.value; syncTrackerUrl({ replace: true }); renderTracker();
    const search = document.querySelector("#tracker-search"); search?.focus(); search?.setSelectionRange(state.trackerQuery.length, state.trackerQuery.length);
  } else if (event.target.id === "company-search") {
    state.companyQuery = event.target.value; syncCompaniesUrl(); renderCompanyResults();
  } else if (event.target.id === "company-note") {
    const { key, name: company } = state.companiesData?.company || {};
    const body = event.target.value;
    if (document.querySelector("#company-note-state")) document.querySelector("#company-note-state").textContent = "Unsaved changes";
    clearTimeout(state.companyNoteTimer);
    state.companyNoteDraft = { key, body };
    state.companyNoteTimer = setTimeout(() => { state.companyNoteDraft = null; queueCompanyNote(key, company, body); }, 600);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "job-sort") { state.sort = event.target.value; state.scrollTop = 0; syncInboxUrl(); renderInbox(); }
  else if (event.target.id === "tracker-status") { state.trackerStatus = event.target.value; syncTrackerUrl(); renderTracker(); }
  else if (event.target.id === "tracker-sort") { state.trackerSort = event.target.value; syncTrackerUrl(); renderTracker(); document.querySelector("#tracker-sort")?.focus(); }
  else if (event.target.matches("[data-board-status-select]")) setTrackerStatus(event.target.dataset.boardStatusSelect, event.target.value);
  else if (event.target.matches("[data-tracker-status-select]")) setTrackerStatus(event.target.dataset.trackerStatusSelect, event.target.value);
  else if (event.target.matches("[data-job-status-select]")) setTrackerStatus(event.target.dataset.jobStatusSelect, event.target.value);
  else if (event.target.id === "company-show") { state.companyShow = companyFilter(event.target.value); syncCompaniesUrl(); renderCompanyResults(); }
  else if (event.target.id === "linkedin-csv") { const file = event.target.files?.[0]; event.target.value = ""; importLinkedInCsv(file); }
  else if (event.target.matches("[data-rule-toggle]")) toggleRule(Number(event.target.dataset.ruleToggle), event.target.checked);
  else if (event.target.matches("#edit-dialog select[name='kind']")) {
    const help = document.querySelector("#edit-value-help");
    if (help) help.textContent = ruleValueHelp(event.target.value);
  }
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

// starts one undoable edit
document.addEventListener("focusin", (event) => {
  if (event.target.id === "job-notes") {
    const key = document.querySelector("#inbox-reading-pane")?.dataset.jobKey;
    const job = key && state.jobs.find((candidate) => candidate.dedupe_key === key);
    if (job) textSessions.begin(`notes:${key}`, job.notes || "");
  } else if (event.target.id === "company-note") {
    const company = state.companiesData?.company;
    if (company?.key) textSessions.begin(`company-note:${company.key}`, company.note?.body || "");
  }
});

document.addEventListener("focusout", (event) => {
  if (event.target.matches("[data-next-step]")) saveTrackerNextStep(event.target.dataset.nextStep, event.target.value, event.target);
  else if (event.target.id === "job-notes") {
    const key = document.querySelector("#inbox-reading-pane")?.dataset.jobKey;
    if (key) settleNotes(key);
  } else if (event.target.id === "company-note") {
    const company = state.companiesData?.company;
    if (company?.key) settleCompanyNote(company.key, company.name);
  }
});

document.addEventListener("submit", (event) => {
  if (event.target.id === "capture-form") { event.preventDefault(); submitCapture(); }
  if (event.target.id === "edit-form") { event.preventDefault(); submitEditor(); }
  if (event.target.id === "interview-form") { event.preventDefault(); submitInterview(event.target); }
  if (event.target.id === "contact-form") { event.preventDefault(); submitContact(event.target); }
  if (event.target.id === "rule-form") { event.preventDefault(); submitRule(event.target); }
});

document.addEventListener("paste", (event) => {
  if (routeRoot() !== "inbox" || captureDialog.open) return;
  if (isTextEntry(event.target)) return;
  const pasted = event.clipboardData?.getData("text/plain")?.trim() || "";
  if (!/^https:\/\/\S+$/i.test(pasted)) return;
  event.preventDefault();
  openCapture(pasted);
});

filterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(filterForm);
  state.source = String(data.get("source") || ""); state.remote = remoteFilter.checked; state.tag = String(data.get("tag") || ""); state.scrollTop = 0; filterDialog.close(); syncInboxUrl(); renderInbox();
});

document.querySelector("#clear-filters").addEventListener("click", () => { state.source = ""; state.remote = false; state.tag = ""; sourceOptions.querySelector('input[value=""]')?.click(); tagOptionsList.querySelector('input[value=""]')?.click(); remoteFilter.checked = false; });
document.querySelector("#save-view-button").addEventListener("click", saveCurrentView);
commandInput.addEventListener("input", () => { commandSelection = 0; renderCommands(); });
commandInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") { event.preventDefault(); commandSelection = Math.min(commandMatches.length - 1, commandSelection + 1); renderCommands(); }
  else if (event.key === "ArrowUp") { event.preventDefault(); commandSelection = Math.max(0, commandSelection - 1); renderCommands(); }
  else if (event.key === "Enter" && commandMatches[commandSelection]) { event.preventDefault(); runCommand(commandMatches[commandSelection].id); }
});

confirmDialog.querySelector("[data-confirm-cancel]").addEventListener("click", () => confirmDialog.close());
for (const button of editDialog.querySelectorAll("[data-edit-cancel]")) button.addEventListener("click", cancelEditor);
// escape asks before dropping edits
editDialog.addEventListener("cancel", (event) => { event.preventDefault(); cancelEditor(); });
editDialog.addEventListener("click", (event) => {
  if (event.target !== editDialog) return;
  const bounds = editDialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) cancelEditor();
});
confirmDialog.querySelector("[data-confirm-accept]").addEventListener("click", () => confirmDialog.close("confirm"));
confirmDialog.addEventListener("close", () => {
  const resolve = confirmResolve;
  confirmResolve = null;
  resolve?.(confirmDialog.returnValue === "confirm");
});

for (const dialog of [commandDialog, shortcutDialog, filterDialog, captureDialog, confirmDialog]) {
  dialog.addEventListener("close", restoreDialogTrigger);
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
}

// toggle doesnt bubble
routeView.addEventListener("toggle", (event) => {
  if (event.target.matches?.(".interview-past")) state.pastInterviewsOpen = event.target.open;
  if (event.target.matches?.('[data-reminder-group="upcoming"]')) state.upcomingFollowupsOpen = event.target.open;
  if (event.target.matches?.('[data-reminder-group="done"]')) state.doneFollowupsOpen = event.target.open;
}, true);

bindQuickFillSurface(quickFillSheet);
bindObjectMenus(resolveObjectMenu);

window.addEventListener("popstate", (event) => { state.scrollTop = Number(event.state?.inboxScroll) || 0; renderRoute({ focus: true }); });
window.addEventListener("resize", () => { if (filterDialog.open) filterDialog.close(); if (routeRoot() === "inbox") renderVirtualRows(); });
document.addEventListener("keydown", handleGlobalKeydown);
document.addEventListener("visibilitychange", () => {
  holdSnackbar("hidden", document.hidden);
  if (!document.hidden) showSubmissionPrompt();
});
window.addEventListener("focus", () => { showSubmissionPrompt(); refreshApplySession(); });
window.addEventListener("focus", () => holdSnackbar("blur", false));
window.addEventListener("blur", () => holdSnackbar("blur", true));
async function resumeFromIdle() {
  if (document.hidden || !navigator.onLine) return;
  try {
    await refreshSession();
    clearTimeout(sessionRetryTimer);
    sessionRetryTimer = null;
    if (state.error || !state.loaded || Date.now() - inboxLoadedAt > 15 * 60 * 1000) await loadInbox();
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

updateOfflineState();
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}
