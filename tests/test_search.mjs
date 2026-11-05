import assert from "node:assert/strict";
import test from "node:test";

import { fuzzyScore } from "../src/jobscout/static/command-palette.js";
import { createSearchTimer, highlightRanges, isEmptyQuery, normalize, parseQuery, scoreFields, scoreRow } from "../src/jobscout/static/search.js";

const score = (query, title, company = "Acme") => scoreRow(parseQuery(query), { title, company });
const hits = (query, title, company = "Acme") => score(query, title, company) !== null;
const marked = (query, text, field) => highlightRanges(parseQuery(query), text, field).map(([start, end]) => text.slice(start, end));

test("normalize folds accents and case and strips punctuation", () => {
  assert.equal(normalize("Résumé Reviewer (Zoë's Team)"), "resume reviewer zoe s team");
  assert.equal(normalize("  Front-End / Platform,  Engineer. "), "front end platform engineer");
  assert.equal(normalize(null), "");
  assert.equal(normalize("ÅNGSTRÖM"), "angstrom");
});

test("accents fold both ways", () => {
  assert.ok(hits("resume", "Résumé Writer"));
  assert.ok(hits("résumé", "Resume Writer"));
  assert.ok(hits("zoe", "Zoë Labs", "Zoë Labs"));
  assert.ok(hits("école", "École Lead"));
});

test("tech tokens stay searchable", () => {
  assert.ok(hits("c++", "Senior C++ Developer"));
  assert.ok(hits("c#", "C# Backend Engineer"));
  assert.ok(hits(".net", "Software Engineer (.NET)"));
  assert.ok(hits("node.js", "Node.js Engineer"));
  assert.ok(hits("k8s", "Platform Engineer, K8s"));
  assert.ok(hits("sre", "SRE, Payments"));
  assert.ok(hits("c++", "Embedded C++/Rust Engineer"));
  assert.ok(hits("rust", "Embedded C++/Rust Engineer"));
});

test("tech tokens match their spaced and spelled variants", () => {
  assert.ok(hits("node js", "Node.js Engineer"));
  assert.ok(hits("nodejs", "Node.js Engineer"));
  assert.ok(hits("cpp", "Senior C++ Developer"));
  assert.ok(hits("csharp", "C# Backend Engineer"));
  assert.ok(hits("dotnet", "Software Engineer (.NET)"));
  assert.ok(hits("net", "Software Engineer (.NET)"));
  assert.ok(hits("asp net", "ASP.NET Developer"));
});

test("tech tokens are not noise", () => {
  assert.ok(!hits("c++", "Cloud Engineer"));
  assert.ok(!hits("c#", "Cloud Engineer"));
  assert.ok(!hits("node.js", "Cloud Engineer"));
  assert.ok(!hits("k8s", "Cloud Engineer"));
  assert.ok(!hits("aws", "Associate Web Services Developer"));
});

test("short queries do not match scattered letters", () => {
  // old matcher took scattered letters
  assert.ok(fuzzyScore("Senior Reliability Engineer Acme", "sre") >= 0);
  assert.ok(fuzzyScore("Associate Web Services Developer Acme", "aws") >= 0);
  assert.equal(score("sre", "Senior Reliability Engineer"), null);
  assert.equal(score("aws", "Associate Web Services Developer"), null);
  // acronyms are never expanded
  assert.equal(score("sre", "Site Reliability Engineer"), null);
  assert.ok(score("sre", "SRE") > 0);
  assert.ok(score("sre", "Staff SRE, Platform") > 0);
});

test("every word must match in any order", () => {
  assert.ok(hits("python backend", "Backend Engineer, Python"));
  assert.ok(hits("backend python", "Backend Engineer, Python"));
  assert.ok(!hits("backend ruby", "Backend Engineer, Python"));
  assert.ok(hits("stripe engineer", "Backend Engineer", "Stripe"));
  assert.ok(hits("engineer stripe", "Backend Engineer", "Stripe"));
});

test("quoted phrases need adjacent words", () => {
  assert.ok(hits('"site reliability"', "Site Reliability Engineer"));
  assert.ok(!hits('"reliability site"', "Site Reliability Engineer"));
  assert.ok(!hits('"site engineer"', "Site Reliability Engineer"));
  assert.ok(hits('"site reliab"', "Site Reliability Engineer"));
  assert.ok(hits('"node js"', "Node.js Engineer"));
  assert.ok(hits('"site reliability', "Site Reliability Engineer"));
  assert.ok(hits('"c++ developer"', "Senior C++ Developer"));
});

