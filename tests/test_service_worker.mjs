import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE = fs.readFileSync(new URL("../src/jobscout/static/service-worker.js", import.meta.url), "utf8");

const APP_HTML = '<html><main id="route-view"></main></html>';
const LOGIN_HTML = "<html><form action=/outpost.goauthentik.io/flow>sign in</form></html>";

function typeFor(path) {
  if (path.endsWith(".html")) return "text/html; charset=utf-8";
  if (path.endsWith(".css")) return "text/css";
  if (path.endsWith(".js")) return "text/javascript";
  if (path.endsWith(".json")) return "application/json";
  if (path.endsWith(".woff2")) return "font/woff2";
  return "image/png";
}

function realResponse(path) {
  return fakeResponse({ contentType: typeFor(path), body: path.endsWith(".html") ? APP_HTML : "x" });
}

function fakeResponse({ ok = true, type = "basic", redirected = false, contentType = "text/css", url = "https://jobs.test/static/x", body = "x" } = {}) {
  return {
    text: async () => body,
    ok,
    type,
    redirected,
    url,
    headers: { get: (name) => (name.toLowerCase() === "content-type" ? contentType : null) },
    clone() { return this; },
  };
}

function load(respond, { failPutAt = 0 } = {}) {
  const listeners = {};
  const stores = new Map();
  const fetched = [];
  const caches = {
    open: async (name) => {
      if (!stores.has(name)) stores.set(name, new Map());
      const store = stores.get(name);
      const put = async (key, value) => {
        if (failPutAt && store.size + 1 === failPutAt) throw new Error("put failed");
        store.set(typeof key === "string" ? key : key.url, value);
      };
      const addAll = async (paths) => {
        const all = await Promise.all(paths.map((path) => fetch(path)));
        if (all.some((r) => !r.ok)) throw new Error("addAll failed");
        paths.forEach((path, i) => put(path, all[i]));
      };
      return { put, addAll, delete: async (key) => store.delete(key) };
    },
    keys: async () => [...stores.keys()],
    delete: async (name) => stores.delete(name),
    match: async () => undefined,
  };
  const self = {
    location: { origin: "https://jobs.test" },
    addEventListener: (type, fn) => { listeners[type] = fn; },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  };
  const fetch = async (request) => {
    const path = typeof request === "string" ? request : new URL(request.url).pathname;
    fetched.push(path);
    return respond(path);
  };
  vm.runInNewContext(SOURCE, { self, caches, fetch, URL, Promise, Error });
  const install = () => {
    let pending;
    listeners.install({ waitUntil: (p) => { pending = p; } });
    return pending;
  };
  return { install, listeners, stores, fetched };
}

test("all assets ok are cached", async () => {
  const sw = load(realResponse);
  await sw.install();
  const [store] = sw.stores.values();
  assert.equal(store.size, sw.fetched.length);
  assert.ok(store.has("/static/app.css"));
  assert.ok(store.has("/static/company-logos/manifest.json"));
});

test("redirected probe makes one fetch and caches nothing", async () => {
  const sw = load(() => fakeResponse({ ok: false, type: "opaqueredirect" }));
  await assert.rejects(sw.install());
  assert.equal(sw.fetched.length, 1);
  assert.equal([...sw.stores.values()].reduce((n, s) => n + s.size, 0), 0);
});

test("a later redirected asset rejects without partial cache", async () => {
  const sw = load((path) => (path === "/static/fonts/rubik-latin.woff2" ? fakeResponse({ ok: false, type: "opaqueredirect" }) : realResponse(path)));
  await assert.rejects(sw.install());
  assert.equal([...sw.stores.values()].reduce((n, s) => n + s.size, 0), 0);
});

test("a followed redirect on a later asset rejects", async () => {
  const sw = load((path) => (path === "/static/motion.js" ? fakeResponse({ redirected: true }) : realResponse(path)));
  await assert.rejects(sw.install());
  assert.equal([...sw.stores.values()].reduce((n, s) => n + s.size, 0), 0);
});

test("an html login page for a css asset rejects", async () => {
  const sw = load((path) => (path === "/static/tokens.css" ? fakeResponse({ contentType: "text/html" }) : realResponse(path)));
  await assert.rejects(sw.install());
  assert.equal([...sw.stores.values()].reduce((n, s) => n + s.size, 0), 0);
});

async function runFetch(response, path = "/static/app.css", mode = "no-cors") {
  const sw = load(() => response);
  let reply;
  const request = { method: "GET", url: "https://jobs.test" + path, mode };
  sw.listeners.fetch({ request, respondWith: (p) => { reply = p; } });
  await reply;
  return [...sw.stores.values()].reduce((n, s) => n + s.size, 0);
}

test("fetch handler caches a real asset", async () => {
  assert.equal(await runFetch(fakeResponse({ url: "https://jobs.test/static/app.css" })), 1);
});

test("fetch handler skips a redirected response", async () => {
  assert.equal(await runFetch(fakeResponse({ redirected: true, url: "https://jobs.test/static/app.css" })), 0);
});

test("fetch handler skips an html page served as css", async () => {
  assert.equal(await runFetch(fakeResponse({ contentType: "text/html", url: "https://jobs.test/static/app.css" })), 0);
});

test("login html at index.html rejects install", async () => {
  const sw = load((path) => (path === "/static/index.html" ? fakeResponse({ contentType: "text/html", body: LOGIN_HTML }) : realResponse(path)));
  await assert.rejects(sw.install());
  assert.equal([...sw.stores.values()].reduce((n, s) => n + s.size, 0), 0);
});

test("legit index.html installs", async () => {
  const sw = load(realResponse);
  await sw.install();
  assert.ok([...sw.stores.values()][0].has("/static/index.html"));
});

test("fetch handler skips login html on navigation", async () => {
  const login = fakeResponse({ contentType: "text/html", body: LOGIN_HTML, url: "https://jobs.test/" });
  assert.equal(await runFetch(login, "/", "navigate"), 0);
});

test("fetch handler caches real html on navigation", async () => {
  const page = fakeResponse({ contentType: "text/html", body: APP_HTML, url: "https://jobs.test/" });
  assert.equal(await runFetch(page, "/", "navigate"), 1);
});

test("a failed cache put leaves no partial entries", async () => {
  const sw = load(realResponse, { failPutAt: 5 });
  await assert.rejects(sw.install());
  assert.equal([...sw.stores.values()].reduce((n, s) => n + s.size, 0), 0);
});

function shellPaths() {
  const block = SOURCE.match(/const SHELL = \[([\s\S]*?)\];/)[1];
  return [...block.matchAll(/"(\/static\/[^"]+)"/g)].map((match) => match[1]);
}

test("every shell entry is a real file", () => {
  const paths = shellPaths();
  assert.ok(paths.length > 20);
  for (const path of paths) assert.ok(fs.existsSync(new URL(`../src/jobscout${path}`, import.meta.url)), path);
});

test("every module the app imports is precached", () => {
  const app = fs.readFileSync(new URL("../src/jobscout/static/app.js", import.meta.url), "utf8");
  const modules = [...app.matchAll(/from "\.\/([\w-]+\.js)"/g)].map((match) => match[1]);
  assert.ok(modules.includes("search.js"));
  assert.ok(modules.includes("scan-run.js"));
  const shell = shellPaths();
  for (const name of modules) assert.ok(shell.includes(`/static/${name}`), name);
});
