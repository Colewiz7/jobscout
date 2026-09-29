"use strict";

const state = {
  jobs: [], profile: {}, selectedKey: null, filter: "all", sort: "score",
  search: "", copyValues: new Map(), notesTimer: null,
};

const statusLabels = {
  new: "New", saved: "Saved", preparing: "Preparing", applied: "Applied",
  interview: "Interview", offer: "Offer", rejected: "Rejected", skipped: "Skipped",
};
const activeStatuses = new Set(["applied", "interview", "offer", "rejected"]);
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));

function icon(name) {
  const paths = {
    pin: '<path d="M12 21s6-5.1 6-11a6 6 0 1 0-12 0c0 5.9 6 11 6 11Z"/><circle cx="12" cy="10" r="2"/>',
    copy: '<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    open: '<path d="M14 5h5v5m0-5-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    bookmark: '<path d="M7 3h10a2 2 0 0 1 2 2v16l-7-4-7 4V5a2 2 0 0 1 2-2Z"/>',
    briefcase: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2m-12 5h18M10 12v2h4v-2"/>',
    database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    arrow: '<path d="m9 18 6-6-6-6"/>',
    kit: '<path d="M8 4h8m-7 0v3h6V4m-8 3h10a2 2 0 0 1 2 2v11H5V9a2 2 0 0 1 2-2Zm2 5h6m-6 4h4"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || ""}</svg>`;
}

function initials(company) {
  const words = String(company || "?").trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

function displayTitle(title) {
  return String(title || "").replace(/co-op/gi, (value) => value.replace("-", "‑"));
}

function ageLabel(days) {
  if (days === null || days === undefined) return "age unknown";
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function validUrl(value) {
  try { return ["http:", "https:"].includes(new URL(value).protocol); } catch { return false; }
}

function selectedJob() { return state.jobs.find((job) => job.dedupe_key === state.selectedKey); }

function visibleJobs() {
  const query = state.search.trim().toLowerCase();
  const filtered = state.jobs.filter((job) => {
    if (state.filter === "new" && job.status !== "new") return false;
    if (state.filter === "saved" && !["saved", "preparing"].includes(job.status)) return false;
    if (state.filter === "active" && !activeStatuses.has(job.status)) return false;
    if (!query) return true;
    return [job.company, job.title, job.location, job.terms].join(" ").toLowerCase().includes(query);
  });
  return filtered.sort((a, b) => {
    if (state.sort === "company") return a.company.localeCompare(b.company) || a.title.localeCompare(b.title);
    if (state.sort === "newest") return (a.age_days ?? 9999) - (b.age_days ?? 9999);
    return (b.score ?? -9999) - (a.score ?? -9999) || (a.age_days ?? 9999) - (b.age_days ?? 9999);
  });
}

function renderSummary() {
  const ready = state.jobs.filter((job) => ["new", "saved", "preparing"].includes(job.status)).length;
  const active = state.jobs.filter((job) => ["applied", "interview", "offer"].includes(job.status)).length;
  $("#ready-count").textContent = ready;
  $("#active-count").textContent = active;
}

function renderJobs() {
  const jobs = visibleJobs();
  $("#job-count").textContent = jobs.length;
  $("#queue-description").textContent = state.search ? `${jobs.length} matching` : {
    score: "Best match first", newest: "Freshest first", company: "Company A–Z",
  }[state.sort];
  if (!jobs.length) {
    $("#job-list").innerHTML = '<div class="list-empty"><strong>No jobs here</strong>Try another filter or search.</div>';
    return;
  }
  $("#job-list").innerHTML = jobs.map((job) => `
    <button class="job-card${job.dedupe_key === state.selectedKey ? " selected" : ""}" type="button"
      role="option" aria-selected="${job.dedupe_key === state.selectedKey}" data-job-key="${esc(job.dedupe_key)}">
      <span class="job-card-top">
        <span class="company-mark">${esc(initials(job.company))}</span>
        <span class="job-card-copy">
          <h3>${esc(displayTitle(job.title))}</h3>
          <span class="company-line">${esc(job.company)}</span>
        </span>
        ${job.score === null || job.score === undefined ? "" : `<span class="job-score">${esc(job.score)}</span>`}
      </span>
      <span class="job-location">${icon("pin")} ${esc(job.location || "Location not listed")}</span>
      <span class="job-meta">
        <span class="status-chip" data-status="${esc(job.status)}">${esc(statusLabels[job.status] || job.status)}</span>
        ${job.terms ? `<span class="term-chip">${esc(job.terms)}</span>` : ""}
        <span class="age-label">${esc(ageLabel(job.age_days))}</span>
      </span>
    </button>`).join("");
}

function matchSignals(job) {
  const labels = {
    role_named: ["Strong role match", "The title names the work directly"],
    role_hinted: ["Possible role match", "The title hints at your target work"],
    co_op: ["Co-op", "Preferred early-career format"],
    internship: ["Internship", "Early-career role"],
    wanted_term: ["Right term", "Names your target season"],
    fresh: ["Fresh posting", "Posted within the last week"],
    off_target_discipline: ["Check discipline", "Title may share infrastructure vocabulary"],
    stale_term: ["Older term", "Names an earlier recruiting cycle"],
  };
  const detail = job.score_detail || {};
  const rows = Object.entries(detail).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return '<div class="match-signal"><strong>Not scored yet</strong><span>The next scout run will explain this match.</span></div>';
  return rows.map(([key, points]) => {
    const [title, description] = labels[key] || [key.replaceAll("_", " "), "Ranking signal"];
    return `<div class="match-signal ${points >= 0 ? "positive" : "negative"}"><strong>${esc(points > 0 ? `+${points} · ${title}` : `${points} · ${title}`)}</strong><span>${esc(description)}</span></div>`;
  }).join("");
}

function flowSteps(job) {
  const order = ["saved", "preparing", "applied", "interview"];
  const current = order.indexOf(job.status);
  const specialComplete = ["offer"].includes(job.status) ? order.length : current;
  const items = [
    ["saved", "Shortlist", "Keep it in the queue"],
    ["preparing", "Tailor", "Resume and answers"],
    ["applied", "Submit", "Record the application"],
    ["interview", "Follow up", "Track the next conversation"],
  ];
  return items.map(([status, title, copy], index) => `
    <button class="flow-step ${specialComplete >= index ? "complete" : ""}" type="button" data-set-status="${status}">
      <i>${specialComplete >= index ? "✓" : index + 1}</i><strong>${title}</strong><span>${copy}</span>
    </button>`).join("");
}

function renderDetail() {
  const job = selectedJob();
  $("#detail-empty").hidden = Boolean(job);
  $("#detail-content").hidden = !job;
  if (!job) return;
  const apply = validUrl(job.url) ? `<a class="filled-button" href="${esc(job.url)}" target="_blank" rel="noopener noreferrer">Open application ${icon("open")}</a>` : "";
  $("#detail-content").innerHTML = `<div class="detail-wrap">
    <button class="text-button mobile-back" type="button" data-view="jobs">${icon("back")} Back to jobs</button>
    <div class="job-hero">
      <div class="company-mark">${esc(initials(job.company))}</div>
      <div>
        <p class="job-kicker">${esc(job.company)}</p>
        <h2>${esc(displayTitle(job.title))}</h2>
        <p class="hero-location">${esc(job.location || "Location not listed")}${job.terms ? ` · ${esc(job.terms)}` : ""}</p>
      </div>
      <div class="score-hero"><strong>${job.score ?? "—"}</strong><span>match score</span></div>
    </div>
    <div class="hero-actions">
      ${apply}
      <button class="tonal-button" type="button" data-copy="job-link">${icon("copy")} Copy job link</button>
      <button class="tonal-button kit-open-button" type="button" data-open-kit>${icon("kit")} Application kit</button>
      <div class="status-select"><label for="status-select">Status</label><select id="status-select">
        ${Object.entries(statusLabels).map(([value, label]) => `<option value="${value}"${job.status === value ? " selected" : ""}>${label}</option>`).join("")}
      </select></div>
    </div>
    <section class="section-block">
      <div class="section-title"><h3>Why it surfaced</h3><p>Every point stays explainable</p></div>
      <div class="tonal-card match-grid">${matchSignals(job)}</div>
    </section>
    <section class="section-block">
      <div class="section-title"><h3>Application path</h3><p>One click keeps your place</p></div>
      <div class="tonal-card application-flow">${flowSteps(job)}</div>
    </section>
    <section class="section-block">
      <div class="section-title"><h3>Notes for this application</h3><p id="notes-status">Saved with the job</p></div>
      <textarea class="notes-area" id="job-notes" maxlength="20000" placeholder="Contacts, application questions, follow-up dates, or details to mention…">${esc(job.notes || "")}</textarea>
      <div class="notes-footer"><span>Autosaves after you stop typing</span><span><span id="notes-count">${(job.notes || "").length}</span> / 20,000</span></div>
    </section>
    <p class="job-source">${icon("database")} Found via ${esc(job.sources || "jobscout")} · <code>${esc(job.dedupe_key)}</code></p>
  </div>`;
  state.copyValues.set("job-link", job.url || "");
}

function tailoredMaterial(job) {
  if (!job) return { intro: "Select a job to tailor this material.", highlights: [] };
  const profile = state.profile;
  const headline = profile.headline || `${profile.degree || "computing"} student at ${profile.school || "RIT"}`;
  const experience = profile.application_intro || profile.summary || "";
  const intro = `I'm a ${headline}, and I'm interested in the ${job.title} role at ${job.company}. ${experience}`.trim();
  const haystack = `${job.title} ${job.company}`.toLowerCase();
  const highlights = (profile.highlights || []).map((item, index) => ({
    ...item, index, matches: (item.tags || []).filter((tag) => haystack.includes(String(tag).toLowerCase())).length,
  })).sort((a, b) => b.matches - a.matches || a.index - b.index).slice(0, 3);
  return { intro, highlights };
}

function registerCopy(id, value) { state.copyValues.set(id, String(value || "")); return id; }

function copyRow(label, value, id) {
  if (!value) return "";
  registerCopy(id, value);
  return `<button class="copy-row" type="button" data-copy="${esc(id)}"><span class="copy-row-copy"><small>${esc(label)}</small><strong>${esc(value)}</strong></span>${icon("copy")}</button>`;
}

function renderKit() {
  state.copyValues = new Map([...state.copyValues].filter(([key]) => key === "job-link"));
  const profile = state.profile;
  const job = selectedJob();
  const tailored = tailoredMaterial(job);
  const personal = [
    ["Full name", profile.name, "profile-name"], ["Email", profile.email, "profile-email"],
    ["Phone", profile.phone, "profile-phone"], ["Location", profile.location, "profile-location"],
    ["School", profile.school, "profile-school"], ["Degree", profile.degree, "profile-degree"],
    ["Graduation", profile.graduation, "profile-graduation"], ["GPA", profile.gpa, "profile-gpa"],
  ].map((row) => copyRow(...row)).join("");
  registerCopy("tailored-intro", tailored.intro);
  const highlightHtml = tailored.highlights.map((item, index) => {
    const id = registerCopy(`highlight-${index}`, item.text);
    return `<button class="highlight-item" type="button" data-copy="${id}"><i>${index + 1}</i><p>${esc(item.text)}</p></button>`;
  }).join("");
  const answers = Object.entries(profile.quick_answers || {}).map(([label, value], index) => copyRow(label, value, `answer-${index}`)).join("");
  const links = Object.entries(profile.links || {}).map(([label, value], index) => copyRow(label, value, `link-${index}`)).join("");
  $("#kit-content").innerHTML = `
    <section class="kit-section"><div class="kit-section-heading"><h3>Personal details</h3><span class="kit-section-label">profile</span></div>${personal || '<p class="kit-empty">Add details to profile.yaml.</p>'}</section>
    <section class="kit-section"><div class="kit-section-heading"><h3>Tailored intro</h3><span class="kit-section-label">${job ? esc(job.company) : "select a job"}</span></div>
      <button class="tailored-copy" type="button" data-copy="tailored-intro"><p>${esc(tailored.intro)}</p><footer>${icon("copy")} Copy introduction</footer></button></section>
    ${job ? `<section class="kit-section"><div class="kit-section-heading"><h3>Best evidence</h3><span class="kit-section-label">role matched</span></div><div class="highlight-list">${highlightHtml}</div></section>` : ""}
    <section class="kit-section"><div class="kit-section-heading"><h3>Quick answers</h3><span class="kit-section-label">common forms</span></div>${answers}</section>
    <section class="kit-section"><div class="kit-section-heading"><h3>Links & files</h3><span class="kit-section-label">public</span></div>${links}
      ${validUrl(profile.resume_url) ? `<a class="filled-button" href="${esc(profile.resume_url)}" target="_blank" rel="noopener noreferrer">Open resume ${icon("open")}</a>` : ""}
    </section>`;
}

function renderAll() { renderSummary(); renderJobs(); renderDetail(); renderKit(); }

async function saveJob(job, message = "Application updated") {
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(job.dedupe_key)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: job.status, notes: job.notes || "" }),
    });
    if (!response.ok) throw new Error("save failed");
    toast(message);
  } catch {
    toast("Could not save — your change is still on screen");
  }
}

