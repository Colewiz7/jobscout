from jobscout.eligibility import analyze


def profile(**values):
    defaults = {
        "degree": "B.S. Computing",
        "graduation": "Expected 2028",
        "work_authorization": "Requires employment sponsorship",
        "clearance": "None",
        "gpa": "3.4",
        "location": "Pittsburgh, PA",
        "skills": "Python, Linux, Git",
    }
    defaults.update(values)
    return {
        "fields": [
            {"key": key, "value": value} for key, value in defaults.items()
        ],
        "stories": [],
        "highlights": [],
    }


def test_hard_blockers_are_explicit_and_profile_compared():
    text = (
        "Candidates must have a master's degree. We will not provide visa sponsorship. "
        "An active Secret security clearance is required. Candidates must graduate in 2027."
    )
    result = analyze(text=text, sections=[], profile=profile())
    assert result["verdict"] == "do_not_apply"
    assert {item["key"] for item in result["blockers"]} == {
        "sponsorship", "clearance", "degree", "graduation",
    }
    assert all(item["provenance"] == "explicit wording" for item in result["blockers"])


def test_unknown_profile_values_prompt_review_without_hard_verdict():
    result = analyze(
        text="Applicants must be authorized without sponsorship. Active clearance is required.",
        sections=[],
        profile=profile(work_authorization="", clearance=""),
    )
    assert result["verdict"] == "review"
    assert result["blockers"] == []
    assert {item["key"] for item in result["warnings"]} == {
        "sponsorship-unknown", "clearance-unknown",
    }


def test_requirement_provenance_and_estimate_guardrail():
    result = analyze(
        text="",
        sections=[
            {"key": "requirements", "text": "Python is required\nLinux"},
            {"key": "responsibilities", "text": "Work with Kubernetes"},
        ],
        profile=profile(skills="Python, Linux, Kubernetes"),
    )
    assert [(item["skill"], item["provenance"]) for item in result["requirements"]] == [
        ("python", "explicit wording"),
        ("linux", "posting structure"),
        ("kubernetes", "estimate"),
    ]
    assert result["match_band"] == "Strong direct evidence"

    estimated_only = analyze(
        text="",
        sections=[{"key": "responsibilities", "text": "Work with Kubernetes"}],
        profile=profile(skills="Kubernetes"),
    )
    assert estimated_only["match_band"] == "Some direct evidence"


def test_override_removes_active_verdict_but_keeps_finding():
    evidence = "We will not provide visa sponsorship."
    result = analyze(
        text=evidence,
        sections=[],
        profile=profile(),
        overrides=[{"blocker_key": "sponsorship", "evidence": evidence}],
    )
    assert result["verdict"] == "clear"
    assert result["overridden"] is True
    assert result["blockers"][0]["overridden"] is True
