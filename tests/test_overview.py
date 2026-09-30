import io
import json

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
    assert all(item["kind"] == "responsibilities" for item in first)
    assert service.overview(sections) == first
    assert len(calls) == 1
    assert calls[0][0]["stream"] is False
    assert calls[0][1] == 30


def test_candidates_skip_short_or_repeated_lines():
    result = candidates([{"key": "role", "text": "Short\nBuild useful software with our team.\nBuild useful software with our team."}])
    assert result == [{"section": "role", "text": "Build useful software with our team."}]
