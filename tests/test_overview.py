import io
import json

from jobscout.descriptions import parse_sections
from jobscout.overview import OverviewService, candidates


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_overview_only_returns_source_exact_excerpts_and_caches():
    calls = []
    sections = [{"key": "responsibilities", "text": (
        "Build reliable tooling for the platform team.\n"
        "Collaborate with engineers on service reliability.\n"
        "Write tests for infrastructure automation.\n"
        "Document operational practices for the team."
    )}]

    def opener(request, timeout):
        calls.append((json.loads(request.data), timeout))
        model_result = {"items": [
            {"id": 2, "kind": "role"},
            {"id": 0, "kind": "work"},
            {"id": 1, "kind": "requirements"},
            {"id": 999, "kind": "work"},
        ]}
        return Response(json.dumps({"response": json.dumps(model_result)}).encode())

    service = OverviewService("http://ollama.test", "test-model", opener=opener)
    first = service.overview(sections)
    assert [item["text"] for item in first] == [
        "Write tests for infrastructure automation.",
        "Build reliable tooling for the platform team.",
        "Collaborate with engineers on service reliability.",
    ]
    assert all(item["kind"] == "work" for item in first)
    assert service.overview(sections) == first
    assert service.cached_overview(sections) == first
    assert len(calls) == 1
    assert calls[0][0]["stream"] is False
    assert calls[0][1] == 30


def test_candidates_skip_short_or_repeated_lines():
    result = candidates([{"key": "role", "text": "Short\nBuild useful software with our team.\nBuild useful software with our team."}])
    assert result == [{"section": "role", "text": "Build useful software with our team."}]


def test_overview_keeps_real_work_ahead_of_logistics_and_never_invents_python():
    sections = parse_sections(
        "Job Description\n"
        "Cross functional collaboration with other teams to account for testing needs.\n"
        "Understanding of the traceability of requirements at the system and subsystem level.\n"
        "Provide more efficient solutions on our documentation organization.\n"
        "Qualifications/Requirements\n"
        "Pursuing a degree in System Engineering or Biomedical Engineering.\n"
        "Willingness to work in Salt Lake City\n"
        "Ability to work a 10-to-12-week full internship\n"
        "Relocation Assistance Provided: Yes"
    )
    def opener(request, timeout):
        del request, timeout
        return Response(json.dumps({"response": json.dumps({"items": [
            {"id": 4}, {"id": 5}, {"id": 6},
        ]})}).encode())

    items = OverviewService("http://ollama.test", "test-model", opener=opener).overview(sections)
    assert [item["kind"] for item in items[:3]] == ["work", "work", "work"]
    assert any(item["kind"] == "required" and "degree" in item["text"] for item in items)
    assert any(item["kind"] == "dates" and "10-to-12-week" in item["text"] for item in items)
    assert all("Python" not in item["text"] for item in items)


def test_overview_terms_are_short_and_reject_unsupported_model_claims():
    sections = [
        {"key": "responsibilities", "text": "Build reliable tooling for the platform team.\nCollaborate with engineers on service reliability."},
        {"key": "requirements", "text": "Python and leadership skills are required."},
    ]

    def opener(request, timeout):
        del request, timeout
        return Response(json.dumps({"response": json.dumps({"items": [
            {"id": 0, "terms": ["Build reliable tooling", "Rust"]},
            {"id": 2, "terms": ["Python", "leadership", "Kubernetes"]},
        ]})}).encode())

    items = OverviewService("http://ollama.test", "test-model", opener=opener).overview(sections)
    assert next(item for item in items if item["kind"] == "work")["terms"] == ["Build reliable tooling"]
    assert next(item for item in items if item["kind"] == "skills")["terms"] == ["leadership", "Python"]
    assert next(item for item in items if item["kind"] == "required")["terms"] == ["Python", "leadership"]
    assert all("Rust" not in item["terms"] and "Kubernetes" not in item["terms"] for item in items)


def test_unstructured_lever_posting_still_produces_actual_work():
    sections = parse_sections(
        "Please Note:\nApplicants must be authorized to work in the United States.\n"
        "As a Systems Engineering Intern, you will work alongside experienced engineers "
        "to contribute to the success of our projects. You will support the design and "
        "verification of real-world space systems, collaborating with electrical, mechanical, "
        "software, RF, and test teams. Tasks may include requirements development, "
        "interface definition, system-level analysis, integration planning, and test execution."
    )
    assert [section["key"] for section in sections] == ["logistics"]

    def opener(request, timeout):
        del request, timeout
        return Response(json.dumps({"response": '{"items":[]}'}).encode())

    items = OverviewService("http://ollama.test", "test-model", opener=opener).overview(sections)
    assert any(item["kind"] == "work" and "work alongside" in item["text"] for item in items)
    assert any(item["kind"] == "work" and "support the design" in item["text"] for item in items)
    assert any(item["kind"] == "required" and "authorized" in item["text"] for item in items)


