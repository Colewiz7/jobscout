// dom free for node tests

export const PROFILE_KINDS = {
  fields: { key: "key", limit: 100, one: "field", many: "fields" },
  documents: { key: "id", limit: 50, one: "document", many: "documents" },
  answer_templates: { key: "id", limit: 50, one: "template", many: "templates" },
  stories: { key: "id", limit: 10, one: "story", many: "stories" },
};

export const itemId = (kind, item) => String(item?.[PROFILE_KINDS[kind].key] ?? "");

const same = (left, right) => String(left) === String(right);

export function newId(prefix, taken, random = Math.random) {
  const used = new Set([...taken].map(String));
  for (;;) {
    const id = `${prefix}-${random().toString(36).slice(2, 10) || "0"}`;
    if (!used.has(id)) return id;
  }
}

// keys stay put after a rename
export function fieldKey(label, taken) {
  const base = String(label).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "field";
  const used = new Set([...taken].map(String));
  if (!used.has(base)) return base;
  let suffix = 2;
  while (used.has(`${base}_${suffix}`)) suffix += 1;
  return `${base}_${suffix}`;
}

export function limitReason(kind, count) {
  const { limit, many } = PROFILE_KINDS[kind];
  return count >= limit ? `You can keep up to ${limit} ${many}. Delete one to add another.` : "";
}

function clash(doc, kind, item, skip = null) {
  const rows = (doc[kind] || []).filter((row) => row !== skip);
  if (kind === "answer_templates") {
    const name = String(item.name || "").trim().toLowerCase();
    if (rows.some((row) => String(row.name || "").trim().toLowerCase() === name)) return `A template named ${item.name} already exists.`;
  }
  if (kind === "fields" || kind === "stories") {
    if (rows.some((row) => same(itemId(kind, row), itemId(kind, item)))) return `That ${PROFILE_KINDS[kind].one} already exists.`;
  }
  return "";
}

// aliases map unsaved ids to saved
const resolve = (aliases, id) => aliases?.[id] ?? id;

// returns the inverse op or error
export function applyOp(doc, op, aliases = {}) {
  const rows = op.type === "default" ? (doc.documents ||= []) : (doc[op.kind] ||= []);
  const find = (id) => rows.findIndex((row) => same(itemId(op.kind || "documents", row), resolve(aliases, id)));
  if (op.type === "add") {
    const reason = limitReason(op.kind, rows.length) || clash(doc, op.kind, op.item);
    if (reason) return { error: reason };
    const index = Math.max(0, Math.min(rows.length, op.index ?? rows.length));
    const item = structuredClone(op.item);
    if (op.kind === "documents" && item.default) rows.forEach((row) => { row.default = false; });
    rows.splice(index, 0, item);
    return { inverse: { type: "remove", kind: op.kind, id: itemId(op.kind, item) } };
  }
  const index = op.type === "default" ? -1 : find(op.id);
  if (op.type !== "default" && index < 0) return { error: `That ${PROFILE_KINDS[op.kind].one} was already removed.` };
  if (op.type === "remove") {
    const [item] = rows.splice(index, 1);
    return { inverse: { type: "add", kind: op.kind, item, index } };
  }
  if (op.type === "move") {
    const to = Math.max(0, Math.min(rows.length - 1, op.to));
    if (to === index) return { noop: true };
    const [item] = rows.splice(index, 1);
    rows.splice(to, 0, item);
    return { inverse: { type: "move", kind: op.kind, id: op.id, to: index } };
  }
  if (op.type === "update") {
    const item = rows[index];
    const reason = op.patch.name !== undefined ? clash(doc, op.kind, { ...item, ...op.patch }, item) : "";
    if (reason) return { error: reason };
    const previous = Object.fromEntries(Object.keys(op.patch).map((key) => [key, item[key]]));
    Object.assign(item, op.patch);
    return { inverse: { type: "update", kind: op.kind, id: op.id, patch: previous } };
  }
  if (op.type === "default") {
    const previous = rows.find((row) => row.default);
    const target = op.id == null ? null : rows.find((row) => same(row.id, resolve(aliases, op.id)));
    if (op.id != null && !target) return { error: "That document was already removed." };
    rows.forEach((row) => { row.default = row === target; });
    return { inverse: { type: "default", id: previous ? previous.id : null } };
  }
  return { error: "Unknown change." };
}

