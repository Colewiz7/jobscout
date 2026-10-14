import io
import json

import pytest

from jobscout import tagline
from jobscout.tagline import (
    TERM_UNCLEAR, UNCLEAR, TaglineService, build_prompt, build_tagline, check_flags,
    load_prompt, process, prompt_hash, settle_fit,
)

TEMPLATE = """You write taglines.

CANDIDATE
- A junior in IT

RULES
- Judge from the description.

OUTPUT
Exactly one line.

EXAMPLES
🟡 Med · HR analytics despite the ERP title · Summer 2027 · Rochester, onsite
"""
DESCRIPTION = (
    "You will run Kubernetes clusters and Argo CD pipelines for the platform team. "
    "Must be pursuing a degree in Electrical or Mechanical Engineering. "
    "Active Secret clearance required at the start of the internship. "
    "Applicants must be able to obtain a security clearance before the start date. "
    "Degrees in Computer Science or a related field are welcome. "
) * 2
JOB = {"company": "Acme", "title": "SRE Intern", "location": "El Segundo, CA", "terms": "Spring 2027", "pay": ""}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def facts(**changes):
    base = {"fit": "High", "work": "Kubernetes and Argo CD SRE work", "flags": [], "term": "Spring 2027, Jan to May",
            "pay": "$33/hr + housing", "location": "El Segundo, onsite"}
    return {**base, **changes}


def flag(kind, evidence, text="a problem"):
    return {"kind": kind, "text": text, "evidence": evidence}


def test_line_is_built_in_code_in_the_documented_order():
    assert build_tagline(facts(), DESCRIPTION) == (
        "🟢 High · Kubernetes and Argo CD SRE work · Spring 2027, Jan to May · $33/hr + housing · El Segundo, onsite"
    )


def test_missing_term_falls_back_to_the_listed_term_then_says_so():
    assert "Spring 2027" in build_tagline(facts(term=""), DESCRIPTION, "Spring 2027")
    assert f" · {TERM_UNCLEAR} · " in build_tagline(facts(term="unknown"), DESCRIPTION)


def test_missing_pay_and_long_text_are_handled_without_stray_separators():
    line = build_tagline(facts(pay="Pay not listed", work="one two three four five six seven eight nine ten eleven twelve"), DESCRIPTION)
    assert "Pay" not in line and "eleven" not in line
    assert "  " not in line and " ·  · " not in line
    assert build_tagline(facts(location="Albany, NY; Buffalo, NY; Rochester, NY; Utica, NY"), DESCRIPTION).endswith(
        "Albany, NY, Buffalo, NY +2 more")
    assert build_tagline(facts(fit="Unclear"), DESCRIPTION) == UNCLEAR
    with pytest.raises(ValueError):
        build_tagline(facts(fit="Great"), DESCRIPTION)
    with pytest.raises(ValueError):
        build_tagline(facts(work=""), DESCRIPTION)


def test_flags_need_a_quote_that_is_in_the_posting():
    made_up = flag("grad_only", "PhD students only need apply")
    real = flag("major_excludes_it", "Must be pursuing a degree in Electrical or Mechanical Engineering")
    assert check_flags([made_up], DESCRIPTION) == []
    assert [item["kind"] for item in check_flags([real], DESCRIPTION)] == ["major_excludes_it"]
    assert check_flags([flag("grad_only", "")], DESCRIPTION) == []


def test_major_list_with_related_field_wording_is_not_a_flag():
    description = "Must be pursuing a degree in Electrical Engineering, Mechanical Engineering, or a related field."
    assert check_flags([flag("major_excludes_it", "pursuing a degree in Electrical Engineering")], description) == []


def test_clearance_only_counts_when_it_is_active_at_the_start():
    active = flag("clearance_active", "Active Secret clearance required at the start of the internship")
    obtainable = flag("clearance_active", "able to obtain a security clearance before the start date")
    assert [item["kind"] for item in check_flags([active], DESCRIPTION)] == ["clearance_active"]
    assert check_flags([obtainable], DESCRIPTION) == []


def test_a_quote_under_a_preferred_heading_is_a_wish_not_a_requirement():
    description = "Requirements: Python. How You Can Stand Out: Active security clearance and a GPA of 3.5."
    assert check_flags([flag("clearance_active", "Active security clearance")], description) == []


def test_grad_only_is_dropped_when_bachelors_students_are_welcome():
    description = "Open to students pursuing a Bachelor's or Master's degree in any technical field."
    assert check_flags([flag("grad_only", "pursuing a Bachelor's or Master's degree")], description) == []
    assert check_flags([flag("grad_only", "Open to PhD candidates only")], "Open to PhD candidates only.") != []