function setStatus(status) {
  const job = selectedJob();
  if (!job || !statusLabels[status]) return;
  job.status = status;
  renderAll();
  saveJob(job, `Marked ${statusLabels[status].toLowerCase()}`);
}

async function copyText(value, label = "Copied") {
  if (!value) { toast("Nothing to copy yet"); return; }
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const area = document.createElement("textarea"); area.value = value; area.style.position = "fixed"; area.style.opacity = "0";
    document.body.append(area); area.select(); document.execCommand("copy"); area.remove();
  }
  toast(label);
}

function copyAll() {
  const profile = state.profile;
  const job = selectedJob();
  const tailored = tailoredMaterial(job);
  const lines = [
    profile.name, profile.email, profile.phone, profile.location,
    [profile.degree, profile.school, profile.graduation].filter(Boolean).join(" — "),
    profile.gpa ? `GPA: ${profile.gpa}` : "", profile.availability,
    "", "Links", ...Object.entries(profile.links || {}).map(([label, value]) => `${label}: ${value}`),
    job ? "" : null, job ? `Tailored for ${job.company} — ${job.title}` : null,
    job ? tailored.intro : null,
    ...tailored.highlights.map((item) => `• ${item.text}`),
  ].filter((value) => value !== null && value !== undefined && value !== "");
  copyText(lines.join("\n"), "Application kit copied");
}

