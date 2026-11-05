// Pure scan logic, testable in node.

export const POLL_MS = 15000;
const DISTANT = 25 * 60 * 1000;
// idle reads before trusting
export const PENDING_READS = 4;

const EXIT_MEANINGS = {
  1: "The scan crashed before it finished.",
  2: "The scan could not start because the database address is missing.",
  3: "A job source came back empty, so the scan stopped on purpose.",
  4: "New matches were found, but notifications are not set up.",
  5: "Sending a notification failed.",
  6: "Some Career Connect mail could not be read.",
};

export function exitMeaning(code) {
  if (code === 0) return "Finished without problems.";
  return EXIT_MEANINGS[code] || "The scan stopped with an error.";
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

export function minutesLabel(ms) {
  if (ms < 60000) return "less than a minute";
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return plural(minutes, "minute", "minutes");
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${plural(hours, "hour", "hours")} ${plural(rest, "minute", "minutes")}` : plural(hours, "hour", "hours");
}

const STATES = new Set(["running", "ok", "failed", "skipped"]);

// only known words reach attributes
export function safeState(value) {
  return STATES.has(value) ? value : "unknown";
}

export function agoLabel(iso, now) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const gap = now - then;
  if (gap < 60000) return "just now";
  if (gap < 24 * 3600000) return `${minutesLabel(gap)} ago`;
  return `on ${new Date(then).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

export function summarizeRun(run, now) {
  const counts = [];
  if (Number.isInteger(run.new_rows)) counts.push(`${plural(run.new_rows, "new posting", "new postings")}`);
  if (Number.isInteger(run.notified)) counts.push(`${plural(run.notified, "notification", "notifications")}`);
  const tail = counts.length ? ` ${counts.join(" and ")}.` : "";
  const when = agoLabel(run.finished_at, now);
  const stamp = when ? ` ${when}` : "";
  if (run.state === "skipped") {
    const why = typeof run.note === "string" && run.note ? run.note.slice(0, 200) : "Another scan was already running.";
    return `Skipped${stamp}. ${why}`;
  }
  if (run.state === "ok") return `Finished${stamp}.${tail}`;
  const code = Number.isInteger(run.exit_code) ? ` (exit code ${run.exit_code})` : "";
  const reason = Number.isInteger(run.exit_code) ? exitMeaning(run.exit_code) : "The scan did not finish.";
  return `Stopped${stamp}. ${reason}${code}${tail}`;
}

export function confirmText(nextScheduledAt, now) {
  let text = "Scan now? Takes about 20 minutes and may send notifications.";
  const next = Date.parse(nextScheduledAt || "");
  if (!Number.isNaN(next) && next > now && next - now <= DISTANT) {
    text += ` The next scheduled scan is in ${minutesLabel(next - now)}, so this may delay or skip it.`;
  }
  return text;
}

export function buildView(model, now) {
  const data = model.data;
  const view = {
    enabled: model.enabled,
    loading: model.loading,
    error: model.error,
    message: model.message,
    confirming: model.confirming,
    starting: model.starting,
    running: false,
    runningLine: "",
    lastLine: "",
    lastState: "",
    triggerLine: "",
    canStart: false,
    disabledReason: "",
    confirmText: "",
    showRetry: Boolean(model.error) && !model.polling,
  };
  if (!model.enabled) return view;
  const runs = data?.runs || [];
  view.running = Boolean(data?.running) || model.starting;
  const active = runs.find((run) => run.state === "running");
  if (view.running) {
    const since = active ? Date.parse(active.started_at) : model.startedLocally;
    view.runningLine = !Number.isFinite(since) ? "Running now." : now - since < 60000 ? "Started just now." : `Running for ${minutesLabel(now - since)}.`;
  }
  const last = runs.find((run) => run.state !== "running");
  if (last) {
    view.lastLine = summarizeRun(last, now);
    view.lastState = safeState(last.state);
    view.triggerLine = last.trigger === "manual" ? "Started by you." : "Started on schedule.";
  }
  const cooldown = Date.parse(data?.cooldown_until || "");
  if (view.running) view.disabledReason = "A scan is already running.";
  else if (!Number.isNaN(cooldown) && cooldown > now) view.disabledReason = `You can scan again in ${minutesLabel(cooldown - now)}.`;
  view.canStart = !view.disabledReason;
  view.confirmText = confirmText(data?.next_scheduled_at, now);
  return view;
}

const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function scanMarkup(view) {
  if (!view.enabled) return "";
  let status;
  if (view.running) status = `<p class="scan-state" data-scan-state="running"><span class="scan-dot" aria-hidden="true"></span>${esc(view.runningLine)}</p>`;
  else if (view.lastLine) status = `<p class="scan-state" data-scan-state="${esc(safeState(view.lastState))}"><span class="scan-dot" aria-hidden="true"></span>${esc(view.lastLine)}</p>`;
  else status = `<p class="scan-state" data-scan-state="none">${view.loading ? "Checking scan history." : "No scans recorded yet."}</p>`;
  const trigger = view.triggerLine && !view.running ? `<p class="scan-note">${esc(view.triggerLine)}</p>` : "";
  const reason = view.disabledReason ? `<p class="scan-note" id="scan-reason">${esc(view.disabledReason)}</p>` : "";
  const error = view.error ? `<p class="scan-note scan-error" role="alert">${esc(view.error)}${view.showRetry ? ' <button class="text-button interactive" type="button" data-scan-retry>Try again</button>' : ""}</p>` : "";
  const message = view.message ? `<p class="scan-note scan-error">${esc(view.message)}</p>` : "";
  let actions;
  if (view.confirming) {
    actions = `<div class="scan-confirm" role="group" aria-labelledby="scan-confirm-text"><p id="scan-confirm-text">${esc(view.confirmText)}</p><div class="scan-buttons"><button class="filled-button interactive" type="button" data-scan-confirm${view.starting ? " disabled" : ""}>${view.starting ? "Starting" : "Confirm"}</button><button class="tonal-button interactive" type="button" data-scan-cancel${view.starting ? " disabled" : ""}>Cancel</button></div></div>`;
  } else {
    const blocked = !view.canStart;
    actions = `<div class="scan-buttons"><button class="tonal-button interactive" type="button" data-scan-start${blocked ? ' aria-disabled="true" aria-describedby="scan-reason"' : ""}>Scan now</button></div>`;
  }
  return `<section class="profile-section scan-section" id="scan-section" aria-labelledby="scan-title"><div class="section-heading"><div><h2 id="scan-title" tabindex="-1">Scanning</h2><p>JobSeer checks for new postings on a schedule. You can also run a check yourself.</p></div></div><div class="scan-card">${status}${trigger}${reason}${error}${message}${actions}</div></section>`;
}

// header seconds, header date, then body
export function retryAfterSeconds(header, bodyValue, now) {
  const text = String(header ?? "").trim();
  if (/^\d+$/.test(text)) return Number(text);
  const date = Date.parse(text);
  if (text && !Number.isNaN(date)) return Math.max(1, Math.ceil((date - now) / 1000));
  const body = Number(bodyValue);
  return Number.isFinite(body) && body > 0 ? body : 0;
}

// first usable element wins
export function focusTarget(wanted, usable) {
  for (const selector of [wanted, "[data-scan-start]", "#scan-title"]) {
    if (selector && usable(selector)) return selector;
  }
  return null;
}

// one visit's announcement memory
function freshVisit() {
  // known stays null until a read works
  return { evidence: new Set(), announced: new Set(), known: null, unnamed: false };
}

// effects are injected for tests
export function createScanController(deps) {
  const {
    fetchEnabled, fetchRuns, startRun, onChange, announce = () => {},
    isOpen = () => true, now = () => Date.now(),
    setTimer = setTimeout, clearTimer = clearTimeout, pollMs = POLL_MS,
  } = deps;
  const model = {
    enabled: false, data: null, loading: false, error: "", message: "",
    confirming: false, starting: false, polling: false, startedLocally: NaN, pending: null,
  };
  let timer = null;
  let seq = 0;
  let pending = null;
  const ANNOUNCED_CAP = 20;
  const KNOWN_CAP = 100;
  let visit = freshVisit();
  let generation = 0;
  let focus = null;

  function view() {
    return buildView(model, now());
  }
  function change() {
    onChange();
  }
  function stopTimer() {
    if (timer !== null) clearTimer(timer);
    timer = null;
    model.polling = false;
  }
  function schedule() {
    stopTimer();
    if (!model.enabled || !isOpen() || !(model.data?.running || model.starting)) return;
    model.polling = true;
    timer = setTimer(tick, pollMs);
  }
  async function tick() {
    timer = null;
    model.polling = false;
    // leaving ends polling
    if (!isOpen()) return;
    await refresh();
  }
  const isFinished = (run) => run.state !== "running" && (Boolean(run.finished_at) || ["ok", "failed", "skipped"].includes(run.state));
  function remember(set, ids, cap) {
    for (const id of ids) set.add(id);
    while (set.size > cap) set.delete(set.values().next().value);
  }
  // only ids we can tie to this visit
  function observe(raw) {
    const runs = raw.runs || [];
    const running = runs.filter((run) => run.state === "running").map((run) => run.id);
    remember(visit.evidence, running, ANNOUNCED_CAP);
    const guess = visit.unnamed && visit.known !== null;
    const fresh = runs.filter((run) => isFinished(run) && !visit.announced.has(run.id)
      && (visit.evidence.has(run.id) || (guess && !visit.known.has(run.id))));
    if (raw.running && !running.length) visit.unnamed = true;
    visit.known = visit.known || new Set();
    remember(visit.known, runs.map((run) => run.id), KNOWN_CAP);
    if (!fresh.length) return;
    visit.unnamed = false;
    remember(visit.announced, fresh.map((run) => run.id), ANNOUNCED_CAP);
    const newest = fresh.reduce((a, b) => (b.id > a.id ? b : a));
    const text = summarizeRun(newest, now());
    announce(newest.state === "skipped" ? text : newest.state === "ok" ? `Scan finished. ${text}` : `Scan stopped. ${text}`);
  }
  // only the newest request may land
  function supersede() {
    seq += 1;
    if (pending) pending.abort();
    pending = null;
  }
  // our run decides, by id
  function withStart(next) {
    const wait = model.pending;
    if (wait === null) return next;
    const mine = wait.id === null ? null : (next.runs || []).find((run) => run.id === wait.id);
    if (mine) {
      // an open row means running
      if (mine.state === "running" && !mine.finished_at) return { ...next, running: true };
      model.pending = null;
      return next;
    }
    if (next.running) return next;
    wait.reads += 1;
    // bounded, then trust the server
    if (wait.reads >= PENDING_READS) {
      model.pending = null;
      return next;
    }
    return { ...next, running: true };
  }
  async function refresh() {
    supersede();
    const mine = seq;
    const era = generation;
    pending = typeof AbortController === "function" ? new AbortController() : null;
    const current = () => mine === seq && era === generation;
    try {
      const raw = await fetchRuns(pending?.signal);
      if (!current()) return;
      const next = withStart(raw);
      observe(raw);
      model.data = next;
      model.error = "";
      if (next.running) model.starting = false;
      if (next.running && model.confirming && !model.starting) {
        // started elsewhere
        model.confirming = false;
        focus = "start";
      }
    } catch {
      if (!current()) return;
      // keep what was shown
      model.error = "Couldn't check the scan just now.";
    } finally {
      if (current()) {
        pending = null;
        model.loading = false;
        schedule();
        change();
      }
    }
  }

  return {
    view,
    announcedSize: () => visit.announced.size,
    consumeFocus() {
      const hint = focus;
      focus = null;
      return hint;
    },
    async open() {
      generation += 1;
      visit = freshVisit();
      model.loading = true;
      model.error = "";
      change();
      const mine = generation;
      let enabled = false;
      try {
        enabled = Boolean(await fetchEnabled());
      } catch {
        enabled = false;
      }
      // an older visit must not decide
      if (mine !== generation) return;
      model.enabled = enabled;
      if (!model.enabled) {
        model.loading = false;
        change();
        return;
      }
      await refresh();
    },
    close() {
      generation += 1;
      visit = freshVisit();
      supersede();
      stopTimer();
    },
    retry() {
      model.error = "";
      return refresh();
    },
    askConfirm() {
      const state = view();
      if (!state.canStart) return;
      model.confirming = true;
      model.message = "";
      focus = "confirm";
      change();
    },
    cancel() {
      if (model.starting) return;
      model.confirming = false;
      focus = "start";
      change();
    },
    async confirm() {
      if (model.starting || !model.confirming) return;
      model.starting = true;
      model.message = "";
      // older reads predate this start
      supersede();
      change();
      let result;
      try {
        result = await startRun();
      } catch {
        result = { status: 0 };
      }
      model.starting = false;
      model.confirming = false;
      focus = "start";
      if (result.status === 202) {
        model.startedLocally = now();
        const runId = Number.isInteger(result.runId) && result.runId > 0 ? result.runId : null;
        model.pending = { id: runId, reads: 0 };
        supersede();
        model.data = { ...(model.data || { runs: [] }), running: true };
        announce("Scan started. It takes about 20 minutes.");
        if (model.pending.id !== null) remember(visit.evidence, [model.pending.id], ANNOUNCED_CAP);
        // poll even if reads fail
        schedule();
        change();
        await refresh();
        return;
      }
      if (result.status === 409) model.message = "A scan is already running.";
      else if (result.status === 429) {
        const wait = retryAfterSeconds(result.retryAfter, result.retryAfterBody, now());
        model.message = wait > 0
          ? `A scan started recently. Try again in ${minutesLabel(wait * 1000)}.`
          : "A scan started recently. Try again in a few minutes.";
      } else if (result.status === 403) model.message = "Your session expired. Reload the page and try again.";
      else model.message = "Couldn't start a scan. Try again shortly.";
      announce(model.message);
      change();
      // the page may be stale
      await refresh();
    },
  };
}
