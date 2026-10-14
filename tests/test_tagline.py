import io
import json

import pytest

from jobscout.tagline import UNCLEAR, TaglineService, build_prompt, load_prompt, parse_tagline

GOOD = "🟢 High · Kubernetes and Argo CD, SRE work · Spring 2027, Jan to May · $33/hr · El Segundo, onsite"
JOB = {"company": "Acme", "title": "SRE Intern", "location": "El Segundo, CA", "terms": "Spring 2027", "pay": ""}
LONG_TEXT = "You will run Kubernetes clusters and Argo CD pipelines for the platform team. " * 4


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_parse_accepts_only_the_one_line_format():
    assert parse_tagline(GOOD) == GOOD
    assert parse_tagline(f"Here you go:\n`{GOOD}`\nHope that helps") == GOOD
    assert parse_tagline("Unclear, read posting.".rstrip(".")) == UNCLEAR
    assert parse_tagline("High fit, looks good") is None
    assert parse_tagline("🟢 no separators here") is None
    assert parse_tagline("🟢 High · " + "x" * 400) is None
    assert parse_tagline(None) is None


def test_parse_drops_pay_segments_that_say_nothing_is_listed():
    assert parse_tagline("🟢 High · SRE work · Summer 2027 · Pay not listed · Columbus, OH, hybrid") == (
        "🟢 High · SRE work · Summer 2027 · Columbus, OH, hybrid"
    )
    assert parse_tagline("🟡 Med · x · Spring 2027 · Salary not stated · NYC, onsite").count("Salary") == 0
    # Real pay and unrelated "not" phrases are kept.
    assert "$25/hr" in parse_tagline("🟡 Med · x · Spring 2027 · $25/hr · NYC, onsite")
    assert "Not a fit" in parse_tagline("🔴 Low · Not a fit for IT majors · Summer 2027 · NYC, onsite")


def test_prompt_carries_the_private_template_and_posting_facts():
    prompt = build_prompt("TEMPLATE", {**JOB, "pay": "$30/hr"}, "Desc text")
    assert prompt.startswith("TEMPLATE")
    assert "Company: Acme" in prompt and "Pay listed: $30/hr" in prompt and "Desc text" in prompt
    assert "Pay listed" not in build_prompt("T", JOB, "d")


def test_generate_calls_the_model_once_and_returns_the_line():
    calls = []

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        return Response(json.dumps({"response": GOOD + "\n"}).encode())

    service = TaglineService("http://ollama.test", "test-model", "TEMPLATE", opener=opener)
    assert service.generate(JOB, LONG_TEXT) == GOOD
    assert calls[0]["model"] == "test-model" and calls[0]["stream"] is False
    assert calls[0]["options"]["temperature"] == 0


def test_thin_description_is_unclear_without_asking_the_model():
    def opener(request, timeout):
        raise AssertionError("the model should not be called")

    service = TaglineService("http://ollama.test", "m", "T", opener=opener)
    assert service.generate(JOB, "Short.") == UNCLEAR


def test_unusable_model_output_is_an_error_so_the_attempt_is_retried():
    service = TaglineService(
        "http://ollama.test", "m", "T",
        opener=lambda request, timeout: Response(json.dumps({"response": "Sure! This looks great."}).encode()),
    )
    with pytest.raises(ValueError):
        service.generate(JOB, LONG_TEXT)


def test_changing_the_prompt_or_model_changes_the_hash():
    one = TaglineService("u", "m", "A").hash
    assert TaglineService("u", "m", "B").hash != one
    assert TaglineService("u", "other", "A").hash != one


def test_load_prompt_tolerates_missing_or_empty_files(tmp_path):
    assert load_prompt(None) is None
    assert load_prompt(str(tmp_path / "missing.md")) is None
    empty = tmp_path / "empty.md"
    empty.write_text("  \n")
    assert load_prompt(str(empty)) is None
    real = tmp_path / "prompt.md"
    real.write_text("  hello \n")
    assert load_prompt(str(real)) == "hello"