// keys let undo drop stale entries
export function opKeys(doc, op) {
  if (op.type === "default") return ["profile:documents:default"];
  const { kind } = op;
  const id = op.type === "add" ? itemId(kind, op.item) : String(op.id);
  if (op.type === "move") {
    const ids = (doc[kind] || []).map((row) => itemId(kind, row));
    const from = ids.indexOf(id);
    const lo = Math.min(from, op.to);
    const hi = Math.max(from, op.to);
    return ids.slice(Math.max(0, lo), hi + 1).map((each) => `profile:${kind}:${each}`);
  }
  return [`profile:${kind}:${id}`];
}

// imports never drop a document
export function mergeDocuments(existing, incoming) {
  const merged = existing.map((row) => ({ ...row }));
  const unmatched = new Map(merged.map((row, index) => [index, String(row.name ?? "").trim().toLowerCase()]));
  const taken = new Set(merged.map((row) => String(row.id)));
  let picked = null;
  for (const row of incoming) {
    const name = String(row.name ?? "").trim().toLowerCase();
    let index = [...unmatched].find(([, value]) => value === name)?.[0];
    if (index === undefined) {
      const id = newId("document", taken);
      taken.add(id);
      index = merged.push({ ...row, id, default: false }) - 1;
    } else {
      unmatched.delete(index);
      merged[index] = { ...merged[index], ...row, id: merged[index].id, default: merged[index].default === true }; // links point at the id
    }
    if (row.default === true) picked = merged[index];
  }
  if (picked) for (const row of merged) row.default = row === picked;
  return merged;
}

// server order is sort_order then id
export function withOrder(doc) {
  const out = {};
  for (const kind of Object.keys(PROFILE_KINDS)) out[kind] = (doc[kind] || []).map((row, index) => ({ ...row, sort_order: index * 10 }));
  return out;
}

// pairs unsaved ids with server ids
export function adoptIds(sent, saved, known) {
  const aliases = {};
  for (const kind of ["documents", "answer_templates"]) {
    const out = sent[kind] || [];
    const back = [...(saved[kind] || [])].sort((left, right) => (left.sort_order ?? 0) - (right.sort_order ?? 0));
    if (out.length !== back.length) continue;
    out.forEach((row, index) => {
      if (row.id != null && known.has(`${kind}:${row.id}`)) return;
      if (kind === "answer_templates" && String(row.name).trim() !== String(back[index].name)) return;
      if (row.id != null && !same(row.id, back[index].id)) aliases[row.id] = back[index].id;
    });
  }
  return aliases;
}

// the live answer beats an orphan
export function renameOverrides(overrides, from, to) {
  if (!overrides || !(from in overrides) || from === to) return { ...(overrides || {}) };
  const next = { ...overrides };
  next[to] = next[from];
  delete next[from];
  return next;
}

export function referenceWarning(count) {
  if (!count) return "";
  if (count === 1) return "1 application lists this as the resume sent. It keeps the resume name but loses the link.";
  return `${new Intl.NumberFormat().format(count)} applications list this as the resume sent. They keep the resume name but lose the link.`;
}

// the server already cleared those links
export function restoredMessage(count) {
  if (!count) return "Document restored.";
  if (count === 1) return "Document restored. Its link from 1 application wasn't restored.";
  return `Document restored. Its links from ${new Intl.NumberFormat().format(count)} applications weren't restored.`;
}

export function eligibilityWarning(key, checks) {
  const names = checks?.[key] || [];
  if (!names.length) return "";
  return `Eligibility checks use this field: ${names.join(", ")}. Without it, those checks show as unknown.`;
}
