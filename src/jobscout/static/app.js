"use strict";

const routeView = document.querySelector("#route-view");
const commandDialog = document.querySelector("#command-dialog");
const commandInput = document.querySelector("#command-input");
const commandResults = document.querySelector("#command-results");
const shortcutDialog = document.querySelector("#shortcut-dialog");
const mediaReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const icons = {
  inbox: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4zM4 14h5l2 2h2l2-2h5"/></svg>',
  queue: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5h12M6 12h12M6 19h8"/></svg>',
  tracker: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19V9m7 10V4m7 15v-7"/></svg>',
  companies: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V8l8-4v16m0-9h8v9M8 9h1m-1 4h1m-1 4h1m8-2h1"/></svg>',
  profile: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M5 21a7 7 0 0 1 14 0"/></svg>',
  shortcuts: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.8 9a2.4 2.4 0 0 1 4.7.7c0 2.3-2.5 2.3-2.5 4.3m0 3h.01"/></svg>',
};

const pages = {
  inbox: {
    title: "Inbox",
    headline: "Decide what deserves your time.",
    description: "New roles will land here for a quick save, dismissal, or move to your application queue.",
    action: ["Review queue", "/queue"],
  },
  queue: {
    title: "Queue",
    headline: "Apply with a clear desk.",
    description: "Queued roles will be ordered for focused, one-at-a-time application sessions.",
    action: ["Find roles", "/inbox"],
  },
  tracker: {
    title: "Tracker",
    headline: "Keep the next step visible.",
    description: "Applications, follow-ups, interviews, and outcomes will stay together here.",
    action: ["Open inbox", "/inbox"],
  },
  companies: {
    title: "Companies",
    headline: "Remember every conversation.",
    description: "Company pages will collect roles, applications, contacts, notes, and account details.",
    action: ["Open inbox", "/inbox"],
  },
  profile: {
    title: "Profile",
    headline: "Write it once. Use it calmly.",
    description: "Your fields, documents, answer templates, rules, and notification settings will live here.",
    action: ["Show shortcuts", "shortcuts"],
  },
};

const commands = [
  { id: "inbox", label: "Go to Inbox", detail: "Triage new roles", route: "/inbox", shortcut: "G I" },
  { id: "queue", label: "Go to Queue", detail: "Apply one job at a time", route: "/queue", shortcut: "G Q" },
  { id: "tracker", label: "Go to Tracker", detail: "Follow up and learn", route: "/tracker", shortcut: "G T" },
  { id: "companies", label: "Go to Companies", detail: "Open company history", route: "/companies", shortcut: "G C" },
  { id: "profile", label: "Go to Profile", detail: "Manage reusable application data", route: "/profile", shortcut: "G P" },
  { id: "shortcuts", label: "Show keyboard shortcuts", detail: "Review the power-user map", action: "shortcuts", shortcut: "?" },
];

let commandSelection = 0;
let commandMatches = commands;
let dialogTrigger = null;
let goChordUntil = 0;

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function routeRoot(pathname = window.location.pathname) {
  return pathname.split("/").filter(Boolean)[0] || "inbox";
}

