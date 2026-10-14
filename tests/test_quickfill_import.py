import json
from pathlib import Path

import pytest

from jobscout.quickfill_import import GROUPS, parse_snippets


def test_snip_answers_map_to_copy_groups_without_guessing_values():
    profile, skipped = parse_snippets("""
# Saved answers
Full name :: Test Applicant
Email :: test@example.invalid
Degree (full) :: B.S. Engineering
Salary (hourly) :: $20/hr
Job description :: Built and maintained internal tools.
Summary :: I build useful systems.
Why this company :: TODO customize for each company
""")
    assert [(row["key"], row["group"]) for row in profile["fields"]] == [
        ("name", "Identity"), ("email", "Contact"), ("degree", "Education"),
        ("salary_hourly", "Compensation"), ("job_description", "Experience"),
    ]
    assert profile["fields"][3]["value"] == "$20/hr"
    assert profile["answer_templates"] == [
        {"id": "template-1", "name": "Summary", "body": "I build useful systems.", "sort_order": 10}
    ]
    assert skipped == ["Why this company"]


def test_snip_import_rejects_unmapped_or_duplicate_labels():
    with pytest.raises(ValueError, match="Unmapped"):
        parse_snippets("Password :: should-not-import")
    with pytest.raises(ValueError, match="duplicated"):
        parse_snippets("Email :: one\nEmail :: two")


def test_ats_ordering_shows_every_imported_group():
    ordering_path = Path(__file__).resolve().parents[1] / "src/jobscout/static/ats-ordering.json"
    ordering = json.loads(ordering_path.read_text())
    for ats, groups in ordering.items():
        if ats.startswith("_"):
            continue
        assert GROUPS.keys() <= set(groups)


def test_new_availability_pay_and_essay_labels_are_mapped():
    profile, skipped = parse_snippets("""
Start date (Spring) :: 01/04/2027
Salary (hourly, big city) :: 35
Co-op or internship :: Co-op through school
CPT / F-1 visa :: No
Exceptional ability 1 :: Standardized device imaging.
Interview availability :: TODO weekday windows
""")
    groups = {row["label"]: row["group"] for row in profile["fields"]}
    assert groups == {
        "Start date (Spring)": "Availability", "Salary (hourly, big city)": "Compensation",
        "Co-op or internship": "Education", "CPT / F-1 visa": "Work authorization",
    }
    assert [row["name"] for row in profile["answer_templates"]] == ["Exceptional ability 1"]
    assert skipped == ["Interview availability"]
