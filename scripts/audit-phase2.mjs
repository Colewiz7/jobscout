#!/usr/bin/env node

import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const fixture = JSON.parse(await readFile(join(root, "tests/fixtures/dashboard_2000.seed.json"), "utf8"));
const profile = join(root, "config/profile.seed.json");
const chromium = process.env.CHROMIUM || "/usr/bin/chromium";

async function freePort() {
  const server = createServer();
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const { port } = server.address();
  await new Promise((resolveClose) => server.close(resolveClose));
  return port;
}

async function waitForHttp(url, attempts = 100) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return response;
    } catch { /* Process startup. */ }
    await new Promise((resolveWait) => setTimeout(resolveWait, 50));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

class Cdp {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.socket = new WebSocket(url);
    this.ready = new Promise((resolveReady, rejectReady) => {
      this.socket.addEventListener("open", resolveReady, { once: true });
      this.socket.addEventListener("error", rejectReady, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (!message.id) { this.events.push(message); return; }
      if (!this.pending.has(message.id)) return;
      const { resolveCall, rejectCall } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) rejectCall(new Error(message.error.message));
      else resolveCall(message.result);
    });
  }

  async call(method, params = {}) {
    await this.ready;
    const id = this.nextId;
    this.nextId += 1;
    const response = new Promise((resolveCall, rejectCall) => {
      this.pending.set(id, { resolveCall, rejectCall });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return response;
  }

  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }

  close() { this.socket.close(); }
}