function toast(message) {
  const node = document.createElement("div"); node.className = "toast"; node.textContent = message;
  $("#toast-region").append(node); setTimeout(() => node.remove(), 2300);
}

function showView(view) {
  document.body.dataset.mobileView = view;
  $$('[data-view]', $(".mobile-nav")).forEach((button) => button.classList.toggle("selected", button.dataset.view === view));
  if (view === "detail") $("#job-detail").focus({ preventScroll: true });
}

function selectJob(key, switchView = true) {
  state.selectedKey = key;
  const url = new URL(window.location.href);
  url.searchParams.set("job", key);
  history.replaceState(null, "", url);
  renderAll();
  if (switchView && window.matchMedia("(max-width:760px)").matches) showView("detail");
}

function cycleTheme() {
  const current = localStorage.getItem("jobscout-theme") || "system";
  const next = current === "system" ? "light" : current === "light" ? "dark" : "system";
  localStorage.setItem("jobscout-theme", next);
  if (next === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = next;
  toast(`${next[0].toUpperCase() + next.slice(1)} theme`);
}

function installEvents() {
  $("#job-search").addEventListener("input", (event) => { state.search = event.target.value; renderJobs(); });
  $("#job-sort").addEventListener("change", (event) => { state.sort = event.target.value; renderJobs(); });
  $("#copy-all").addEventListener("click", copyAll);
  $("#theme-button").addEventListener("click", cycleTheme);
  $$(".filter-chip").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter; $$(".filter-chip").forEach((item) => item.classList.toggle("selected", item === button)); renderJobs();
  }));
  $(".mobile-nav").addEventListener("click", (event) => { const button = event.target.closest("[data-view]"); if (button) showView(button.dataset.view); });
  document.addEventListener("click", (event) => {
    const jobCard = event.target.closest("[data-job-key]"); if (jobCard) { selectJob(jobCard.dataset.jobKey); return; }
    const copy = event.target.closest("[data-copy]"); if (copy) { copyText(state.copyValues.get(copy.dataset.copy), "Copied to clipboard"); return; }
    const status = event.target.closest("[data-set-status]"); if (status) { setStatus(status.dataset.setStatus); return; }
    const view = event.target.closest("[data-view]"); if (view) { showView(view.dataset.view); return; }
    if (event.target.closest("[data-open-kit]")) {
      if (window.matchMedia("(max-width:760px)").matches) showView("kit");
      else document.body.dataset.kitOpen = document.body.dataset.kitOpen === "true" ? "false" : "true";
    }
  });
  document.addEventListener("change", (event) => { if (event.target.id === "status-select") setStatus(event.target.value); });
  document.addEventListener("input", (event) => {
    if (event.target.id !== "job-notes") return;
    const job = selectedJob(); if (!job) return;
    job.notes = event.target.value; $("#notes-count").textContent = job.notes.length; $("#notes-status").textContent = "Saving…";
    clearTimeout(state.notesTimer); state.notesTimer = setTimeout(async () => { await saveJob(job, "Notes saved"); const label = $("#notes-status"); if (label) label.textContent = "Saved with the job"; }, 550);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { event.preventDefault(); $("#job-search").focus(); }
    if (["j", "k"].includes(event.key) && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      const jobs = visibleJobs(); const index = Math.max(0, jobs.findIndex((job) => job.dedupe_key === state.selectedKey));
      const next = event.key === "j" ? Math.min(jobs.length - 1, index + 1) : Math.max(0, index - 1); if (jobs[next]) selectJob(jobs[next].dedupe_key, false);
    }
    if (event.key === "Escape") { document.body.dataset.kitOpen = "false"; if (window.matchMedia("(max-width:760px)").matches) showView("jobs"); }
  });
}

