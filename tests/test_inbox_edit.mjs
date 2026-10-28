import assert from "node:assert/strict";
import test from "node:test";

import { cleanTag, dateInputValue, hasTag, moveId, pickedDay, reminderUndo, removableTags, ruleValueHelp, snoozeChoices, tagOptions, upcomingDay, withManualTag } from "../src/jobscout/static/inbox-edit.js";

const job = (tags, manual = [], rules = tags.filter((tag) => !manual.includes(tag))) => ({ tags, manual_tags: manual, rule_tags: rules });

test("tags are trimmed and inner space collapsed like the server", () => {
  assert.equal(cleanTag("  Dream \t  job "), "Dream job");
  assert.equal(cleanTag(null), "");
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

test("reminder undo sends the old state back", () => {
  assert.deepEqual(reminderUndo("done", { status: "pending" }), { action: "reopen" });
  assert.deepEqual(reminderUndo("reopen", { status: "done" }), { action: "done" });
  assert.deepEqual(reminderUndo("snooze", { status: "pending", snoozed_until: null }), { action: "unsnooze" });
  assert.deepEqual(reminderUndo("snooze", { status: "snoozed", snoozed_until: "2026-11-02T14:00:00Z" }), { action: "snooze", until: "2026-11-02T14:00:00Z" });
});

test("snooze choices land at 9 am and next week is the next monday", () => {
  const wednesday = new Date(2026, 9, 28, 15, 30);
  const [tomorrow, three, week] = snoozeChoices(wednesday);
  assert.deepEqual([tomorrow.until, three.until, week.until], [new Date(2026, 9, 29, 9), new Date(2026, 9, 31, 9), new Date(2026, 10, 2, 9)]);
  // on a sunday that is tomorrow
  assert.deepEqual(snoozeChoices(new Date(2026, 10, 1, 8)).at(-1).until, new Date(2026, 10, 2, 9));
  assert.deepEqual(snoozeChoices(new Date(2026, 10, 2, 8)).at(-1).until, new Date(2026, 10, 9, 9));
});

test("a picked day must be a real future day within a year", () => {
  const now = new Date(2026, 9, 28, 8);
  assert.deepEqual(pickedDay("2026-10-29", now), { until: new Date(2026, 9, 29, 9) });
  assert.equal(pickedDay("2026-10-28", now).error, "Pick a day after today.");
  assert.equal(pickedDay("2026-02-30", now).error, "Pick a real date.");
  assert.equal(pickedDay("", now).error, "Pick a date.");
  assert.equal(pickedDay("2027-12-01", now).error, "Pick a day within a year.");
  assert.equal(dateInputValue(new Date(2026, 0, 5)), "2026-01-05");
});

test("upcoming days read forward from today", () => {
  const now = new Date(2026, 9, 28, 22);
  assert.equal(upcomingDay(new Date(2026, 9, 28, 23), now, "en-US"), "Today");
  assert.equal(upcomingDay(new Date(2026, 9, 29, 1), now, "en-US"), "Tomorrow");
  assert.equal(upcomingDay(new Date(2026, 9, 31, 9), now, "en-US"), "Saturday");
  assert.equal(upcomingDay(new Date(2026, 10, 4, 9), now, "en-US"), "Nov 4");
  assert.equal(upcomingDay(new Date(2027, 0, 4, 9), now, "en-US"), "Jan 4, 2027");
  assert.equal(upcomingDay("nope", now), "");
});
