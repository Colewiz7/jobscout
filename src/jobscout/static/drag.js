// pointer drag, flip and keyboard lift

export const SPATIAL_DEFAULT = "cubic-bezier(0.21, 0.05, 0.28, 1)";
const SHIFT = 200;
const FLIP_CAP = 30;

export const reducedMotion = () => document.documentElement.dataset.motion === "reduce";

const onScreen = (rect) => rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;

// moving a focused node blurs it
export function placeNodes(parent, nodes, anchor = null) {
  const at = anchor && nodes.includes(anchor) ? nodes.indexOf(anchor) : -1;
  if (at < 0) { nodes.forEach((node) => parent.append(node)); return; }
  let before = anchor;
  for (const node of nodes.slice(0, at).reverse()) {
    if (node.nextElementSibling !== before) parent.insertBefore(node, before);
    before = node;
  }
  let after = anchor;
  for (const node of nodes.slice(at + 1)) {
    if (after.nextElementSibling !== node) parent.insertBefore(node, after.nextElementSibling);
    after = node;
  }
}

// carrel: read, cancel, restart from there
export function flip(nodes, change) {
  if (reducedMotion()) { change(); return; }
  const first = new Map(nodes.map((node) => [node, node.getBoundingClientRect()]));
  change();
  const moves = [];
  for (const node of nodes) {
    if (!node.isConnected) continue;
    node.getAnimations().forEach((animation) => { if (animation.id === "flip") animation.cancel(); });
    const before = first.get(node);
    const after = node.getBoundingClientRect();
    const dx = before.left - after.left;
    const dy = before.top - after.top;
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
    if (!onScreen(before) && !onScreen(after)) continue; // content-visibility skips these
    moves.push([node, dx, dy]);
  }
  if (moves.length > FLIP_CAP) return;
  for (const [node, dx, dy] of moves) {
    const animation = node.animate([{ translate: `${dx}px ${dy}px` }, { translate: "0 0" }], { duration: SHIFT, easing: SPATIAL_DEFAULT });
    animation.id = "flip";
  }
}
