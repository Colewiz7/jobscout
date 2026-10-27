// gesture springs and scroll physics
// seconds inside, ms at the edges

export function toPhysics({ duration, bounce }) {
  const stiffness = (2 * Math.PI / duration) ** 2;
  const damping = bounce >= 0 ? (4 * Math.PI * (1 - bounce)) / duration : (4 * Math.PI) / (duration * (1 + bounce));
  return { mass: 1, stiffness, damping };
}

// carrel tokens are ratio and stiffness
export function fromRatio(ratio, stiffness) {
  return { mass: 1, stiffness, damping: 2 * ratio * Math.sqrt(stiffness) };
}

export const springTokens = {
  "spatial-fast": { ratio: 0.9, stiffness: 1400 },
  "spatial-default": { ratio: 0.9, stiffness: 700 },
  "spatial-slow": { ratio: 0.9, stiffness: 300 },
  "effects-fast": { ratio: 1, stiffness: 3800 },
  "effects-default": { ratio: 1, stiffness: 1600 },
  "effects-slow": { ratio: 1, stiffness: 800 },
};

// x0 is offset from target
// closed form so frame rate doesnt matter
export function solve(k, c, m, x0, v0, t) {
  const w0 = Math.sqrt(k / m);
  const z = c / (2 * Math.sqrt(k * m));
  if (Math.abs(z - 1) < 1e-6) {
    const C = v0 + w0 * x0, e = Math.exp(-w0 * t);
    return { x: (x0 + C * t) * e, v: (v0 - w0 * C * t) * e };
  }
  if (z < 1) {
    const wd = w0 * Math.sqrt(1 - z * z);
    const e = Math.exp(-z * w0 * t);
    const B = (v0 + z * w0 * x0) / wd;
    const cos = Math.cos(wd * t), sin = Math.sin(wd * t);
    return { x: e * (x0 * cos + B * sin), v: e * ((B * wd - z * w0 * x0) * cos - (x0 * wd + z * w0 * B) * sin) };
  }
  const s = Math.sqrt(z * z - 1);
  const r1 = -w0 * (z - s), r2 = -w0 * (z + s);
  const C2 = (v0 - r1 * x0) / (r2 - r1), C1 = x0 - C2;
  const e1 = Math.exp(r1 * t), e2 = Math.exp(r2 * t);
  return { x: C1 * e1 + C2 * e2, v: r1 * C1 * e1 + r2 * C2 * e2 };
}

export class Spring {
  constructor(value, phys = toPhysics({ duration: 0.5, bounce: 0 })) {
    this.m = phys.mass; this.k = phys.stiffness; this.c = phys.damping;
    this.from = value; this.target = value; this.v0 = 0; this.t0 = 0;
  }

  sample(now) {
    const t = (now - this.t0) / 1000;
    const s = solve(this.k, this.c, this.m, this.from - this.target, this.v0, t);
    return { value: this.target + s.x, velocity: s.v };
  }

  // starts from whats on screen
  // keeps velocity unless one is passed
  setTarget(target, now, velocity) {
    const { value, velocity: v } = this.sample(now);
    this.from = value; this.v0 = velocity ?? v; this.t0 = now; this.target = target;
  }

  jump(value, now) {
    this.from = value; this.target = value; this.v0 = 0; this.t0 = now;
  }

  atRest(now, eps = 0.5, speed = 5) {
    const { value, velocity } = this.sample(now);
    return Math.abs(value - this.target) < eps && Math.abs(velocity) < speed;
  }
}

// fast release toward a close target overshoots otherwise
export const clampVelocity = (v, distance, duration) => {
  const max = (2 * Math.PI / duration) * Math.abs(distance);
  return Math.sign(v) * Math.min(Math.abs(v), max);
};

export const project = (v, rate = 0.998) => (v / 1000) * rate / (1 - rate);
export const decay = (v, rate, t) => v * (Math.pow(rate, 1000 * t) - 1) / (1000 * Math.log(rate));
export const rubberband = (x, d, c = 0.55) => Math.sign(x) * (Math.abs(x) * d * c) / (d + c * Math.abs(x));

export class VelocityTracker {
  constructor() {
    this.s = [];
  }

  add(x, t) {
    this.s.push({ x, t });
    while (this.s.length > 1 && t - this.s[0].t > 100) this.s.shift();
  }

  velocity(now) {
    if (this.s.length < 2) return 0;
    const a = this.s[0], b = this.s[this.s.length - 1];
    if (now - b.t > 50) return 0; // rested before lifting
    return ((b.x - a.x) / (b.t - a.t)) * 1000;
  }
}