async function init() {
  const savedTheme = localStorage.getItem("jobscout-theme");
  if (["light", "dark"].includes(savedTheme)) document.documentElement.dataset.theme = savedTheme;
  installEvents();
  try {
    const [jobsResponse, profileResponse] = await Promise.all([fetch("/api/jobs"), fetch("/api/profile")]);
    if (!jobsResponse.ok || !profileResponse.ok) throw new Error("request failed");
    const payload = await jobsResponse.json(); state.profile = await profileResponse.json(); state.jobs = payload.jobs || [];
    const requested = new URLSearchParams(window.location.search).get("job");
    state.selectedKey = state.jobs.some((job) => job.dedupe_key === requested) ? requested : (visibleJobs()[0]?.dedupe_key || null);
    $("#sync-label").textContent = state.jobs.length ? "scout is current" : "no open jobs";
    renderAll();
    const requestedView = new URLSearchParams(window.location.search).get("view");
    if (["jobs", "detail", "kit"].includes(requestedView)) showView(requestedView);
  } catch {
    $("#sync-label").textContent = "could not load jobs"; $(".sync-state").classList.add("error");
    $("#job-list").innerHTML = '<div class="list-empty"><strong>Could not reach JobScout</strong>Check the dashboard service and database connection.</div>';
    renderKit();
  }
}

init();
