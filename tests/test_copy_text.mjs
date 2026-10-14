import assert from "node:assert/strict";
import test from "node:test";

import { inboxCopyText } from "../src/jobscout/static/copy-text.js";

const job = { company: "Acme", title: "SRE Intern", location: "Austin, TX", url: "https://acme.example/1" };

test("jobs without taglines keep the one-line-per-job format", () => {
  assert.equal(
    inboxCopyText([job, { ...job, company: "Beta" }]),
    "Acme — SRE Intern — Austin, TX — https://acme.example/1\nBeta — SRE Intern — Austin, TX — https://acme.example/1",
  );
});

test("a tagline goes under its job and listings are separated by a blank line", () => {
  const text = inboxCopyText([
    { ...job, tagline: "🟢 High · Kubernetes SRE work · Summer 2027 · Austin, onsite" },
    { ...job, company: "Beta" },
  ]);
  assert.equal(text, [
    "Acme — SRE Intern — Austin, TX — https://acme.example/1",
    "🟢 High · Kubernetes SRE work · Summer 2027 · Austin, onsite",
    "",
    "Beta — SRE Intern — Austin, TX — https://acme.example/1",
  ].join("\n"));
});

test("missing fields and stray whitespace are dropped", () => {
  assert.equal(inboxCopyText([{ company: " Acme ", title: "", url: "u", tagline: "  🟡 Med · x\n· y " }]), "Acme — u\n🟡 Med · x · y");
});
