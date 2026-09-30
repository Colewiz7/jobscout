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