test("a leading minus excludes", () => {
  assert.ok(hits("engineer -senior", "Backend Engineer"));
  assert.ok(!hits("engineer -senior", "Senior Backend Engineer"));
  assert.ok(!hits("engineer -acme", "Backend Engineer", "Acme"));
  assert.ok(!hits('engineer -"backend engineer"', "Backend Engineer"));
  assert.ok(hits("front-end", "Front-End Engineer"));
  assert.equal(parseQuery("-").empty, true);
});

test("field scoping limits where a word may match", () => {
  assert.ok(hits("title:stripe", "Stripe Integrations Lead", "Other"));
  assert.ok(!hits("title:stripe", "Backend Engineer", "Stripe"));
  assert.ok(hits("company:stripe", "Backend Engineer", "Stripe"));
  assert.ok(!hits("company:engineer", "Backend Engineer", "Stripe"));
  assert.ok(hits("company:stripe title:backend", "Backend Engineer", "Stripe"));
  assert.ok(!hits("title:backend -company:stripe", "Backend Engineer", "Stripe"));
  assert.ok(hits('title:"backend eng"', "Backend Engineer", "Stripe"));
});

test("unknown field prefixes are plain text", () => {
  assert.ok(hits("location:remote", "Remote Location Lead"));
  assert.ok(!hits("location:remote", "Backend Engineer"));
  assert.ok(isEmptyQuery(parseQuery("title:")));
});

test("prefix beats substring beats typo", () => {
  const exact = score("engineer", "Engineer");
  const prefix = score("enginee", "Engineer");
  const substring = score("ginee", "Engineer");
  const typo = score("enginer", "Engineer");
  assert.ok(exact > prefix);
  assert.ok(prefix > substring);
  assert.ok(substring > typo);
  assert.ok(typo > 0);
  assert.ok(score("engineer", "Engineer") > score("engineer", "Engineers"));
});

test("typo tolerance needs five letters", () => {
  assert.ok(hits("engineeer", "Engineer"));
  assert.ok(hits("enginer", "Engineer"));
  assert.ok(hits("scintist", "Scientist"));
  assert.ok(hits("sceintist", "Scientist"));
  assert.ok(hits("pyhton", "Python Developer"));
  assert.ok(hits("pythn", "Python Developer"));
  assert.ok(!hits("rubi", "Ruby"));
  assert.ok(!hits("jaba", "Java Developer"));
  assert.ok(!hits("pyhn", "Python"));
  assert.ok(!hits("javas", "Jazz Developer"));
});

test("company name bonus and title start bonus", () => {
  const whole = scoreRow(parseQuery("stripe"), { title: "Engineer", company: "Stripe" });
  const inTitle = scoreRow(parseQuery("stripe"), { title: "Engineer at Stripe", company: "Other" });
  assert.ok(whole > inTitle);
  const start = score("backend", "Backend Engineer");
  const later = score("backend", "Senior Backend Engineer");
  assert.ok(start > later);
});

test("adjacent words beat scattered ones", () => {
  const near = score("data engineer", "Data Engineer");
  const apart = score("data engineer", "Data Platform Engineer");
  assert.ok(near > apart);
});

test("a short typed prefix is not noise", () => {
  assert.ok(hits("ba", "Backend Engineer"));
  assert.ok(!hits("zq", "Backend Engineer"));
  assert.ok(fuzzyScore("Backend Engineer Acme", "kn") >= 0);
  assert.equal(score("kn", "Backend Engineer", "Acme"), null);
});

test("an empty query is no filter", () => {
  for (const query of ["", "   ", "\t\n", null, undefined, '""', "---", "&"]) {
    const parsed = parseQuery(query);
    assert.ok(isEmptyQuery(parsed), String(query));
    assert.equal(scoreRow(parsed, { title: "x", company: "y" }), 0);
    assert.deepEqual(highlightRanges(parsed, "anything"), []);
  }
});

