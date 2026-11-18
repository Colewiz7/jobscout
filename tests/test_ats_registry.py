"""The adapter registry as capture, descriptions, liveness and the scout see it."""
import urllib.parse

import httpx
import pytest

from jobscout import ats
from jobscout.capture import PostingCapture, PostingGone
from jobscout.descriptions import DescriptionAccessBlocked, ProviderDescriptionFetcher
from jobscout.liveness import PostingLivenessChecker
from jobscout.sources import boards


class FakeAdapter:
    name = "fakeats"

    def __init__(self, outcome="live"):
        self.outcome = outcome
        self.searched = []

    def match(self, parsed):
        if parsed.hostname != "jobs.fakeats.example":
            return None
        job_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        return ats.Ref("fakeats", "acme", job_id, f"https://jobs.fakeats.example/acme/{job_id}")

    def detail(self, fetcher, ref):
        if self.outcome == "gone":
            raise ats.Gone("404")
        if self.outcome == "blocked":
            raise ats.Unavailable("403", 403)
        if self.outcome == "broken":
            raise ats.Unavailable("unexpected shape")
        return ats.Detail(
            ref, company="Acme", title="DevOps Co-op", location="Rochester, NY; Remote",
            description_html="<h2>The role</h2><p>" + "Keep the lights on. " * 10 + "</p>",
            deadline="2027-01-15",
        )

    def search(self, fetcher, board, company, terms):
        self.searched.append((board, company, tuple(terms)))
        return [self.detail(fetcher, ats.Ref("fakeats", board, "7", "https://jobs.fakeats.example/acme/7")).posting()]


@pytest.fixture
def adapter(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(ats, "_adapters", [fake])
    return fake


URL = "https://jobs.fakeats.example/acme/42"


def test_match_only_claims_owned_hosts(adapter):
    assert ats.match(URL)[1].provider_id == "fakeats:acme:42"
    for other in ("https://jobs.fakeats.example.evil.com/acme/42", "ftp://jobs.fakeats.example/acme/42", "not a url", ""):
        assert ats.match(other) is None


def test_capture_reads_an_adapter_posting(adapter):
    captured = PostingCapture(fetcher=None).capture({"url": URL})
    posting = captured.posting
    assert (posting.source, posting.board, posting.company, posting.title) == ("fakeats", "acme", "Acme", "DevOps Co-op")
    assert posting.dedupe_key == "fakeats:acme:42"
    assert posting.remote is True
    assert captured.description is not None and str(captured.description.deadline) == "2027-01-15"


def test_capture_says_a_gone_posting_is_gone_unless_fields_were_typed(adapter):
    adapter.outcome = "gone"
    with pytest.raises(PostingGone):
        PostingCapture(fetcher=None).capture({"url": URL})
    typed = PostingCapture(fetcher=None).capture({"url": URL, "company": "Acme", "title": "DevOps Co-op"})
    assert typed.posting.source == "manual"


def test_capture_falls_back_to_typed_fields_when_the_provider_is_unreadable(adapter):
    adapter.outcome = "broken"
    with pytest.raises(ValueError):
        PostingCapture(fetcher=None).capture({"url": URL})
    assert PostingCapture(fetcher=None).capture({"url": URL, "company": "Acme", "title": "X"}).posting.source == "manual"


def test_descriptions_come_from_the_adapter(adapter):
    parsed = ProviderDescriptionFetcher(fetcher=None, min_interval=0).fetch({"dedupe_key": "fakeats:acme:42", "url": URL})
    assert parsed is not None and "Keep the lights on" in parsed.text


def test_a_blocked_adapter_description_says_so(adapter):
    adapter.outcome = "blocked"
    with pytest.raises(DescriptionAccessBlocked):
        ProviderDescriptionFetcher(fetcher=None, min_interval=0).fetch({"dedupe_key": "x", "url": URL})
    adapter.outcome = "gone"
    assert ProviderDescriptionFetcher(fetcher=None, min_interval=0).fetch({"dedupe_key": "x", "url": URL}) is None


@pytest.mark.parametrize("outcome,status", [("live", "live"), ("gone", "closed"), ("blocked", "unknown"), ("broken", "unknown")])
def test_liveness_maps_the_adapter_answer(adapter, outcome, status):
    adapter.outcome = outcome
    assert PostingLivenessChecker(fetcher=None, min_interval=0).check({"dedupe_key": "x", "url": URL}).status == status


def test_the_scout_finds_adapter_boards(adapter):
    postings, fetched = boards.fetch(None, {"fakeats": ("acme",)}, {"fakeats": {"acme": "Acme"}}, ("devops",))
    assert fetched == {("fakeats", "acme")}
    assert [p.provider_id for p in postings] == ["fakeats:acme:7"]
    assert adapter.searched == [("acme", "Acme", ("devops",))]


def test_fetch_helpers_map_status_codes():
    class Fetcher:
        def __init__(self, status, body=b"{}"):
            self.status, self.body = status, body

        def send(self, method, url, **kwargs):
            return httpx.Response(self.status, content=self.body, request=httpx.Request(method, url))

    assert ats.fetch_json(Fetcher(200, b'{"a": 1}'), "https://x.example/") == {"a": 1}
    with pytest.raises(ats.Gone):
        ats.fetch_json(Fetcher(404), "https://x.example/")
    with pytest.raises(ats.Gone):
        ats.fetch_text(Fetcher(410), "https://x.example/")
    with pytest.raises(ats.Unavailable) as blocked:
        ats.fetch_json(Fetcher(403), "https://x.example/")
    assert blocked.value.status == 403
    with pytest.raises(ats.Unavailable):
        ats.fetch_json(Fetcher(200, b"<html>"), "https://x.example/")


def test_workday_capture_accepts_a_locale_prefix():
    class Fetcher:
        def get_json(self, url):
            self.url = url
            return {"jobPostingInfo": {"title": "IT Intern", "location": "Madison, WI"}}

    fetcher = Fetcher()
    captured = PostingCapture(fetcher).capture({"url": "https://amfam.wd1.myworkdayjobs.com/en-US/Careers/job/WI-Madison/IT-Intern_R39462"})
    assert fetcher.url == "https://amfam.wd1.myworkdayjobs.com/wday/cxs/amfam/Careers/job/WI-Madison/IT-Intern_R39462"
    assert captured.posting.board == "amfam/wd1/Careers"
