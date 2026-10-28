export function fuzzyScore(candidate, query) {
  const text = candidate.toLowerCase();
  const needle = query.trim().toLowerCase();
  if (!needle) return 1;
  let score = 0;
  let position = -1;
  for (const character of needle) {
    const next = text.indexOf(character, position + 1);
    if (next === -1) return -1;
    score += next === position + 1 ? 3 : 1;
    position = next;
  }
  return score - position / 100;
}

// one list feeds menu and palette
export function menuCommands(items, { prefix, detail, icon = "more", format = (spec) => spec }) {
  return items.filter((item) => !item.disabled && item.checked !== true).map((item, index) => ({
    id: `${prefix}:${index}`, icon, detail, run: item.run,
    // a status row reads as a move
    label: item.checked === false ? `Move to ${item.label}` : item.label,
    shortcut: item.shortcut ? format(item.shortcut) : "",
  }));
}

// ties keep the subject first
export function rankCommands(entries, query) {
  return entries.map((entry, order) => ({ entry, order, score: fuzzyScore(`${entry.label} ${entry.detail}`, query) }))
    .filter((item) => item.score >= 0)
    .sort((left, right) => right.score - left.score || left.order - right.order)
    .map((item) => item.entry);
}
