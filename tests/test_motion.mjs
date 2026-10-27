import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { Spring, VelocityTracker, clampVelocity, decay, fromRatio, project, rubberband, solve, springTokens, toPhysics } from "../src/jobscout/static/motion.js";

const vectors = JSON.parse(readFileSync(new URL("./fixtures/motion-vectors.json", import.meta.url)));

const close = (actual, expected, label) => assert.ok(Math.abs(actual - expected) <= 0.001, `${label}: ${actual} vs ${expected}`);

test("springs match every vector", () => {
  for (const c of vectors.spring) {
    const label = `d=${c.duration} b=${c.bounce} v0=${c.v0} t=${c.t}`;
    const { stiffness, damping, mass } = toPhysics(c);
    const s = solve(stiffness, damping, mass, c.from - c.to, c.v0, c.t);
    close(c.to + s.x, c.x, label + " solve x");
    close(s.v, c.v, label + " solve v");

    const spring = new Spring(c.from, toPhysics(c));
    spring.setTarget(c.to, 0, c.v0);
    const { value, velocity } = spring.sample(c.t * 1000);
    close(value, c.x, label + " spring x");
    close(velocity, c.v, label + " spring v");
  }
});

test("scroll helpers match every vector", () => {
  for (const c of vectors.rubberband) close(rubberband(c.x, c.d, c.c), c.out, `rubberband x=${c.x} d=${c.d}`);
  for (const c of vectors.project) close(project(c.v, c.rate), c.out, `project v=${c.v} rate=${c.rate}`);
  for (const c of vectors.decay) close(decay(c.v, c.rate, c.t), c.dx, `decay v=${c.v} t=${c.t}`);
});

test("retarget keeps velocity", () => {
  const spring = new Spring(0);
  spring.setTarget(100, 0, 800);
  const before = spring.sample(120);
  spring.setTarget(-40, 120);
  const after = spring.sample(120);
  assert.equal(after.velocity, before.velocity);
  assert.equal(after.value, before.value);
  assert.notEqual(after.velocity, 0);
});

test("60 and 120 hz trace the same curve", () => {
  const run = (hz) => {
    const spring = new Spring(0, fromRatio(0.9, 700));
    spring.setTarget(100, 0, 1500);
    const seen = new Map();
    for (let i = 0; i <= hz; i++) {
      const now = (i / hz) * 1000;
      if (now === 200) spring.setTarget(30, now);
      seen.set(now, spring.sample(now));
    }
    return seen;
  };
  const at60 = run(60), at120 = run(120);
  let shared = 0;
  for (const [now, sample] of at60) {
    assert.deepEqual(at120.get(now), sample, `t=${now}`);
    shared++;
  }
  assert.equal(shared, 61);
});

test("clamped release never passes the target", () => {
  const duration = 0.5;
  const phys = toPhysics({ duration, bounce: 0 });
  for (const distance of [-300, -40, -2, 2, 40, 300]) {
    for (let release = -20000; release <= 20000; release += 250) {
      const spring = new Spring(0, phys);
      spring.setTarget(distance, 0, clampVelocity(release, distance, duration));
      for (let i = 1; i <= 120; i++) {
        const { value } = spring.sample((i / 60) * 1000);
        assert.ok(Math.sign(distance) * (distance - value) >= 0, `d=${distance} v=${release} frame ${i} at ${value}`);
      }
    }
  }
});

test("spatial-default is ratio 0.9 stiffness 700", () => {
  const { ratio, stiffness } = springTokens["spatial-default"];
  const phys = fromRatio(ratio, stiffness);
  assert.equal(phys.mass, 1);
  assert.equal(phys.stiffness, 700);
  assert.equal(phys.damping, 2 * 0.9 * Math.sqrt(700));
});

test("velocity tracker reads the last 100 ms", () => {
  const tracker = new VelocityTracker();
  for (let t = 0; t <= 100; t += 10) tracker.add(0, t); // still at first
  for (let t = 110; t <= 200; t += 10) tracker.add((t - 100) * 2, t);
  close(tracker.velocity(200), 2000, "moving");
  close(tracker.velocity(250), 2000, "just lifted");
  assert.equal(tracker.velocity(251), 0);
  assert.equal(new VelocityTracker().velocity(0), 0);
});
