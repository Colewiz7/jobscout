// tile tones are theme tokens in app.css
export const TONES = ["neutral", "brass", "ink", "plum"];

export function initialsTone(key) {
  const text = String(key ?? "");
  // fnv-1a keeps the pick stable across reloads
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return TONES[hash % TONES.length];
}
