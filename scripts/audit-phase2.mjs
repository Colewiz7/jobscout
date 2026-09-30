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
const auditQuickFill = process.env.JOBSCOUT_AUDIT_QUICK_FILL === "true";
const auditApplySession = process.env.JOBSCOUT_AUDIT_APPLY_SESSION === "true";
const auditSessionNoProfile = process.env.JOBSCOUT_AUDIT_SESSION_NO_PROFILE === "true";
const auditTracker = process.env.JOBSCOUT_AUDIT_TRACKER === "true";
const auditEligibility = process.env.JOBSCOUT_AUDIT_ELIGIBILITY === "true";
const auditOverview = process.env.JOBSCOUT_AUDIT_OVERVIEW === "true";

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
      JOBSCOUT_QUICK_FILL_ENABLED: auditQuickFill || (auditApplySession && !auditSessionNoProfile) || auditEligibility ? "true" : "false",
      JOBSCOUT_AI_OVERVIEW_ENABLED: auditOverview ? "true" : "false",
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
      ${auditOverview ? `
        const originalFetch = window.fetch.bind(window);
        window.fetch = (input, options) => {
          const url = String(typeof input === "string" ? input : input.url);
          if (url.includes("/overview?cached=1")) return Promise.resolve(new Response(JSON.stringify({ items: [
            { kind: "work", text: "Build tools with Claude and Codex.", terms: ["Build tools"] },
            { kind: "skills", text: "Python, leadership", terms: ["Python", "leadership"] },
            { kind: "location", text: "Hybrid in Boston", terms: ["Hybrid", "Boston"] },
          ] }), { headers: { "Content-Type": "application/json" } }));
          if (url.endsWith("/description")) return Promise.resolve(new Response(JSON.stringify({ description: {
            description_text: "Build tools with Claude and Codex using Python. Hybrid in Boston.",
            sections: [{ key: "responsibilities", title: "Responsibilities", text: "Build tools with Claude and Codex using Python. Hybrid in Boston." }],
          } }), { headers: { "Content-Type": "application/json" } }));
          return originalFetch(input, options);
        };
      ` : ""}
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
  let overview = null;
  if (auditOverview) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (await cdp.evaluate("Boolean(document.querySelector('.overview-grid'))")) break;
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    overview = await cdp.evaluate(`(() => {
      const grid = document.querySelector('.overview-grid');
      return {
        labels: [...(grid?.querySelectorAll('dt') || [])].map((item) => item.textContent),
        values: [...(grid?.querySelectorAll('dd') || [])].map((item) => item.textContent),
        columns: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
        highlighted: [...document.querySelectorAll('.posting-sections mark')].map((item) => item.textContent),
        highlightCategories: [...document.querySelectorAll('.posting-sections mark')].map((item) => item.className),
        highlightKey: [...document.querySelectorAll('.highlight-key-item')].map((item) => item.textContent),
        brandSize: document.querySelector('.brand-mark img')?.getBoundingClientRect().width || 0,
        dividers: ['.job-detail-body', '.job-detail-body > .posting-sections',
          '.job-detail-body > [aria-labelledby="posting-details-heading"]',
          '.job-detail-body > [aria-labelledby="activity-heading"]'].map((selector) => {
          const element = document.querySelector(selector);
          return element ? getComputedStyle(element).borderTopWidth : 'missing';
        }),
        customField: Boolean(document.querySelector('#highlight-terms')),
      };
    })()`);
  }

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
    const titleIndent = getComputedStyle(rows[0].querySelector('.job-row-title')).paddingInlineStart;
    const rowTops = rows.map((row) => Math.round(row.getBoundingClientRect().top));
    const selectedBefore = document.querySelector('#job-title')?.textContent.trim() || '';
    const start = performance.now();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true }));
    const interaction = performance.now() - start;
    return {
      renderedRows: rows.length,
      rowHeights,
      titleIndent,
      rowTops,
      duplicateIds: ids.filter((id, index) => ids.indexOf(id) !== index),
      unnamedControls: unnamed.map((element) => element.outerHTML.slice(0, 120)),
      interaction,
      selectedBefore,
      lcp: window.__audit.lcp,
      cls: window.__audit.cls,
      visibility: document.visibilityState,
      paintEntries: performance.getEntriesByType('paint').map((entry) => ({ name: entry.name, startTime: entry.startTime })),
      selectedJob: document.querySelector('#job-title')?.textContent.trim() || '',
    };
  })()`);
  await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  const selectedAfter = await cdp.evaluate("document.querySelector('#job-title')?.textContent.trim() || ''");
  const copiedList = await cdp.evaluate(`(async () => {
    let copied = '';
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value) => { copied = value; } } });
    document.querySelector('[data-copy-list]').click();
    await new Promise((resolve) => setTimeout(resolve, 10));
    return { lines: copied.split('\\n').length, containsPostingUrl: copied.includes('https://') };
  })()`);
  const logoScroll = await cdp.evaluate(`(async () => {
    const viewport = document.querySelector('#job-viewport');
    const row = [...viewport.querySelectorAll('.job-row')][6];
    const key = row?.dataset.jobKey;
    const logo = row?.querySelector('.job-logo');
    const headerLogo = document.querySelector('.detail-company-logo');
    const headerTitle = document.querySelector('.detail-heading h1');
    const before = viewport.scrollTop;
    viewport.scrollTop += 72;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const retained = [...viewport.querySelectorAll('.job-row')].find((item) => item.dataset.jobKey === key);
    return {
      sameRow: retained === row,
      sameLogo: retained?.querySelector('.job-logo') === logo,
      scrolled: viewport.scrollTop === before + 72,
      headerLogoSize: headerLogo?.getBoundingClientRect().height,
      headerTitleSize: headerTitle ? Number.parseFloat(getComputedStyle(headerTitle).fontSize) : 0,
    };
  })()`);
  const scrolledSelection = await cdp.evaluate(`(async () => {
    const viewport = document.querySelector('#job-viewport');
    viewport.scrollTop = 72 * 120;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const before = viewport.scrollTop;
    const row = [...viewport.querySelectorAll('.job-row')].find((item) => {
      const rect = item.getBoundingClientRect();
      const host = viewport.getBoundingClientRect();
      return rect.top >= host.top + 16 && rect.bottom <= host.bottom - 16;
    });
    if (!row) return { error: 'no visible row after scroll' };
    const expected = row.querySelector('.job-row-title')?.textContent.trim();
    const key = row.dataset.jobKey;
    row.click();
    const immediate = document.querySelector('#job-viewport')?.scrollTop;
    await new Promise((resolve) => setTimeout(resolve, 250));
    return {
      before,
      immediate,
      after: document.querySelector('#job-viewport')?.scrollTop,
      expected,
      actual: document.querySelector('#job-title')?.textContent.trim(),
      route: decodeURIComponent(location.pathname.split('/').slice(2).join('/')),
      key,
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
  let quickFill = null;
  if (auditQuickFill) {
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });
    quickFill = await cdp.evaluate(`(async () => {
      const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
      document.querySelector('#quick-fill-trigger')?.click();
      for (let attempt = 0; attempt < 40 && !document.querySelector('.copy-row'); attempt += 1) await wait(50);
      document.querySelector('[data-quick-fill-edit]')?.click();
      await wait(50);
      const set = (control, value, eventName = 'input') => {
        control.value = value;
        control.dispatchEvent(new Event(eventName, { bubbles: true }));
      };
      const override = document.querySelector('[data-context-kind="override"]');
      const exists = document.querySelector('select[data-context-property="account_exists"]');
      const email = document.querySelector('[data-context-property="sign_in_email"]');
      const manager = document.querySelector('[data-context-property="password_manager_url"]');
      if (!override || !exists || !email || !manager) return { error: 'context controls unavailable' };
      set(override, 'A job-specific answer for {company}.');
      set(exists, 'yes', 'change');
      set(email, 'audit@example.invalid');
      set(manager, 'https://vault.example.invalid/jobseer');
      await wait(900);
      const jobs = await fetch('/api/v1/jobs').then((response) => response.json()).then((data) => data.jobs);
      const selectedKey = () => decodeURIComponent(location.pathname.split('/').slice(2).join('/'));
      const firstJob = jobs.find((job) => job.dedupe_key === selectedKey());
      const firstParams = new URLSearchParams({ job: firstJob.dedupe_key, company: firstJob.company });
      const stored = await fetch('/api/v1/profile?' + firstParams).then((response) => response.json()).then((data) => data.profile);
      document.querySelector('[data-quick-fill-edit]')?.click();
      await wait(50);
      const passwordManagerLink = document.querySelector('.quick-fill-password-link')?.href || '';
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => {} } });
      [...document.querySelectorAll('.copy-row')].find((row) => row.querySelector('strong')?.textContent === 'Email')?.click();
      await wait(300);
      const copied = await fetch('/api/v1/profile?' + firstParams).then((response) => response.json()).then((data) => data.profile.copied_fields);
      const nextJob = jobs.find((job) => job.dedupe_key !== firstJob.dedupe_key && job.company !== firstJob.company);
      history.pushState({}, '', '/inbox/' + encodeURIComponent(nextJob.dedupe_key) + '?status=all');
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
      await wait(300);
      const secondJob = jobs.find((job) => job.dedupe_key === selectedKey());
      const secondParams = new URLSearchParams({ job: secondJob.dedupe_key, company: secondJob.company });
      const isolated = await fetch('/api/v1/profile?' + secondParams).then((response) => response.json()).then((data) => data.profile);
      const controls = [...document.querySelectorAll('#quick-fill-sheet button, #quick-fill-sheet a[href], #quick-fill-sheet input, #quick-fill-sheet textarea, #quick-fill-sheet select')]
        .filter((element) => element.getClientRects().length > 0);
      const unnamed = controls.filter((element) => !(
        element.getAttribute('aria-label') || element.textContent.trim() || element.closest('label') ||
        (element.id && document.querySelector('label[for="' + CSS.escape(element.id) + '"]'))
      ));
      return {
        override: stored.answer_overrides['Why this role'],
        accountEmail: stored.company_account?.sign_in_email || '',
        passwordManagerLink,
        copiedEmail: copied.includes('field:email'),
        contextIsolated: Object.keys(isolated.answer_overrides).length === 0 && isolated.company_account === null,
        firstContext: firstJob ? firstJob.dedupe_key + ' / ' + firstJob.company : '',
        secondContext: secondJob ? secondJob.dedupe_key + ' / ' + secondJob.company : '',
        unnamedControls: unnamed.length,
      };
    })()`);
  }
  let applySession = null;
  if (auditApplySession) {
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });
    await cdp.call("Page.navigate", { url: `${base}/queue` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate("Boolean(document.querySelector('[data-start-session]') && document.querySelectorAll('.queue-card').length > 1)")) break;
      if (attempt === 99) throw new Error("Queue did not finish rendering");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    const queue = await cdp.evaluate(`(() => {
      const cards = [...document.querySelectorAll('.queue-card')];
      const before = cards.slice(0, 3).map((card) => card.dataset.queueKey);
      cards[0].querySelector('[data-queue-move="down"]')?.click();
      return { count: cards.length, before };
    })()`);
    await new Promise((resolveWait) => setTimeout(resolveWait, 300));
    queue.after = await cdp.evaluate("[...document.querySelectorAll('.queue-card')].slice(0, 3).map((card) => card.dataset.queueKey)");
    await cdp.evaluate("document.querySelector('[data-start-session]').click()");
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate("location.pathname.startsWith('/queue/session/') && Boolean(document.querySelector('[data-session-apply]'))")) break;
      if (attempt === 99) throw new Error("Apply session did not start");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    const session = await cdp.evaluate(`(() => ({
      navHidden: getComputedStyle(document.querySelector('.nav-rail')).display === 'none',
      quickFillEmbedded: Boolean(document.querySelector('#session-quick-fill .copy-row')),
      postingReview: Boolean(document.querySelector('.session-review h2')),
      reviewWidth: Math.round(document.querySelector('.session-review')?.getBoundingClientRect().width || 0),
      previewItems: document.querySelectorAll('.session-requirements li').length,
      expandedPostingSections: document.querySelectorAll('.session-review .parsed-section[open]').length,
      filledActions: document.querySelectorAll('.apply-session .filled-button').length,
      routeHeight: Math.round(document.querySelector('#route-view').getBoundingClientRect().height),
      jobVisible: (() => { const r = document.querySelector('.session-job')?.getBoundingClientRect(); return Boolean(r && r.top < innerHeight && r.bottom > 64); })(),
      horizontalOverflow: document.querySelector('#route-view').scrollWidth > document.querySelector('#route-view').clientWidth,
    }))()`);
    session.readFullOpens = await cdp.evaluate(`(() => {
      const link = document.querySelector('[data-session-see-requirements]');
      const full = document.querySelector('.apply-session .parsed-section[data-section-key="requirements"]');
      if (!link || !full) return false;
      link.click();
      return full.open;
    })()`);
    await cdp.evaluate(`(() => {
      window.open = () => ({});
      document.querySelector('[data-session-apply]').click();
    })()`);
    await new Promise((resolveWait) => setTimeout(resolveWait, 400));
    await cdp.evaluate("window.dispatchEvent(new Event('focus'))");
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    const prompt = await cdp.evaluate(`(() => ({
      visible: Boolean(document.querySelector('[data-session-applied]')),
      resumeOptions: document.querySelectorAll('#session-resume option').length,
      filledActions: document.querySelectorAll('.apply-session .filled-button').length,
    }))()`);
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 320, height: 800, deviceScaleFactor: 1, mobile: true,
    });
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    const compactSessionOverflow = await cdp.evaluate(
      "document.documentElement.scrollWidth > document.documentElement.clientWidth",
    );
    const appliedPath = await cdp.evaluate(`(() => {
      const select = document.querySelector('#session-resume');
      if (select && select.options.length > 1) select.selectedIndex = 1;
      document.querySelector('[data-session-applied]')?.click();
      return location.pathname;
    })()`);
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate(`location.pathname !== ${JSON.stringify(appliedPath)} && Boolean(document.querySelector('[data-session-end]'))`)) break;
      if (attempt === 99) throw new Error("Session did not advance after marking applied");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    await cdp.evaluate("document.querySelector('[data-session-end]').click()");
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    const summary = await cdp.evaluate("document.querySelector('.session-summary')?.textContent || ''");
    applySession = { queue, session, prompt, compactSessionOverflow, summary };
  }
  let tracker = null;
  if (auditTracker) {
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });
    await cdp.call("Page.navigate", { url: `${base}/tracker` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate("Boolean(document.querySelector('.tracker-page .tracker-table'))")) break;
      if (attempt === 99) throw new Error("Tracker did not finish rendering");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    const table = await cdp.evaluate(`(() => ({
      applications: document.querySelectorAll('.tracker-table tbody tr').length,
      insights: document.querySelectorAll('.tracker-insights > div').length,
      todayVisible: !document.querySelector('#today-strip').hidden,
      columns: [...document.querySelectorAll('.tracker-table th')].map((item) => item.textContent.trim()),
    }))()`);
    await cdp.evaluate(`(() => {
      const input = document.querySelector('[data-next-step]');
      input.value = 'Send a concise follow-up';
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    })()`);
    await new Promise((resolveWait) => setTimeout(resolveWait, 300));
    await cdp.evaluate("document.querySelector('[data-tracker-view=\"board\"]').click()");
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    const board = await cdp.evaluate(`(() => ({
      columns: document.querySelectorAll('.board-column').length,
      cards: document.querySelectorAll('.board-card').length,
      alternatives: document.querySelectorAll('[data-board-status-select]').length,
    }))()`);
    await cdp.evaluate("document.querySelector('[data-tracker-view=\"calibration\"]').click()");
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    const calibration = await cdp.evaluate(`(() => ({
      present: Boolean(document.querySelector('.calibration')),
      guardrail: document.querySelector('.calibration-intro')?.textContent.includes('never changes scout scoring') || false,
      bands: document.querySelectorAll('.calibration-grid article').length,
    }))()`);
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 320, height: 800, deviceScaleFactor: 1, mobile: true,
    });
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    const trackerOverflow = await cdp.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth");

    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });
    await cdp.call("Page.navigate", { url: `${base}/companies` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate("document.querySelectorAll('.company-card').length > 1")) break;
      if (attempt === 99) throw new Error("Companies did not finish rendering");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    const companyCount = await cdp.evaluate("document.querySelectorAll('.company-card').length");
    await cdp.evaluate("document.querySelector('.company-card').click()");
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate("Boolean(document.querySelector('.company-detail'))")) break;
      if (attempt === 99) throw new Error("Company detail did not finish rendering");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    const company = await cdp.evaluate(`(async () => {
      const note = document.querySelector('#company-note');
      note.value = 'Audit note'; note.dispatchEvent(new InputEvent('input', { bubbles: true }));
      document.querySelector('.contact-add').open = true;
      const form = document.querySelector('#contact-form');
      form.elements.name.value = 'Audit Contact'; form.elements.title.value = 'Engineer';
      form.requestSubmit();
      await new Promise((resolveWait) => setTimeout(resolveWait, 900));
      const controls = [...document.querySelectorAll('.company-detail button, .company-detail a[href], .company-detail input, .company-detail textarea, .company-detail select')]
        .filter((element) => element.getClientRects().length > 0);
      return {
        protectedAccount: Boolean(document.querySelector('.protected-copy')),
        contacts: document.querySelectorAll('.contact-row').length,
        noteState: document.querySelector('#company-note-state')?.textContent || '',
        unnamedControls: controls.filter((element) => !(element.getAttribute('aria-label') || element.textContent.trim() || element.closest('label'))).length,
      };
    })()`);
    tracker = { table, board, calibration, trackerOverflow, companyCount, company };
  }
  let phase6 = null;
  if (auditEligibility) {
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });
    await cdp.call("Page.navigate", { url: `${base}/inbox?status=all` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate("Boolean(document.querySelector('.skill-match'))")) break;
      if (attempt === 99) throw new Error("Eligibility evidence did not finish rendering");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    const reading = await cdp.evaluate(`(() => ({
      atGlance: document.querySelectorAll('.at-glance li').length,
      matched: document.querySelectorAll('.skill-chips > span').length,
      provenance: [...document.querySelectorAll('.requirement-evidence small')].map((item) => item.textContent.trim()),
      band: document.querySelector('.skill-match .section-heading p')?.textContent.trim() || '',
    }))()`);
    await cdp.call("Page.navigate", { url: `${base}/profile` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await cdp.evaluate("Boolean(document.querySelector('#rule-form'))")) break;
      if (attempt === 99) throw new Error("Profile rules did not finish rendering");
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
    const rules = await cdp.evaluate(`(async () => {
      const form = document.querySelector('#rule-form');
      form.elements.name.value = 'Systems titles';
      form.elements.kind.value = 'tag_title';
      form.elements.pattern.value = 'Engineer';
      form.elements.value.value = 'Systems';
      form.requestSubmit();
      await new Promise((resolveWait) => setTimeout(resolveWait, 700));
      const jobs = await fetch('/api/v1/jobs').then((response) => response.json()).then((data) => data.jobs);
      const controls = [...document.querySelectorAll('.profile-page button, .profile-page input, .profile-page select')]
        .filter((element) => element.getClientRects().length > 0);
      return {
        count: document.querySelectorAll('.rule-card').length,
        tagged: jobs.filter((job) => job.tags?.includes('Systems')).length,
        unnamedControls: controls.filter((element) => !(element.getAttribute('aria-label') || element.textContent.trim() || element.closest('label'))).length,
      };
    })()`);
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 320, height: 800, deviceScaleFactor: 1, mobile: true,
    });
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    const overflow = await cdp.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth");
    phase6 = { reading, rules, overflow };
  }
  const report = {
    fixture,
    metrics: {
      jobs: jobCount,
      renderedRows: desktop.renderedRows,
      rowHeight: [...new Set(desktop.rowHeights)],
      distinctRowPositions: new Set(desktop.rowTops).size,
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
    scrolledSelection,
    logoScroll,
    copiedList,
    ...(overview ? { overview } : {}),
    ...(quickFill ? { quickFill } : {}),
    ...(applySession ? { applySession } : {}),
    ...(tracker ? { tracker } : {}),
    ...(phase6 ? { phase6 } : {}),
  };
  console.log(JSON.stringify(report, null, 2));

  const failures = [];
  if (jobCount !== fixture.count) failures.push(`expected ${fixture.count} jobs, got ${jobCount}`);
  if (desktop.renderedRows >= 50) failures.push(`virtual list rendered ${desktop.renderedRows} rows`);
  if (desktop.rowHeights.some((height) => height !== 72)) failures.push(`row heights were ${desktop.rowHeights.join(", ")}`);
  if (desktop.titleIndent !== "0px") failures.push(`job title has unwanted indent: ${desktop.titleIndent}`);
  if (new Set(desktop.rowTops).size !== desktop.rowTops.length) failures.push("virtual rows overlap at the same position");
  if (desktop.selectedBefore === selectedAfter) failures.push("next-job shortcut did not update the reading pane");
  if (scrolledSelection.error || scrolledSelection.after !== scrolledSelection.before || scrolledSelection.actual !== scrolledSelection.expected || scrolledSelection.route !== scrolledSelection.key) failures.push(`scrolled row selection failed: ${JSON.stringify(scrolledSelection)}`);
  if (!logoScroll.sameRow || !logoScroll.sameLogo || !logoScroll.scrolled) failures.push(`scroll remounted a visible company logo: ${JSON.stringify(logoScroll)}`);
  if (logoScroll.headerLogoSize !== 80 || logoScroll.headerTitleSize !== 28) failures.push(`job header logo or title size regressed: ${JSON.stringify(logoScroll)}`);
  if (copiedList.lines !== jobCount || !copiedList.containsPostingUrl) failures.push(`copy list did not include the whole view: ${JSON.stringify(copiedList)}`);
  const observedLcp = desktop.lcp || renderReadyMs;
  if (observedLcp >= 2000) failures.push(`render-ready/LCP was ${observedLcp.toFixed(1)}ms`);
  if (desktop.interaction >= 200) failures.push(`selection interaction was ${desktop.interaction.toFixed(1)}ms`);
  if (desktop.cls >= 0.05) failures.push(`CLS was ${desktop.cls.toFixed(4)}`);
  if (desktop.duplicateIds.length) failures.push("duplicate IDs found");
  if (desktop.unnamedControls.length) failures.push("unnamed interactive controls found");
  if (compact.hasHorizontalOverflow) failures.push(`320px layout overflowed to ${compact.content}px`);
  if (auditOverview && (overview.columns !== 2 || !overview.labels.includes("Skills") || !overview.values.includes("Python, leadership"))) failures.push(`overview fact grid failed: ${JSON.stringify(overview)}`);
  if (auditOverview && (!overview.highlighted.includes("Claude") || !overview.highlighted.includes("Codex") || overview.customField)) failures.push(`automatic highlighting failed: ${JSON.stringify(overview)}`);
  if (auditOverview && (!overview.highlightCategories.some((kind) => kind.includes("posting-highlight--stack")) || !overview.highlightKey.includes("Stack match") || overview.brandSize !== 48)) failures.push(`highlight colors or brand size failed: ${JSON.stringify(overview)}`);
  if (auditOverview && overview.dividers.some((width) => width !== '1px')) failures.push(`reading pane dividers missing: ${JSON.stringify(overview.dividers)}`);
  if (quickFill?.error) failures.push(quickFill.error);
  if (auditQuickFill && quickFill?.override !== "A job-specific answer for {company}.") failures.push("job-specific answer did not autosave");
  if (auditQuickFill && quickFill?.accountEmail !== "audit@example.invalid") failures.push("ATS account did not autosave");
  if (auditQuickFill && quickFill?.passwordManagerLink !== "https://vault.example.invalid/jobseer") failures.push("password-manager link missing");
  if (auditQuickFill && !quickFill?.copiedEmail) failures.push("per-job copy marker did not persist");
  if (auditQuickFill && !quickFill?.contextIsolated) failures.push("Quick-fill context leaked between jobs");
  if (auditQuickFill && quickFill?.unnamedControls) failures.push("unnamed Quick-fill controls found");
  if (auditApplySession && applySession?.queue.before[0] === applySession?.queue.after[0]) failures.push("queue reorder did not persist in the UI");
  if (auditApplySession && !applySession?.session.navHidden) failures.push("application session did not hide app chrome");
  if (auditApplySession && (applySession?.session.routeHeight < 600 || !applySession?.session.jobVisible || applySession?.session.horizontalOverflow)) failures.push(`application session content is clipped: ${JSON.stringify(applySession?.session)}`);
  if (auditApplySession && !auditSessionNoProfile && !applySession?.session.quickFillEmbedded) failures.push("Quick-fill was not embedded in the session");
  if (auditApplySession && auditSessionNoProfile && !applySession?.session.postingReview) failures.push("production session had no posting review");
  if (auditApplySession && auditSessionNoProfile && (applySession?.session.reviewWidth < 480 || applySession?.session.previewItems > 4 || applySession?.session.expandedPostingSections)) failures.push(`production session is not compact: ${JSON.stringify(applySession?.session)}`);
  if (auditApplySession && auditSessionNoProfile && !applySession?.session.readFullOpens) failures.push("requirements preview did not open the full section");
  if (auditApplySession && applySession?.session.filledActions !== 1) failures.push("application session has more than one filled action");
  if (auditApplySession && (!applySession?.prompt.visible || applySession?.prompt.resumeOptions < (auditSessionNoProfile ? 1 : 2))) failures.push("submission prompt or resume selector missing");
  if (auditApplySession && applySession?.prompt.filledActions !== 1) failures.push("submission prompt has more than one filled action");
  if (auditApplySession && applySession?.compactSessionOverflow) failures.push("compact application session overflowed horizontally");
  if (auditApplySession && !applySession?.summary.includes("applied to 1 role")) failures.push("session summary did not record the application");
  if (auditTracker && tracker?.table.applications < 1) failures.push("tracker table has no applications");
  if (auditTracker && tracker?.table.insights !== 4) failures.push("tracker insights are incomplete");
  if (auditTracker && tracker?.table.columns.length !== 8) failures.push("tracker table columns are incomplete");
  if (auditTracker && tracker?.board.columns !== 5) failures.push("tracker board columns are incomplete");
  if (auditTracker && tracker?.board.cards !== tracker?.board.alternatives) failures.push("board drag cards lack keyboard alternatives");
  if (auditTracker && (!tracker?.calibration.present || !tracker?.calibration.guardrail || tracker?.calibration.bands !== 3)) failures.push("score calibration view is incomplete");
  if (auditTracker && tracker?.trackerOverflow) failures.push("compact tracker overflowed horizontally");
  if (auditTracker && tracker?.companyCount !== 50) failures.push(`expected 50 companies, got ${tracker?.companyCount}`);
  if (auditTracker && !tracker?.company.protectedAccount) failures.push("protected ATS account state is missing");
  if (auditTracker && tracker?.company.contacts < 1) failures.push("company contact did not save");
  if (auditTracker && tracker?.company.noteState !== "Saved automatically") failures.push("company note did not autosave");
  if (auditTracker && tracker?.company.unnamedControls) failures.push("unnamed company controls found");
  if (auditEligibility && phase6?.reading.atGlance < 3) failures.push("at-a-glance extraction is incomplete");
  if (auditEligibility && phase6?.reading.matched < 1) failures.push("skill evidence is missing");
  if (auditEligibility && !phase6?.reading.provenance.includes("posting structure")) failures.push("requirement provenance is missing");
  if (auditEligibility && phase6?.rules.count !== 1) failures.push("rule did not save");
  if (auditEligibility && phase6?.rules.tagged < 1) failures.push("tag rule did not apply");
  if (auditEligibility && phase6?.rules.unnamedControls) failures.push("unnamed Profile controls found");
  if (auditEligibility && phase6?.overflow) failures.push("compact Profile overflowed horizontally");
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
