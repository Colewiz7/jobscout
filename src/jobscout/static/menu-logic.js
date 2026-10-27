// pure menu math, no dom here

// one path for points and buttons
export function placeMenu({ anchor, width, height, viewport, align = "start", gap = 4, margin = 8 }) {
  const room = { below: viewport.height - margin - anchor.bottom - gap, above: anchor.top - margin - gap };
  const below = height <= room.below || room.below >= room.above;
  const maxHeight = Math.max(0, Math.min(height, viewport.height - margin * 2, Math.max(room.below, room.above)));
  let top = below ? anchor.bottom + gap : anchor.top - gap - maxHeight;
  const fitsStart = anchor.left + width <= viewport.width - margin;
  const fitsEnd = anchor.right - width >= margin;
  const roomier = viewport.width - anchor.left >= anchor.right; // neither fits, take the bigger side
  const start = fitsStart && fitsEnd ? align === "start" : fitsStart || (!fitsEnd && roomier);
  let left = start ? anchor.left : anchor.right - width;
  left = Math.min(Math.max(left, margin), Math.max(margin, viewport.width - margin - width));
  top = Math.min(Math.max(top, margin), Math.max(margin, viewport.height - margin - maxHeight));
  return { left, top, maxHeight, origin: `${start ? "left" : "right"} ${below ? "top" : "bottom"}` };
}

// menus wrap, per the roving rules
export function stepIndex(count, current, delta) {
  if (!count) return -1;
  if (current < 0) return delta > 0 ? 0 : count - 1;
  return (current + delta + count) % count;
}

// matches native listbox typeahead
export function typeahead(labels, buffer, current) {
  const query = buffer.toLowerCase();
  const offset = query.length === 1 ? 1 : 0;
  for (let step = 0; step < labels.length; step += 1) {
    const index = (Math.max(current, 0) + offset + step) % labels.length;
    if (labels[index].toLowerCase().startsWith(query)) return index;
  }
  return -1;
}
