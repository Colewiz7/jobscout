// ranked title and company matching

const MAX_QUERY = 256;
const MAX_TERMS = 12;
const MAX_TEXT = 2000;
const CACHE_LIMIT = 2000;
const FIELD_PREFIX = /^(title|company):/i;
const RUN = /[\p{L}\p{N}+#.]+/gu;
const ALNUM = /[\p{L}\p{N}]/u;
const NON_ALNUM = /[^\p{L}\p{N}]/gu;
const MARKS = /\p{M}/gu;
const SPACE = /\s/;
const NEGATABLE = /[\p{L}\p{N}"]/u;
const ALIASES = new Map([["c++", ["cpp"]], ["c#", ["csharp"]], [".net", ["dotnet"]]]);
const SCORE = { exact: 100, prefix: 70, substring: 40, typo: 20 };
const TIER_RANK = { exact: 4, prefix: 3, substring: 2, typo: 1 };
const cache = new Map();

// per char so offsets map back
function fold(text) {
  let s = "";
  const starts = [];
  const ends = [];
  for (let i = 0; i < text.length;) {
    const char = String.fromCodePoint(text.codePointAt(i));
    const next = i + char.length;
    const out = char.normalize("NFD").toLowerCase().normalize("NFD").replace(MARKS, "").replaceAll("ß", "ss");
    if (!out) {
      for (let j = ends.length - 1; j >= 0 && ends[j] === i; j--) ends[j] = next;
    }
    for (let k = 0; k < out.length; k++) {
      s += out[k];
      starts.push(i);
      ends.push(next);
    }
    i = next;
  }
  return { s, starts, ends };
}

function formsOf(word, start, end) {
  const forms = [{ t: word, start, end, exact: true }];
  if (word.includes(".")) {
    let at = 0;
    for (const part of word.split(".")) {
      if (part && part !== word) forms.push({ t: part, start: start + at, end: start + at + part.length, exact: true, part: true });
      at += part.length + 1;
    }
  }
  const stripped = word.replace(NON_ALNUM, "");
  if (stripped !== word && stripped.length >= 2) forms.push({ t: stripped, start, end, exact: false });
  for (const alias of ALIASES.get(word) || []) forms.push({ t: alias, start, end, exact: false });
  return forms;
}

function tokenize(s) {
  const tokens = [];
  for (const run of s.matchAll(RUN)) {
    const raw = run[0];
    let from = 0;
    while (from < raw.length && "+#.".includes(raw[from])) from++;
    if (from > 0 && raw[from - 1] === "." && from < raw.length) from--;
    let to = raw.length;
    while (to > from && raw[to - 1] === ".") to--;
    const word = raw.slice(from, to);
    if (!ALNUM.test(word)) continue;
    const start = run.index + from;
    tokens.push({ w: word, start, end: run.index + to, forms: formsOf(word, start, run.index + to) });
  }
  return tokens;
}

function analyze(text) {
  const source = String(text ?? "").slice(0, MAX_TEXT);
  const known = cache.get(source);
  if (known) return known;
  const folded = fold(source);
  const analysis = { folded, tokens: tokenize(folded.s) };
  if (cache.size >= CACHE_LIMIT) cache.clear();
  cache.set(source, analysis);
  return analysis;
}

export function normalize(text) {
  return analyze(text).tokens.map((token) => token.w).join(" ");
}

export function parseQuery(raw) {
  const input = String(raw ?? "").slice(0, MAX_QUERY);
  const terms = [];
  let i = 0;
  while (i < input.length && terms.length < MAX_TERMS) {
    while (i < input.length && SPACE.test(input[i])) i++;
    if (i >= input.length) break;
    let negate = false;
    if (input[i] === "-" && NEGATABLE.test(input[i + 1] ?? "")) {
      negate = true;
      i++;
    }
    let fields = null;
    const prefix = FIELD_PREFIX.exec(input.slice(i, i + 8));
    if (prefix) {
      fields = [prefix[1].toLowerCase()];
      i += prefix[0].length;
    }
    let text;
    let quoted = false;
    if (input[i] === '"') {
      quoted = true;
      const close = input.indexOf('"', i + 1);
      const stop = close < 0 ? input.length : close;
      text = input.slice(i + 1, stop);
      i = close < 0 ? stop : stop + 1;
    } else {
      let j = i;
      while (j < input.length && !SPACE.test(input[j])) j++;
      text = input.slice(i, j);
      i = j;
    }
    const words = tokenize(fold(text).s).map((token) => token.w);
    if ((quoted || negate) && words.length > 1) terms.push({ type: "phrase", negate, fields, words });
    else for (const word of words) terms.push({ type: "word", negate, fields, words: [word] });
  }
  return { terms: terms.slice(0, MAX_TERMS), empty: terms.length === 0 };
}

export function isEmptyQuery(parsed) {
  return !parsed || parsed.empty;
}

function withinOne(a, b) {
  const gap = a.length - b.length;
  if (gap > 1 || gap < -1) return false;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  if (i === a.length && i === b.length) return true;
  if (gap === 0) {
    if (a.slice(i + 1) === b.slice(i + 1)) return true;
    return a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2);
  }
  return gap > 0 ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
}

function typoClose(q, t) {
  if (q.length < 5) return false;
  return withinOne(q, t) || withinOne(q, t.slice(0, q.length)) || withinOne(q, t.slice(0, q.length + 1)) || withinOne(q, t.slice(0, q.length - 1));
}

function matchForm(q, form, typo) {
  const t = form.t;
  if (t === q) return { tier: "exact", start: form.start, end: form.end };
  if (t.startsWith(q)) return { tier: "prefix", start: form.start, end: form.exact ? form.start + q.length : form.end };
  if (q.length >= 3) {
    const at = t.indexOf(q);
    if (at >= 0) return { tier: "substring", start: form.exact ? form.start + at : form.start, end: form.exact ? form.start + at + q.length : form.end };
  }
  if (typo && typoClose(q, t)) return { tier: "typo", start: form.start, end: form.exact ? Math.min(form.end, form.start + q.length) : form.end };
  return null;
}

function matchToken(q, token, typo) {
  let best = null;
  for (const form of token.forms) {
    const hit = matchForm(q, form, typo);
    if (hit && (!best || TIER_RANK[hit.tier] > TIER_RANK[best.tier])) best = hit;
  }
  return best;
}

function slotLists(analysis) {
  const whole = analysis.tokens.map((token) => ({ forms: token.forms, start: token.start, end: token.end }));
  const split = analysis.tokens.flatMap((token) => {
    const parts = token.forms.filter((form) => form.part);
    return parts.length ? parts.map((form) => ({ forms: [form], start: form.start, end: form.end })) : [{ forms: token.forms, start: token.start, end: token.end }];
  });
  return [whole, split];
}

function phraseHits(words, analysis) {
  const hits = [];
  for (const slots of slotLists(analysis)) {
    for (let i = 0; i + words.length <= slots.length; i++) {
      const fits = words.every((word, k) => slots[i + k].forms.some((form) => (k === words.length - 1 ? form.t.startsWith(word) : form.t === word)));
      if (fits) hits.push({ idx: i, score: SCORE.exact + 10 * (words.length - 1), start: slots[i].start, end: slots[i + words.length - 1].end });
    }
  }
  return hits;
}

function termHits(term, analysis, typo) {
  if (term.type === "phrase") return phraseHits(term.words, analysis);
  const hits = [];
  analysis.tokens.forEach((token, idx) => {
    const hit = matchToken(term.words[0], token, typo);
    if (hit) hits.push({ idx, score: SCORE[hit.tier], start: hit.start, end: hit.end });
  });
  return hits;
}

function bestHit(hits) {
  let best = null;
  for (const hit of hits) if (!best || hit.score > best.score) best = hit;
  return best;
}

export function scoreFields(parsed, fields, scopes = {}) {
  if (isEmptyQuery(parsed)) return 0;
  const analyses = {};
  for (const [name, text] of Object.entries(fields)) analyses[name] = analyze(text);
  let total = 0;
  const found = [];
  for (const term of parsed.terms) {
    const scoped = term.fields?.flatMap((name) => (Object.hasOwn(scopes, name) ? scopes[name] : [name]));
    const names = scoped ? scoped.filter((name) => Object.hasOwn(analyses, name)) : Object.keys(analyses);
    if (term.negate) {
      if (names.some((name) => termHits(term, analyses[name], false).length)) return null;
      continue;
    }
    const per = {};
    let top = 0;
    for (const name of names) {
      const hit = bestHit(termHits(term, analyses[name], true));
      if (!hit) continue;
      per[name] = hit;
      top = Math.max(top, hit.score);
    }
    if (!top) return null;
    total += top;
    found.push({ term, per });
  }
  if (!found.length) return 0;
  if (Object.hasOwn(analyses, "company") && found.every(({ per }) => per.company)) total += 25;
  if (found[0].per.title?.idx === 0) total += 10;
  for (let i = 1; i < found.length; i++) {
    const before = found[i - 1].per;
    const after = found[i].per;
    if (Object.keys(after).some((name) => before[name] && after[name].idx === before[name].idx + 1)) total += 10;
  }
  return total;
}

export function scoreRow(parsed, row) {
  return scoreFields(parsed, { title: row.title, company: row.company });
}

// callers slice the original text
export function highlightRanges(parsed, text, field = "") {
  if (isEmptyQuery(parsed)) return [];
  const analysis = analyze(text);
  const { starts, ends } = analysis.folded;
  const found = [];
  for (const term of parsed.terms) {
    if (term.negate || (field && term.fields && !term.fields.includes(field))) continue;
    for (const hit of termHits(term, analysis, true)) {
      if (hit.end > hit.start) found.push([starts[hit.start], ends[hit.end - 1]]);
    }
  }
  found.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged = [];
  for (const range of found) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

export function createSearchTimer({ run, canRun, delay = 80, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let handle = null;
  const cancel = () => {
    if (handle !== null) clearTimer(handle);
    handle = null;
  };
  const fire = () => {
    handle = null;
    if (canRun()) run();
  };
  // clearing the box skips the wait
  const schedule = (value) => {
    cancel();
    if (String(value).trim()) handle = setTimer(fire, delay);
    else fire();
  };
  return { schedule, cancel };
}
