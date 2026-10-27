"use strict";

document.documentElement.dataset.accent = localStorage.getItem("jobseer-accent") || "clay";

function motionMode(reduce) {
  return reduce ? "reduce" : "full";
}

// no motion flash on load
const motionQuery = matchMedia("(prefers-reduced-motion: reduce)");
document.documentElement.dataset.motion = motionMode(motionQuery.matches);
motionQuery.addEventListener("change", (event) => { document.documentElement.dataset.motion = motionMode(event.matches); });