def test_long_export_control_sentence_keeps_the_explicit_requirement():
    sentence = (
        "To conform with United States Government Space Technology Export Regulations, "
        "the applicant must be a U.S. citizen, lawful permanent resident of the U.S., "
        "conditional resident, asylee or refugee, or eligible to obtain the required "
        "authorizations from the U.S. Department of State, including any applicable "
        "export-control authorization before starting work in this program."
    )
    excerpts = candidates([{"key": "about", "text": sentence}])
    assert excerpts
    assert all(len(item["text"]) <= 320 for item in excerpts)
    assert any("must be a U.S. citizen" in item["text"] for item in excerpts)


def test_location_ignores_travel_and_generic_remote_benefit():
    sections = [
        {"key": "benefits", "text": "Our employees may find an opportunity for remote work through the Excellus Talent Acquisition team."},
        {"key": "requirements", "text": "Ability to travel across the Health Plan service area."},
        {"key": "logistics", "text": "This position is based in Rochester, New York."},
        {"key": "role", "text": "You will be required to work fully on-site."},
    ]

    def opener(request, timeout):
        del request, timeout
        return Response(json.dumps({"response": json.dumps({"items": [
            {"id": 2, "terms": ["Talent Acquisition team"]},
            {"id": 3, "terms": ["travel across"]},
        ]})}).encode())

    items = OverviewService("http://ollama.test", "test-model", opener=opener).overview(sections)
    location = [term for item in items if item["kind"] == "location" for term in item["terms"]]
    assert "based in Rochester, New York" in location
    assert "fully on-site" in location
    assert not any("remote" in term or "travel" in term or "Talent" in term for term in location)


def test_remote_access_and_hybrid_cloud_are_not_work_modes():
    sections = [{"key": "responsibilities", "text":
                 "Support remote access and hybrid cloud infrastructure for the engineering team."}]

    def opener(request, timeout):
        del request, timeout
        return Response(json.dumps({"response": '{"items":[]}'}).encode())

    items = OverviewService("http://ollama.test", "test-model", opener=opener).overview(sections)
    assert any(item["kind"] == "work" for item in items)
    assert not any(item["kind"] == "location" for item in items)


def test_caci_nike_and_audax_examples_surface_decision_facts():
    examples = [
        (
            "The Opportunity:\nThe internship will begin in May and last 12 weeks. "
            "You will be required to work fully on-site. Responsibilities:\n"
            "Implement and test software in IP networking equipment.\n"
            "Qualifications: Required:\nSoftware development skills in JavaScript, Python and C/C++.\n"
            "Desired:\nMinimum GPA of 3.0 is preferred.\n",
            {"work", "skills", "required", "preferred", "dates", "location"},
            {"Python", "JavaScript", "C/C++"},
        ),
        (
            "WHAT YOU WILL WORK ON\nInvent and evaluate future cushioning systems.\n"
            "WHO WE ARE LOOKING FOR\nExperience creating and executing Design of Experiments (DOE).\n"
            "This is a 10-week paid internship opportunity.\n",
            {"work", "skills", "required", "dates"},
            {"Design of Experiments", "DOE"},
        ),
        (
            "RESPONSIBILITIES:\nSupport desktops, laptops, and mobile devices.\n"
            "COMPETENCIES:\nWindows OS, macOS, Apple iOS, Outlook and Office 365.\n"
            "REQUIREMENTS/QUALIFICATIONS:\nCurrently enrolled in a BS/BA program.\n"
            "For New York: The hourly range is $28.00-$30.00.\n",
            {"work", "skills", "required", "pay"},
            {"Windows", "macOS", "iOS", "Outlook", "Office 365"},
        ),
    ]

    def opener(request, timeout):
        del request, timeout
        return Response(json.dumps({"response": '{"items":[]}'}).encode())

    for posting, expected_kinds, expected_skills in examples:
        items = OverviewService("http://ollama.test", "test-model", opener=opener).overview(parse_sections(posting))
        assert expected_kinds <= {item["kind"] for item in items}
        skills = next(item["terms"] for item in items if item["kind"] == "skills")
        assert expected_skills <= set(skills)