function renderRoute({ focus = false } = {}) {
  const root = routeRoot();
  const page = pages[root] || pages.inbox;
  const isSession = root === "queue" && window.location.pathname.startsWith("/queue/session/");
  const headline = isSession ? "Application sessions start here." : page.headline;
  const description = isSession
    ? "Choose jobs from the queue before beginning a focused session."
    : page.description;
  const action = isSession ? ["Return to queue", "/queue"] : page.action;
  const actionMarkup = action[1] === "shortcuts"
    ? `<button class="tonal-button interactive" type="button" data-open-shortcuts>${escapeHtml(action[0])}</button>`
    : `<a class="tonal-button interactive" href="${action[1]}" data-route>${escapeHtml(action[0])}</a>`;

  routeView.innerHTML = `
    <section class="page-shell" aria-labelledby="page-title">
      <div class="empty-state">
        <div class="empty-mark" aria-hidden="true">${icons[root] || icons.inbox}</div>
        <h1 id="page-title">${escapeHtml(headline)}</h1>
        <p>${escapeHtml(description)}</p>
        ${actionMarkup}
      </div>
    </section>`;

  document.title = `${isSession ? "Session" : page.title} — JobSeer`;
  document.querySelectorAll("[data-nav]").forEach((item) => {
    if (item.dataset.nav === root) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  if (focus) routeView.focus({ preventScroll: true });
}

function navigate(url, { replace = false } = {}) {
  const destination = new URL(url, window.location.origin);
  if (destination.origin !== window.location.origin) return;
  const method = replace ? "replaceState" : "pushState";
  history[method]({}, "", `${destination.pathname}${destination.search}${destination.hash}`);
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

function renderCommands() {
  const query = commandInput.value;
  commandMatches = commands
    .map((command) => ({
      command,
      score: fuzzyScore(`${command.label} ${command.detail}`, query),
    }))
    .filter((entry) => entry.score >= 0)
    .sort((left, right) => right.score - left.score)
    .map((entry) => entry.command);
  commandSelection = Math.min(commandSelection, Math.max(0, commandMatches.length - 1));
  if (!commandMatches.length) {
    commandResults.innerHTML = '<p class="command-empty">No matching commands</p>';
    return;
  }
  commandResults.innerHTML = commandMatches.map((command, index) => `
    <button class="command-item interactive" type="button" role="option"
      aria-selected="${index === commandSelection}" data-command="${command.id}">
      ${icons[command.id] || icons.shortcuts}
      <span><strong>${escapeHtml(command.label)}</strong><small>${escapeHtml(command.detail)}</small></span>
      <kbd>${escapeHtml(command.shortcut)}</kbd>
    </button>`).join("");
  commandResults.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function rememberDialogTrigger(trigger) {
  dialogTrigger = trigger instanceof HTMLElement ? trigger : document.activeElement;
}

function restoreDialogTrigger() {
  if (dialogTrigger instanceof HTMLElement && dialogTrigger.isConnected) dialogTrigger.focus();
  dialogTrigger = null;
}

function openCommand(trigger) {
  if (commandDialog.open) return;
  if (shortcutDialog.open) shortcutDialog.close();
  rememberDialogTrigger(trigger);
  commandInput.value = "";
  commandSelection = 0;
  renderCommands();
  commandDialog.showModal();
  commandInput.focus();
}

function openShortcuts(trigger) {
  if (shortcutDialog.open) return;
  if (commandDialog.open) commandDialog.close();
  rememberDialogTrigger(trigger);
  shortcutDialog.showModal();
  shortcutDialog.querySelector("button")?.focus();
}

function runCommand(id) {
  const command = commands.find((item) => item.id === id);
  if (!command) return;
  commandDialog.close();
  if (command.route) navigate(command.route);
  if (command.action === "shortcuts") openShortcuts(document.querySelector("[data-open-shortcuts]"));
}

function shortcutScopeAllows(event) {
  const target = event.target;
  return !(target instanceof HTMLElement && (
    target.matches("input, textarea, select, [contenteditable='true'], [role='textbox']")
    || target.closest("[contenteditable='true']")
  ));
}

function handleGlobalKeydown(event) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openCommand(document.activeElement);
    return;
  }
  if (commandDialog.open || shortcutDialog.open || !shortcutScopeAllows(event)) return;
  if (event.key === "?") {
    event.preventDefault();
    openShortcuts(document.activeElement);
    return;
  }
  const now = performance.now();
  const key = event.key.toLowerCase();
  if (key === "g") {
    goChordUntil = now + 1000;
    return;
  }
  if (now <= goChordUntil) {
    const routes = { i: "/inbox", q: "/queue", t: "/tracker", c: "/companies", p: "/profile" };
    goChordUntil = 0;
    if (routes[key]) {
      event.preventDefault();
      navigate(routes[key]);
    }
  }
}

function updateThemeColor() {
  const color = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", color);
}

document.addEventListener("click", (event) => {
  const route = event.target.closest("[data-route]");
  if (route instanceof HTMLAnchorElement) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(route.href);
    return;
  }
  const commandTrigger = event.target.closest("[data-open-command]");
  if (commandTrigger) {
    openCommand(commandTrigger);
    return;
  }
  const shortcutTrigger = event.target.closest("[data-open-shortcuts]");
  if (shortcutTrigger) {
    openShortcuts(shortcutTrigger);
    return;
  }
  const close = event.target.closest("[data-close-dialog]");
  if (close) {
    close.closest("dialog")?.close();
    return;
  }
  const command = event.target.closest("[data-command]");
  if (command) runCommand(command.dataset.command);
});

commandInput.addEventListener("input", () => {
  commandSelection = 0;
  renderCommands();
});

commandInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    commandSelection = Math.min(commandMatches.length - 1, commandSelection + 1);
    renderCommands();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    commandSelection = Math.max(0, commandSelection - 1);
    renderCommands();
  } else if (event.key === "Enter" && commandMatches[commandSelection]) {
    event.preventDefault();
    runCommand(commandMatches[commandSelection].id);
  }
});

for (const dialog of [commandDialog, shortcutDialog]) {
  dialog.addEventListener("close", restoreDialogTrigger);
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    const outside = event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom;
    if (outside) dialog.close();
  });
}

window.addEventListener("popstate", () => renderRoute({ focus: true }));
document.addEventListener("keydown", handleGlobalKeydown);

if (window.location.pathname === "/" || window.location.pathname === "/index.html") {
  navigate("/inbox", { replace: true });
} else {
  renderRoute();
}
updateThemeColor();

if (!mediaReducedMotion.matches && document.fonts) {
  document.fonts.ready.then(() => document.documentElement.classList.add("fonts-ready"));
}