test("a query of only exclusions matches the rest", () => {
  const parsed = parseQuery("-senior");
  assert.equal(isEmptyQuery(parsed), false);
  assert.equal(scoreRow(parsed, { title: "Engineer", company: "Acme" }), 0);
  assert.equal(scoreRow(parsed, { title: "Senior Engineer", company: "Acme" }), null);
});

test("missing fields are empty text", () => {
  assert.equal(scoreRow(parseQuery("acme"), { title: undefined, company: "Acme" }) > 0, true);
  assert.equal(scoreRow(parseQuery("acme"), {}), null);
});

test("scoreFields searches any named fields", () => {
  const parsed = parseQuery("globex");
  assert.ok(scoreFields(parsed, { company: "Acme", aliases: "Globex Corp" }) > 0);
  assert.equal(scoreFields(parsed, { company: "Acme" }), null);
});

test("highlight offsets point into the original text", () => {
  assert.deepEqual(marked("zoe", "Zoë Labs"), ["Zoë"]);
  assert.deepEqual(marked("resume", "Résumé Writer"), ["Résumé"]);
  assert.deepEqual(marked("res", "Résumé Writer"), ["Rés"]);
  assert.deepEqual(marked("senior", "Backend Engineer"), []);
  assert.deepEqual(marked("back eng", "Backend Engineer"), ["Back", "Eng"]);
  assert.deepEqual(marked("c++", "Senior C++ Developer"), ["C++"]);
  assert.deepEqual(marked("node js", "Node.js Engineer"), ["Node", "js"]);
  assert.deepEqual(marked("nodejs", "Node.js Engineer"), ["Node.js"]);
  assert.deepEqual(marked("net", "Software Engineer (.NET)"), ["NET"]);
  assert.deepEqual(marked('"site reliab"', "Site Reliability Engineer"), ["Site Reliability"]);
  assert.deepEqual(marked("ginee", "Engineer"), ["ginee"]);
  assert.deepEqual(marked("sre", "SRE"), ["SRE"]);
});

test("highlights survive decomposed accents and astral text", () => {
  const decomposed = "Zoé Labs";
  assert.deepEqual(marked("zoe", decomposed), ["Zoé"]);
  const emoji = "🚀 Rocket Engineer";
  assert.deepEqual(marked("rocket", emoji), ["Rocket"]);
  assert.deepEqual(marked("İstanbul", "İstanbul Office"), ["İstanbul"]);
});

test("highlights merge overlaps and honour fields", () => {
  assert.deepEqual(marked("back backend", "Backend Engineer"), ["Backend"]);
  assert.deepEqual(marked("title:stripe", "Stripe", "company"), []);
  assert.deepEqual(marked("title:stripe", "Stripe", "title"), ["Stripe"]);
  assert.deepEqual(marked("-stripe engineer", "Stripe Engineer"), ["Engineer"]);
});

test("unicode and long input stay safe", () => {
  assert.ok(hits("日本語", "日本語 Engineer"));
  assert.ok(hits("naïve", "Naive Systems"));
  const huge = "a".repeat(100000);
  assert.doesNotThrow(() => parseQuery(huge));
  assert.ok(parseQuery(huge).terms[0].words[0].length <= 256);
  assert.doesNotThrow(() => score(huge, huge, huge));
  assert.doesNotThrow(() => score("engineer", huge, huge));
  const many = parseQuery(Array.from({ length: 200 }, (_, i) => `w${i}`).join(" "));
  assert.ok(many.terms.length <= 12);
});

test("regex characters in queries are plain text", () => {
  const started = Date.now();
  for (const query of ["(a+)+$", "[", "\\", ".*", "a{1,}{", "(?:x*)*y", "^$", "+", "*"]) {
    assert.doesNotThrow(() => score(query, "Backend (a+)+ Engineer"));
  }
  assert.ok(hits("a+", "a+ Engineer"));
  assert.ok(Date.now() - started < 500);
});

test("object prototype names are ordinary words", () => {
  assert.ok(hits("constructor", "Constructor Lead"));
  assert.ok(hits("__proto__", "__proto__ Tester"));
  assert.doesNotThrow(() => score("toString", "valueOf hasOwnProperty"));
});

