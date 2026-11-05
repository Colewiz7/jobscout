"use strict";

const CACHE = "jobseer-reads-v28";
const SHELL = [
  "/static/index.html",
  "/static/app.css",
  "/static/tokens.css",
  "/static/theme.js",
  "/static/app.js",
  "/static/highlight-terms.js",
  "/static/copy-text.js",
  "/static/selection.js",
  "/static/shortcuts.js",
  "/static/menu-logic.js",
  "/static/action-menu.js",
  "/static/undo-stack.js",
  "/static/motion.js",
  "/static/drag-logic.js",
  "/static/drag.js",
  "/static/profile-edit.js",
  "/static/save-queue.js",
  "/static/record-edit.js",
  "/static/company-edit.js",
  "/static/inbox-edit.js",
  "/static/apply-session.js",
  "/static/command-palette.js",
  "/static/counts.js",
  "/static/fonts/rubik-latin.woff2",
  "/static/fonts/newsreader-latin.woff2",
  "/static/icons/jobseer-fan-192.png",
  "/static/icons/jobseer-fan-transparent-112.png",
  "/static/company-logos/manifest.json",
];

const PROBE = "/static/app.css";

// login page can come back as 200
async function isRealAsset(response, html) {
  if (!response.ok || response.redirected || response.type !== "basic") return false;
  const type = response.headers.get("Content-Type") || "";
  if (!html) return !type.includes("text/html");
  if (!type.includes("text/html")) return false;
  const body = await response.clone().text();
  return body.includes('id="route-view"') && !/goauthentik|outpost\./i.test(body);
}

async function fetchAsset(path) {
  const response = await fetch(path, { redirect: "manual" });
  if (!(await isRealAsset(response, path.endsWith(".html")))) throw new Error("not an asset: " + path);
  return response;
}

async function precache() {
  // dead session costs one request
  const probe = await fetchAsset(PROBE);
  const rest = SHELL.filter((path) => path !== PROBE);
  const responses = await Promise.all(rest.map(fetchAsset));
  const cache = await caches.open(CACHE);
  const entries = [[PROBE, probe], ...rest.map((path, i) => [path, responses[i]])];
  try {
    for (const [path, response] of entries) await cache.put(path, response);
  } catch (error) {
    // no partial shell left behind
    await Promise.all(entries.map(([path]) => cache.delete(path)));
    throw error;
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(Promise.all([
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("jobseer-reads-") && key !== CACHE).map((key) => caches.delete(key)))),
    self.clients.claim(),
  ]));
});

function isCachedRead(url) {
  if (url.pathname.startsWith("/static/")) return true;
  if (["/api/v1/jobs", "/api/v1/saved-views", "/api/v1/tracker", "/api/v1/companies", "/api/v1/company-icons"].includes(url.pathname)) return true;
  if (url.pathname.startsWith("/api/v1/company-icons/")) return true;
  return /^\/api\/v1\/jobs\/[^/]+\/(description|overview)$/.test(url.pathname);
}

async function networkFirst(request, fallback) {
  try {
    const response = await fetch(request);
    const responseUrl = new URL(response.url);
    const path = new URL(request.url).pathname;
    const expectedType = request.mode === "navigate" || !path.startsWith("/api/v1/")
      ? null : path.startsWith("/api/v1/company-icons/") ? "image/" : "application/json";
    const html = request.mode === "navigate" || path.endsWith(".html");
    if (response.ok && !response.redirected && response.type === "basic"
        && responseUrl.origin === self.location.origin
        && (!(html || path.startsWith("/static/")) || await isRealAsset(response, html))
        && (!expectedType || response.headers.get("Content-Type")?.includes(expectedType))) {
      const cache = await caches.open(CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (fallback) {
      const shell = await caches.match(fallback);
      if (shell) return shell;
    }
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/static/index.html"));
  } else if (isCachedRead(url)) {
    event.respondWith(networkFirst(request));
  }
});
