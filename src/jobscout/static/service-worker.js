"use strict";

const CACHE = "jobseer-reads-v8";
const SHELL = [
  "/static/index.html",
  "/static/app.css",
  "/static/tokens.css",
  "/static/theme.js",
  "/static/app.js",
  "/static/highlight-terms.js",
  "/static/fonts/rubik-latin.woff2",
  "/static/fonts/newsreader-latin.woff2",
  "/static/icons/jobseer-fan-192.png",
  "/static/icons/jobseer-fan-transparent-112.png",
  "/static/company-logos/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(Promise.all([
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("jobseer-reads-") && key !== CACHE).map((key) => caches.delete(key)))),
    self.clients.claim(),
  ]));
});

function isCachedRead(url) {
  if (url.pathname.startsWith("/static/")) return true;
  if (["/api/v1/jobs", "/api/v1/saved-views", "/api/v1/tracker", "/api/v1/companies"].includes(url.pathname)) return true;
  return /^\/api\/v1\/jobs\/[^/]+\/(description|overview)$/.test(url.pathname);
}

async function networkFirst(request, fallback) {
  try {
    const response = await fetch(request);
    const responseUrl = new URL(response.url);
    const expectedType = request.mode === "navigate" || !new URL(request.url).pathname.startsWith("/api/v1/")
      ? null : "application/json";
    if (response.ok && !response.redirected && response.type === "basic"
        && responseUrl.origin === self.location.origin
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
