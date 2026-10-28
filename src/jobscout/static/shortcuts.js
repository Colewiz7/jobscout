// one spec drives keys and labels
// mac and linux share bindings

const NAMES = { Mod: "Ctrl", Control: "Ctrl", Meta: "Cmd", ContextMenu: "Menu" };

export function parseShortcut(spec) {
  const parts = spec.split("+");
  const key = parts.pop();
  const mods = new Set(parts);
  return { key, mod: mods.has("Mod"), ctrl: mods.has("Control"), meta: mods.has("Meta"), shift: mods.has("Shift"), alt: mods.has("Alt") };
}

export function matchShortcut(event, spec) {
  const want = parseShortcut(spec);
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const target = want.key.length === 1 ? want.key.toLowerCase() : want.key;
  if (key !== target) return false;
  if (want.mod ? !(event.ctrlKey || event.metaKey) : event.ctrlKey !== want.ctrl || event.metaKey !== want.meta) return false;
  if (event.altKey !== want.alt) return false;
  // ? needs shift on most layouts
  if (want.key.length === 1 && !/[a-z]/i.test(want.key)) return true;
  return event.shiftKey === want.shift;
}

export function ariaKeyshortcuts(specs) {
  return [].concat(specs).flatMap((spec) => {
    const { key, mod, ctrl, meta, shift, alt } = parseShortcut(spec);
    const rest = [ctrl && "Control", meta && "Meta", alt && "Alt", shift && "Shift"].filter(Boolean);
    const name = key.length === 1 ? key.toUpperCase() : key;
    return mod ? [["Control", ...rest, name].join("+"), ["Meta", ...rest, name].join("+")] : [[...rest, name].join("+")];
  }).join(" ");
}

export function formatShortcut(spec, mac = false) {
  const parts = spec.split("+");
  const key = parts.pop();
  const mods = parts.map((part) => mac && part === "Mod" ? "⌘" : NAMES[part] || part);
  return [...mods, NAMES[key] || (key.length === 1 ? key.toUpperCase() : key)].join(" ");
}

// inputs that take no typing
const TEXT_TYPES = new Set(["text", "search", "email", "url", "tel", "password", "number", "date", "datetime-local", "month", "week", "time"]);

export function isTextEntry(element) {
  if (!element || typeof element.tagName !== "string") return false;
  if (element.isContentEditable) return true;
  const tag = element.tagName.toLowerCase();
  if (tag === "textarea" || tag === "select") return true;
  if (["textbox", "searchbox", "combobox"].includes(element.getAttribute?.("role"))) return true;
  return tag === "input" && TEXT_TYPES.has(String(element.type || "text").toLowerCase());
}