const dashboardPort = await freePort();
const browserPort = await freePort();
const browserData = await mkdtemp(join(tmpdir(), "jobseer-audit-"));
const dashboard = spawn(
  join(root, ".venv/bin/python"),
  ["-m", "jobscout", "dashboard", "--demo", "--host", "127.0.0.1", "--port", String(dashboardPort), "--profile", profile],
  {
    cwd: root,
    env: {
      ...process.env,
      JOBSCOUT_DEMO_COUNT: String(fixture.count),
      JOBSCOUT_DEMO_SEED: String(fixture.seed),
    },
    stdio: ["ignore", "ignore", "pipe"],
  },
);
const browser = spawn(
  chromium,
  [
    "--headless=new", "--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox",
    `--remote-debugging-port=${browserPort}`, `--user-data-dir=${browserData}`, "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);

let cdp;
try {
  const base = `http://127.0.0.1:${dashboardPort}`;
  await waitForHttp(`${base}/healthz`);
  await waitForHttp(`http://127.0.0.1:${browserPort}/json/version`);
  const page = await fetch(`http://127.0.0.1:${browserPort}/json/list`)
    .then((response) => response.json())
    .then((targets) => targets.find((target) => target.type === "page"));
  if (!page) throw new Error("Chromium did not expose a page target");
  cdp = new Cdp(page.webSocketDebuggerUrl);
  await cdp.call("Page.enable");
  await cdp.call("Runtime.enable");
  await cdp.call("Performance.enable");
  await cdp.call("PerformanceTimeline.enable", {
    eventTypes: ["largest-contentful-paint", "layout-shift"],
  });
  await cdp.call("Page.bringToFront");
  await cdp.call("Emulation.setDeviceMetricsOverride", {
    width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await cdp.call("Page.addScriptToEvaluateOnNewDocument", {
    source: `
      window.__audit = { lcp: 0, cls: 0 };
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) window.__audit.lcp = entry.startTime;
      }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.__audit.cls += entry.value;
      }).observe({ type: "layout-shift", buffered: true });
    `,
  });
  await cdp.call("Page.navigate", { url: `${base}/inbox?status=all` });
  let navigationStarted = Date.now();
  for (let measurement = 0; measurement < 3; measurement += 1) {
    if (measurement) {
      await cdp.call("Page.reload", { ignoreCache: true });
      navigationStarted = Date.now();
    }
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await cdp.evaluate("Boolean(document.querySelector('.job-row')?.getBoundingClientRect().height === 72 && document.querySelector('#job-title'))");
      if (ready) break;
      if (attempt === 99) throw new Error("Inbox did not finish rendering");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 600));
    if (await cdp.evaluate("window.__audit.lcp > 0")) break;
  }
  const renderReadyMs = Date.now() - navigationStarted;

  const jobCount = await fetch(`${base}/api/v1/jobs`).then((response) => response.json()).then((data) => data.jobs.length);
  const desktop = await cdp.evaluate(`(() => {
    const rows = [...document.querySelectorAll('.job-row')];
    const focusables = [...document.querySelectorAll('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
      .filter((element) => !element.hidden && element.getClientRects().length > 0);
    const unnamed = focusables.filter((element) => {
      const labelledBy = element.getAttribute('aria-labelledby');
      return !(element.getAttribute('aria-label') || element.textContent.trim() || element.title ||
        (labelledBy && document.getElementById(labelledBy)?.textContent.trim()) ||
        (element.id && document.querySelector('label[for="' + CSS.escape(element.id) + '"]')?.textContent.trim()));
    });
    const ids = [...document.querySelectorAll('[id]')].map((element) => element.id);
    const rowHeights = rows.map((row) => row.getBoundingClientRect().height);
    const start = performance.now();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true }));
    const interaction = performance.now() - start;
    return {
      renderedRows: rows.length,
      rowHeights,
      duplicateIds: ids.filter((id, index) => ids.indexOf(id) !== index),
      unnamedControls: unnamed.map((element) => element.outerHTML.slice(0, 120)),
      interaction,
      lcp: window.__audit.lcp,
      cls: window.__audit.cls,
      visibility: document.visibilityState,
      paintEntries: performance.getEntriesByType('paint').map((entry) => ({ name: entry.name, startTime: entry.startTime })),
      selectedJob: document.querySelector('#job-title')?.textContent.trim() || '',
    };
  })()`);

  await cdp.call("Emulation.setDeviceMetricsOverride", {
    width: 320, height: 800, deviceScaleFactor: 1, mobile: true,
  });
  await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  const compact = await cdp.evaluate(`(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
    hasHorizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }))()`);
  const report = {
    fixture,
    metrics: {
      jobs: jobCount,
      renderedRows: desktop.renderedRows,
      rowHeight: [...new Set(desktop.rowHeights)],
      lcpMs: desktop.lcp ? Math.round(desktop.lcp) : null,
      renderReadyMs,
      interactionMs: Number(desktop.interaction.toFixed(2)),
      cls: Number(desktop.cls.toFixed(4)),
    },
    accessibility: {
      duplicateIds: desktop.duplicateIds,
      unnamedControls: desktop.unnamedControls,
      horizontalOverflowAt320: compact.hasHorizontalOverflow,
    },
  };
  console.log(JSON.stringify(report, null, 2));

  const failures = [];
  if (jobCount !== fixture.count) failures.push(`expected ${fixture.count} jobs, got ${jobCount}`);
  if (desktop.renderedRows >= 50) failures.push(`virtual list rendered ${desktop.renderedRows} rows`);
  if (desktop.rowHeights.some((height) => height !== 72)) failures.push(`row heights were ${desktop.rowHeights.join(", ")}`);
  const observedLcp = desktop.lcp || renderReadyMs;
  if (observedLcp >= 2000) failures.push(`render-ready/LCP was ${observedLcp.toFixed(1)}ms`);
  if (desktop.interaction >= 200) failures.push(`selection interaction was ${desktop.interaction.toFixed(1)}ms`);
  if (desktop.cls >= 0.05) failures.push(`CLS was ${desktop.cls.toFixed(4)}`);
  if (desktop.duplicateIds.length) failures.push("duplicate IDs found");
  if (desktop.unnamedControls.length) failures.push("unnamed interactive controls found");
  if (compact.hasHorizontalOverflow) failures.push(`320px layout overflowed to ${compact.content}px`);
  if (failures.length) throw new Error(failures.join("; "));
} finally {
  cdp?.close();
  const dashboardExited = dashboard.exitCode === null
    ? new Promise((resolveExit) => dashboard.once("exit", resolveExit)) : Promise.resolve();
  const browserExited = browser.exitCode === null
    ? new Promise((resolveExit) => browser.once("exit", resolveExit)) : Promise.resolve();
  dashboard.kill("SIGTERM");
  browser.kill("SIGTERM");
  await Promise.allSettled([dashboardExited, browserExited]);
  await rm(browserData, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