test("only a single leading minus negates", () => {
  assert.ok(hits("--engineer", "Backend Engineer"));
  assert.ok(hits("--engineer", "Engineer"));
  assert.ok(isEmptyQuery(parseQuery("-")));
  assert.ok(isEmptyQuery(parseQuery("--")));
  assert.ok(hits("- engineer", "Backend Engineer"));
  assert.equal(parseQuery("- engineer").terms.length, 1);
  assert.equal(parseQuery("- engineer").terms[0].negate, false);
  assert.ok(hits("full-stack", "Full-Stack Engineer"));
  assert.ok(hits("front-end engineer", "Front-End Engineer"));
});

test("a negated hyphenated word excludes the adjacent pair only", () => {
  assert.ok(!hits("-full-stack", "Full-Stack Engineer"));
  assert.ok(hits("-full-stack", "Full Platform Stack Engineer"));
  assert.ok(hits("-full-stack engineer", "Backend Engineer"));
  assert.ok(!hits("--engineer -backend", "Backend Engineer"));
});

test("sharp s folds to ss", () => {
  assert.ok(hits("strasse", "Straße Labs", "Acme"));
  assert.ok(hits("straße", "Strasse Labs", "Acme"));
  assert.ok(hits("gross", "Groß Systems", "Acme"));
  assert.equal(normalize("STRAẞE"), "strasse");
  assert.deepEqual(marked("strasse", "Straße Labs"), ["Straße"]);
  assert.deepEqual(marked("labs", "Straße Labs"), ["Labs"]);
  assert.deepEqual(marked("gross", "Der Groß Plan"), ["Groß"]);
  assert.deepEqual(marked("stras", "Straße Labs"), ["Straß"]);
});

test("dotted and dotless i keep offsets intact", () => {
  assert.deepEqual(marked("istanbul", "İSTANBUL Office"), ["İSTANBUL"]);
  assert.deepEqual(marked("office", "İSTANBUL Office"), ["Office"]);
  assert.deepEqual(marked("ısı", "ISI ısı lab"), ["ısı"]);
  assert.deepEqual(marked("lab", "ısı İş lab"), ["lab"]);
});

test("company scope can span declared fields", () => {
  const fields = { company: "Société Générale", aliases: "SocGen" };
  const scopes = { company: ["company", "aliases"] };
  assert.equal(scoreFields(parseQuery("company:socgen"), fields), null);
  assert.ok(scoreFields(parseQuery("company:socgen"), fields, scopes) > 0);
  assert.equal(scoreFields(parseQuery("-company:socgen"), fields, scopes), null);
  assert.equal(scoreFields(parseQuery("-company:socgen"), fields), 0);
  assert.ok(scoreFields(parseQuery("company:societe"), fields, scopes) > 0);
  assert.equal(scoreFields(parseQuery("company:nothing"), fields, scopes), null);
});

test("search timer waits and then runs once", () => {
  const timers = [];
  const calls = [];
  const timer = createSearchTimer({ run: () => calls.push("run"), canRun: () => true, setTimer: (fn, ms) => timers.push({ fn, ms }), clearTimer: () => timers.pop() });
  timer.schedule("eng");
  assert.equal(calls.length, 0);
  assert.equal(timers[0].ms, 80);
  timers.shift().fn();
  assert.deepEqual(calls, ["run"]);
});

test("search timer clears at once and replaces pending work", () => {
  const pending = new Map();
  let next = 1;
  const calls = [];
  const timer = createSearchTimer({
    run: () => calls.push("run"),
    canRun: () => true,
    setTimer: (fn) => { pending.set(next, fn); return next++; },
    clearTimer: (id) => pending.delete(id),
  });
  timer.schedule("a");
  timer.schedule("ab");
  assert.equal(pending.size, 1);
  timer.schedule("   ");
  assert.deepEqual(calls, ["run"]);
  assert.equal(pending.size, 0);
});

test("search timer never runs after a cancel or a route change", () => {
  const pending = new Map();
  let next = 1;
  let onInbox = true;
  const calls = [];
  const timer = createSearchTimer({
    run: () => calls.push("run"),
    canRun: () => onInbox,
    setTimer: (fn) => { pending.set(next, fn); return next++; },
    clearTimer: (id) => pending.delete(id),
  });
  timer.schedule("eng");
  timer.cancel();
  assert.equal(pending.size, 0);
  timer.schedule("eng");
  onInbox = false;
  [...pending.values()][0]();
  timer.schedule("");
  assert.deepEqual(calls, []);
});
