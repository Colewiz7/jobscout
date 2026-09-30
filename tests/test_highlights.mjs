import assert from "node:assert/strict";
import test from "node:test";

import { findPostingHighlights } from "../src/jobscout/static/highlight-terms.js";

const cues = (text) => findPostingHighlights(text).map(({ text: value, category }) => [value, category]);

test("CACI example distinguishes hard requirements, fit, stack, and logistics", () => {
  const found = cues(
    "The internship begins in May and lasts 12 weeks. You must work fully on-site. " +
    "Python, C/C++ and IP networking are required. No sponsorship. Minimum GPA of 3.0 preferred."
  );
  assert.ok(found.some(([value, kind]) => value === "fully on-site" && kind === "blocker"));
  assert.ok(found.some(([value, kind]) => value === "12 weeks" && kind === "fit"));
  assert.ok(found.some(([value, kind]) => value === "Python" && kind === "stack"));
  assert.ok(found.some(([value, kind]) => value === "C/C++" && kind === "stack"));
  assert.ok(found.some(([value, kind]) => value === "No sponsorship" && kind === "blocker"));
  assert.ok(found.some(([value, kind]) => /GPA of 3\.0/i.test(value) && kind === "logistics"));
});

test("Nike example picks specific tools and dates without treating DOE as pay", () => {
  const found = cues(
    "Experience creating and executing Design of Experiments (DOE) and using Siemens NX. " +
    "Graduation date between December 2027 and June 2028. This is a 10-week internship."
  );
  assert.ok(found.some(([value, kind]) => value === "Design of Experiments" && kind === "stack"));
  assert.ok(found.some(([value, kind]) => value === "DOE" && kind === "stack"));
  assert.ok(found.some(([value, kind]) => value.toLowerCase() === "graduation date" && kind === "blocker"));
  assert.ok(found.some(([value, kind]) => value === "10-week" && kind === "fit"));
  assert.ok(!found.some(([, kind]) => kind === "money"));
});

test("Audax example separates actual pay and support tasks from irrelevant cues", () => {
  const found = cues(
    "Assets under management are $42 billion. Answer service requests and support printers. " +
    "Use Windows OS, macOS and Office 365. Remote access is available. " +
    "This is 5 days in office. The hourly range is $28.00-$30.00. Volunteer leave is offered."
  );
  assert.ok(found.some(([value, kind]) => value === "service requests" && kind === "caution"));
  assert.ok(found.some(([value, kind]) => value === "Windows" && kind === "stack"));
  assert.ok(found.some(([value, kind]) => value === "5 days in office" && kind === "blocker"));
  assert.ok(found.some(([value, kind]) => value === "$28.00-$30.00" && kind === "money"));
  assert.ok(!found.some(([value]) => value === "$42" || value.toLowerCase() === "remote" || value.toLowerCase() === "volunteer"));
});

test("a negated clearance and high-school senior are not flagged as blockers", () => {
  assert.deepEqual(cues("Security Clearance Type: None/Not Required. High school senior applicants welcome."), []);
});

test("a phrase is marked once within a section", () => {
  const seen = new Set();
  assert.equal(findPostingHighlights("Python and Python", { seen }).filter((item) => item.text === "Python").length, 1);
  assert.equal(findPostingHighlights("Python", { seen }).length, 0);
});
