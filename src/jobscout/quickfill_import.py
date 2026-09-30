"""Import the local Super+J ``snip`` answer format into Quick-fill.

The source contains personal information. Read it from stdin so neither the
contents nor an intermediate JSON document need to be written to disk.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from jobscout import db


GROUPS = {
    "Identity": {"Full name", "First name", "Last name"},
    "Contact": {
        "Email", "Phone", "Phone (digits)", "Location", "City", "State",
        "Street address", "Permanent city", "Zip", "Permanent address (full)",
    },
    "Links": {"LinkedIn", "GitHub", "Portfolio / website", "TigerHub repo", "WizSearch repo"},
    "Education": {
        "School", "Degree type", "Degree (full)", "Major / field of study",
        "Major (dropdown fallback)", "Minor", "Immersion", "GPA", "Start date",
        "Start year", "Graduation (month + year)", "Graduation year", "Coursework", "Clubs",
    },
    "Work authorization": {"Work authorization", "Eligible to work in US", "Require sponsorship"},
    "Availability": {"Willing to relocate", "Availability", "Prior co-ops completed"},
    "Compensation": {"Salary (hourly)", "Salary (annual)"},
    "Experience": {"Employer", "Job title", "Job dates", "Job location", "Job description"},
    "Skills": {"Skills (short)", "Skills (full)"},
    "Optional disclosures": {"EEO questions"},
    "Short answers": {"Summary", "Homelab pitch", "Why DevOps", "Why this company", "Anything else to share"},
}

KEYS = {
    "Full name": "name", "Email": "email", "Phone": "phone", "Location": "location",
    "LinkedIn": "linkedin", "GitHub": "github", "Portfolio / website": "portfolio",
    "School": "school", "Degree (full)": "degree", "GPA": "gpa",
    "Graduation (month + year)": "graduation", "Work authorization": "work_authorization",
    "Availability": "availability",
}


def parse_snippets(source: str) -> tuple[dict, list[str]]:
    """Return a validated Quick-fill document and labels skipped as TODOs."""
    fields: list[dict] = []
    templates: list[dict] = []
    skipped: list[str] = []
    seen_labels: set[str] = set()
    seen_keys: set[str] = set()
    groups_by_label = {label: group for group, labels in GROUPS.items() for label in labels}
    for line in source.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if " :: " not in line:
            raise ValueError("A snippet line has no ' :: ' separator")
        label, value = line.split(" :: ", 1)
        label = label.strip()
        value = value.replace(r"\n", "\n")
        if not label or label in seen_labels:
            raise ValueError("A snippet label is empty or duplicated")
        seen_labels.add(label)
        group = groups_by_label.get(label)
        if not group:
            raise ValueError(f"Unmapped snippet label: {label}")
        if re.search(r"\bTODO\b", value, flags=re.IGNORECASE) or not value.strip():
            skipped.append(label)
            continue
        if group == "Short answers":
            templates.append({"name": label, "body": value, "sort_order": len(templates) * 10 + 10})
            continue
        key = KEYS.get(label) or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        if key in seen_keys:
            raise ValueError(f"Duplicate Quick-fill key: {key}")
        seen_keys.add(key)
        fields.append({
            "key": key, "group": group, "label": label, "value": value,
            "pinned": label in {"Full name", "Email"}, "sort_order": len(fields) * 10 + 10,
        })
    if not fields and not templates:
        raise ValueError("No filled application snippets found")
    return db.normalize_profile_data({
        "fields": fields, "answer_templates": templates, "documents": [], "stories": [],
    }), skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or import Super+J application answers")
    parser.add_argument("--apply-if-empty", action="store_true", help="Write only if the production Quick-fill profile is empty")
    args = parser.parse_args()
    try:
        profile, skipped = parse_snippets(sys.stdin.read())
        if args.apply_if_empty:
            dsn = os.environ.get("DATABASE_URL")
            if not dsn:
                raise ValueError("DATABASE_URL is required to apply the import")
            with db.connect(dsn) as conn:
                db.require_schema(conn)
                existing = db.profile_data(conn)
                if any(existing[name] for name in ("fields", "answer_templates", "documents", "stories")):
                    raise ValueError("Quick-fill is not empty; import cancelled to protect existing data")
                saved = db.replace_profile_data(conn, profile)
                if len(saved["fields"]) != len(profile["fields"]) or len(saved["answer_templates"]) != len(profile["answer_templates"]):
                    raise RuntimeError("Saved Quick-fill counts do not match the import")
        print(f"{'Imported' if args.apply_if_empty else 'Validated'} {len(profile['fields'])} fields and {len(profile['answer_templates'])} short answers.")
        if skipped:
            print("Skipped unfinished entries: " + ", ".join(skipped))
        return 0
    except (ValueError, RuntimeError) as error:
        print(f"Quick-fill import stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
