import assert from "node:assert/strict";
import test from "node:test";

import { companyFilter, filterCompanies, linkHref, linkLabel, mergeCopy, mergeTargets } from "../src/jobscout/static/company-edit.js";

const company = (name, extra = {}) => ({
  key: name.toLowerCase(), name, applications: [], postings: [], contacts_count: 0,
  saved_links_count: 0, has_note: false, hidden: false, manual: false, aliases: [], ...extra,
});

const companies = [
  company("Acme", { applications: [{}], aliases: ["Globex"] }),
  company("Initech", { manual: true, contacts_count: 2 }),
  company("Hooli", { hidden: true, applications: [{}] }),
];

test("the default filter leaves hidden companies out", () => {
  assert.deepEqual(filterCompanies(companies).map((item) => item.name), ["Acme", "Initech"]);
});

test("each filter keeps its own companies", () => {
  const names = (show) => filterCompanies(companies, { show }).map((item) => item.name);
  assert.deepEqual(names("applied"), ["Acme"]);
  assert.deepEqual(names("contacts"), ["Initech"]);
  assert.deepEqual(names("manual"), ["Initech"]);
  assert.deepEqual(names("hidden"), ["Hooli"]);
});

test("search matches names and merged names", () => {
  assert.deepEqual(filterCompanies(companies, { query: " ini " }).map((item) => item.name), ["Initech"]);
  assert.deepEqual(filterCompanies(companies, { query: "GLOB" }).map((item) => item.name), ["Acme"]);
  assert.deepEqual(filterCompanies(companies, { query: "hoo" }), []);
  assert.deepEqual(filterCompanies(companies, { query: "hoo", show: "hidden" }).map((item) => item.name), ["Hooli"]);
});

test("unknown filters fall back to all", () => {
  assert.equal(companyFilter("everything"), "all");
  assert.equal(companyFilter("__proto__"), "all");
  assert.equal(companyFilter("hidden"), "hidden");
});

test("merge targets skip the source and hidden companies", () => {
  const zeta = company("Zeta");
  assert.deepEqual(mergeTargets([zeta, ...companies], companies[0]).map((item) => item.name), ["Initech", "Zeta"]);
});

test("merge copy names both companies and the counts that move", () => {
  const source = company("Globex", { postings: [{}, {}], contacts_count: 1, saved_links_count: 0, has_note: true });
  const copy = mergeCopy(source, company("Acme"));
  assert.equal(copy.title, "Merge Globex into Acme?");
  assert.match(copy.body, /^2 jobs and 1 contact move to Acme/);
  assert.match(mergeCopy(company("Globex", { postings: [{}] }), company("Acme")).body, /^1 job moves to Acme/);
  assert.match(mergeCopy(company("Globex", { saved_links_count: 3, contacts_count: 2 }), company("Acme")).body, /^0 jobs, 2 contacts, and 3 links move/);
  assert.match(copy.body, /Acme keeps its name, ATS account and logo\./);
  assert.match(copy.body, /Globex's notes are added below Acme's\./);
  assert.doesNotMatch(mergeCopy(company("Globex"), company("Acme")).body, /notes/);
});

test("only https links get an href", () => {
  assert.equal(linkHref("https://acme.example/careers"), "https://acme.example/careers");
  assert.equal(linkHref("http://acme.example"), "");
  assert.equal(linkHref("javascript:alert(1)"), "");
  assert.equal(linkHref("not a url"), "");
  assert.equal(linkHref(null), "");
});

test("a link without a label shows its host", () => {
  assert.equal(linkLabel({ label: "Careers", url: "https://acme.example/jobs" }), "Careers");
  assert.equal(linkLabel({ label: "", url: "https://jobs.acme.example/x" }), "jobs.acme.example");
  assert.equal(linkLabel({ label: "", url: "nonsense" }), "nonsense");
});