def test_coding_interview_needs_real_interview_wording():
    skills = "Experience with C, C++, and Python is expected for this role."
    assert check_flags([flag("coding_interview", "Experience with C, C++, and Python")], skills) == []
    heavy = "Candidates complete a LeetCode style coding challenge in the technical screen."
    assert check_flags([flag("coding_interview", "complete a LeetCode style coding challenge")], heavy) != []


def test_hard_flags_force_low_and_soft_flags_cap_at_med():
    hard = [{"kind": "clearance_active", "tier": "hard"}]
    soft = [{"kind": "coding_interview", "tier": "soft"}]
    assert settle_fit("High", hard) == "Low" and settle_fit("Med", hard) == "Low"
    assert settle_fit("High", soft) == "Med" and settle_fit("Low", soft) == "Low"
    assert settle_fit("High", []) == "High"


def test_end_to_end_line_uses_only_verified_flags():
    description = "Active security clearance required at start. " + "Work on Linux platform tooling. " * 8
    payload = facts(fit="High", flags=[
        flag("clearance_active", "Active security clearance required at start", "requires active clearance"),
        flag("grad_only", "invented quote that is nowhere in the text", "PhD only"),
    ])
    service = TaglineService(
        "http://ollama.test", "m", TEMPLATE,
        opener=lambda request, timeout: Response(json.dumps({"response": json.dumps(payload)}).encode()),
    )
    line = service.generate(JOB, description)
    assert line.startswith("🔴 Low · ") and "⚠ requires active clearance" in line and "PhD" not in line


def test_prompt_keeps_the_private_rules_and_examples_but_owns_the_output_format():
    prompt = build_prompt(TEMPLATE, {**JOB, "pay": "$30/hr"}, "Desc text")
    assert "A junior in IT" in prompt and "Judge from the description." in prompt
    assert "Exactly one line." not in prompt
    assert "CALIBRATION EXAMPLES" in prompt and "HR analytics despite the ERP title" in prompt
    assert "Reply with one JSON object" in prompt
    assert "Company: Acme" in prompt and "Pay listed: $30/hr" in prompt and prompt.rstrip().endswith("Desc text")
    assert "Pay listed" not in build_prompt(TEMPLATE, JOB, "d")
    assert "CALIBRATION" not in build_prompt("RULES only", JOB, "d")


def test_generate_asks_for_structured_output_once():
    calls = []

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        return Response(json.dumps({"response": json.dumps(facts())}).encode())

    service = TaglineService("http://ollama.test", "test-model", TEMPLATE, opener=opener)
    assert service.generate(JOB, DESCRIPTION).startswith("🟢 High")
    assert len(calls) == 1 and calls[0]["model"] == "test-model"
    assert calls[0]["format"]["required"] and calls[0]["options"]["temperature"] == 0


def test_thin_description_is_unclear_without_asking_the_model():
    def opener(request, timeout):
        raise AssertionError("the model should not be called")

    assert TaglineService("http://ollama.test", "m", "T", opener=opener).generate(JOB, "Short.") == UNCLEAR


@pytest.mark.parametrize("reply", ["not json", "[1, 2]", json.dumps({"fit": "High"})])
def test_unusable_model_output_is_an_error_so_the_attempt_is_retried(reply):
    service = TaglineService(
        "http://ollama.test", "m", "T",
        opener=lambda request, timeout: Response(json.dumps({"response": reply}).encode()),
    )
    with pytest.raises(ValueError):
        service.generate(JOB, DESCRIPTION)


def test_changing_the_prompt_model_or_format_version_changes_the_hash(monkeypatch):
    one = prompt_hash("A", "m")
    assert prompt_hash("B", "m") != one and prompt_hash("A", "other") != one
    monkeypatch.setattr(tagline, "FORMAT_VERSION", "v-next")
    assert prompt_hash("A", "m") != one


def test_load_prompt_tolerates_missing_or_empty_files(tmp_path):
    assert load_prompt(None) is None
    assert load_prompt(str(tmp_path / "missing.md")) is None
    empty = tmp_path / "empty.md"
    empty.write_text("  \n")
    assert load_prompt(str(empty)) is None
    real = tmp_path / "prompt.md"
    real.write_text("  hello \n")
    assert load_prompt(str(real)) == "hello"


def test_graduation_windows_are_not_a_term_flag():
    window = "Candidates must have an expected graduation date between December 2027 and August 2028."
    assert check_flags([flag("term_outside", "expected graduation date between December 2027 and August 2028")], window) == []
    dates = "This internship runs from September through December during the Fall 2027 term."
    assert [item["kind"] for item in check_flags([flag("term_outside", "runs from September through December during the Fall 2027 term")], dates)] == ["term_outside"]
