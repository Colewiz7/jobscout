import copy
import re

from .client import BridgeError

# diff needs stable row identity
SECTIONS = {
    "fields": ("key", "label"),
    "documents": ("id", "name"),
    "answer_templates": ("id", "name"),
    "stories": ("id", "title"),
}
COMPUTED = ("copied_fields", "answer_overrides", "company_account", "eligibility_checks")
SKIP = ("application_count",)
CLIP = 160


def editable(profile: dict) -> dict:
    return {key: copy.deepcopy(value) for key, value in profile.items() if key not in COMPUTED}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60]


def _find(rows: list, ident: str, row: dict, label: str):
    if row.get(ident) not in (None, ""):
        return next((item for item in rows if str(item.get(ident)) == str(row[ident])), None)
    name = str(row.get(label) or "").casefold()
    return next((item for item in rows if name and str(item.get(label) or "").casefold() == name), None)


def apply(profile: dict, set_fields: dict | None, upsert: dict | None, remove: dict | None) -> dict:
    new = editable(profile)
    for key, value in (set_fields or {}).items():
        row = next((item for item in new["fields"] if item["key"] == key), None)
        if row is None:
            raise BridgeError("unknown profile field %r; add it with upsert.fields" % key)
        row["value"] = value
    for section, rows in (upsert or {}).items():
        if section not in SECTIONS:
            raise BridgeError("upsert section must be one of %s" % ", ".join(SECTIONS))
        ident, label = SECTIONS[section]
        for row in rows:
            row = {key: value for key, value in row.items() if key not in SKIP}
            current = _find(new[section], ident, row, label)
            if current is not None:
                current.update(row)
                continue
            if section in {"fields", "stories"} and not row.get(ident):
                if not row.get(label):
                    raise BridgeError("a new %s row needs %s" % (section, label))
                row[ident] = slug(str(row[label]))
            if section in {"documents", "answer_templates"}:
                row.setdefault(ident, None)
            new[section].append(row)
    for section, ids in (remove or {}).items():
        if section not in SECTIONS:
            raise BridgeError("remove section must be one of %s" % ", ".join(SECTIONS))
        ident = SECTIONS[section][0]
        have = {str(item.get(ident)) for item in new[section]}
        missing = [item for item in ids if str(item) not in have]
        if missing:
            raise BridgeError("nothing to remove in %s with %s %s" % (section, ident, ", ".join(map(str, missing))))
        new[section] = [item for item in new[section] if str(item.get(ident)) not in {str(i) for i in ids}]
    return new


def _short(value):
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= CLIP else text[:CLIP] + "..."


def _clean(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in SKIP}


def diff(old: dict, new: dict) -> dict:
    out: dict = {}
    for section, (ident, label) in SECTIONS.items():
        before = {str(row.get(ident)): _clean(row) for row in old.get(section, [])}
        after = {str(row.get(ident) if row.get(ident) is not None else "new:%d" % index): _clean(row) for index, row in enumerate(new.get(section, []))}
        added = [_short(row.get(label)) for key, row in after.items() if key not in before]
        removed = [_short(row.get(label)) for key, row in before.items() if key not in after]
        changed = []
        for key, row in after.items():
            if key in before and row != before[key]:
                moves = {name: [_short(before[key].get(name)), _short(row.get(name))] for name in sorted(set(row) | set(before[key])) if row.get(name) != before[key].get(name)}
                changed.append({label: _short(row.get(label)), "changes": moves})
        if added or removed or changed:
            out[section] = {name: value for name, value in (("added", added), ("removed", removed), ("changed", changed)) if value}
    for name in ("name", "email", "phone", "location", "highlights"):
        if old.get(name) != new.get(name):
            out[name] = [_short(old.get(name)), _short(new.get(name))]
    return out
