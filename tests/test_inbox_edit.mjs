import assert from "node:assert/strict";
import test from "node:test";

import { paneJob, cleanTag, hasTag, moveId, nextStepPayload, removableTags, ruleValueHelp, tagOptions, withManualTag } from "../src/jobscout/static/inbox-edit.js";

const job = (tags, manual = [], rules = tags.filter((tag) => !manual.includes(tag))) => ({ tags, manual_tags: manual, rule_tags: rules });

test("tags are trimmed and inner space collapsed like the server", () => {
  assert.equal(cleanTag("  Dream \t  job "), "Dream job");
  assert.equal(cleanTag(null), "");
});

test("the pane shows nothing once the list is empty", () => {
  const jobs = [{ dedupe_key: "a" }, { dedupe_key: "b" }];
  assert.equal(paneJob(jobs, 2, "b"), jobs[1]);
  assert.equal(paneJob(jobs, 0, "b"), undefined);
  assert.equal(paneJob(jobs, 1, "missing"), undefined);
});

test("tag checks ignore case", () => {
  assert.equal(hasTag(job(["Systems"]), "systems"), true);
  assert.equal(hasTag(job(["Systems"]), ""), false);
  assert.equal(hasTag({}, "Systems"), false);
});

test("filter options list each tag once, sorted", () => {
  assert.deepEqual(tagOptions([job(["Systems", "Dream"]), job(["dream", "Alpha"])]), ["Alpha", "Dream", "Systems"]);
});

test("only hand tags can be removed, with how many jobs have each", () => {
  const jobs = [job(["Systems", "Dream"], ["Dream"]), job(["dream", "Later"], ["dream", "Later"]), job(["Systems"])];
  assert.deepEqual(removableTags(jobs), [{ tag: "Dream", count: 2 }, { tag: "Later", count: 1 }]);
  assert.deepEqual(removableTags([job(["Systems"])]), []);
});

test("an optimistic tag keeps rule tags first and skips a rule duplicate", () => {
  const start = job(["Systems"], [], ["Systems"]);
  assert.deepEqual(withManualTag(start, "Dream", false), { manual_tags: ["Dream"], tags: ["Systems", "Dream"] });
  assert.deepEqual(withManualTag(start, "systems", false), { manual_tags: ["systems"], tags: ["Systems"] });
  const tagged = { ...start, ...withManualTag(start, "Dream", false) };
  assert.deepEqual(withManualTag(tagged, "DREAM", true), { manual_tags: [], tags: ["Systems"] });
});

test("moving an id clamps to the ends", () => {
  assert.deepEqual(moveId([1, 2, 3], 3, 0), [3, 1, 2]);
  assert.deepEqual(moveId([1, 2, 3], 1, 1), [2, 1, 3]);
  assert.deepEqual(moveId([1, 2, 3], 1, 9), [2, 3, 1]);
  assert.deepEqual(moveId([1, 2, 3], 2, -4), [2, 1, 3]);
});

test("each rule kind explains its value", () => {
  assert.match(ruleValueHelp("tag_title"), /tag/);
  assert.match(ruleValueHelp("boost_company"), /1 to 100/);
  assert.match(ruleValueHelp("archive_title"), /ignore/);
});

test("undo sends back the follow-ups the save closed", () => {
  const closed = [{ id: 7, revision: 3 }];
  assert.deepEqual(nextStepPayload("Email Sam"), { next_step: "Email Sam" });
  assert.deepEqual(nextStepPayload("", "Email Sam", closed), { next_step: "", expected_next_step: "Email Sam", reopen_follow_ups: closed });
  assert.deepEqual(nextStepPayload("", "Email Sam", []).reopen_follow_ups, []);
});
