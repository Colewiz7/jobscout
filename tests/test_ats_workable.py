import json
import pathlib
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats.workable import ADAPTER

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"


def fixture(name):
    return (FIXTURES / name).read_text()


class FakeFetcher:
    """Answers send() from a handler, recording every request."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def send(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        status, body = self.handler(method, url, kwargs)
        return httpx.Response(status, content=body.encode(), request=httpx.Request(method, url))


def match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


def test_matches_posting_urls():
    ref = match("https://apply.workable.com/mindex/j/A836E7C2B9/")
    assert ref.provider == "workable"
    assert ref.board == "mindex"
    assert ref.job_id == "A836E7C2B9"
    assert ref.url == "https://apply.workable.com/mindex/j/A836E7C2B9/"
    assert ref.provider_id == "workable:mindex:A836E7C2B9"

    assert match("https://apply.workable.com/mindex/j/A836E7C2B9/apply/").provider_id == ref.provider_id
    assert match("https://apply.workable.com/Mindex/j/a836e7c2b9?utm_source=x").provider_id == ref.provider_id
    # the older subdomain form redirects to the same posting
    assert match("https://mindex.workable.com/j/A836E7C2B9").provider_id == ref.provider_id


@pytest.mark.parametrize(
    "url",
    [
        "https://apply.workable.com.evil.com/mindex/j/A836E7C2B9/",
        "https://evilapply.workable.com.example/mindex/j/A836E7C2B9/",
        "https://notworkable.com/mindex/j/A836E7C2B9/",
        "https://apply.workable.com/mindex/",
        "https://apply.workable.com/mindex/j/",
        "https://apply.workable.com/api/v2/accounts/mindex/jobs/A836E7C2B9",
        "https://www.workable.com/j/A836E7C2B9",
        "https://jobs.workable.com/view/abc123/data-engineer",
        "https://a.b.workable.com/j/A836E7C2B9",
    ],
)
def test_rejects_other_urls(url):
    assert match(url) is None


def test_detail_reads_the_posting():
    fetcher = FakeFetcher(lambda method, url, kw: (200, fixture("workable_detail.json")))
    ref = match("https://apply.workable.com/mindex/j/A836E7C2B9/")
    detail = ADAPTER.detail(fetcher, ref)

    method, url, _ = fetcher.calls[0]
    assert (method, url) == ("GET", "https://apply.workable.com/api/v2/accounts/mindex/jobs/A836E7C2B9")
    assert detail.ref == ref
    assert detail.company == "Mindex"
    assert detail.title == "Sr. OpenEdge Software Engineer - Remote"
    assert detail.location == "Rochester, New York, United States"
    assert detail.remote is True
    assert detail.employment_type == "FULL_TIME"
    assert detail.deadline is None
    assert isinstance(detail.age_days, int) and detail.age_days >= 0
    html = detail.description_html
    assert html.startswith("<p>Mindex is looking for a Senior OpenEdge")
    assert "<h2>Requirements</h2><ul><li>5+ years" in html
    assert "<h2>Benefits</h2>" in html


def test_detail_unknown_account_falls_back_to_the_slug():
    fetcher = FakeFetcher(lambda method, url, kw: (200, fixture("workable_detail.json")))
    ref = match("https://apply.workable.com/acme-robotics/j/A836E7C2B9/")
    assert ADAPTER.detail(fetcher, ref).company == "Acme Robotics"


def test_detail_closed_state_is_gone():
    payload = json.loads(fixture("workable_detail.json"))
    payload["state"] = "closed"
    fetcher = FakeFetcher(lambda method, url, kw: (200, json.dumps(payload)))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, match("https://apply.workable.com/mindex/j/A836E7C2B9/"))


def test_detail_404_is_gone():
    fetcher = FakeFetcher(lambda method, url, kw: (404, "Job not found"))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, match("https://apply.workable.com/mindex/j/0000000000/"))


def test_detail_403_is_unavailable():
    fetcher = FakeFetcher(lambda method, url, kw: (403, "blocked"))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match("https://apply.workable.com/mindex/j/A836E7C2B9/"))


def test_search_posts_one_us_query_per_term():
    fetcher = FakeFetcher(lambda method, url, kw: (200, fixture("workable_search.json")))
    postings = ADAPTER.search(fetcher, "mindex", "Mindex", ("devops", "cloud"))

    assert len(fetcher.calls) == 2
    for (method, url, kwargs), term in zip(fetcher.calls, ("devops", "cloud")):
        assert method == "POST"
        assert url == "https://apply.workable.com/api/v3/accounts/mindex/jobs"
        body = json.loads(kwargs["content"])
        assert body["query"] == term
        assert body["location"] == [{"country": "United States", "countryCode": "US"}]
        assert "token" not in body
        assert kwargs["headers"]["Content-Type"] == "application/json"

    assert [p.title for p in postings] == [
        "Data Engineer - Hybrid",
        ".NET Software Engineer - Hybrid",
        "Sr. OpenEdge Software Engineer - Remote",
    ]
    hybrid, _, remote = postings
    assert hybrid.source == "workable"
    assert hybrid.board == "mindex"
    assert hybrid.company == "Mindex"
    assert hybrid.location == "Rochester, New York, United States"
    assert hybrid.remote is False
    assert hybrid.url == f"https://apply.workable.com/mindex/j/{hybrid.provider_id.rsplit(':', 1)[1]}/"
    assert hybrid.provider_id.startswith("workable:mindex:")
    assert hybrid.employment_type == "FULL_TIME"
    assert remote.remote is True
    assert remote.provider_id == "workable:mindex:A836E7C2B9"


def test_search_follows_the_next_page_token():
    page = json.loads(fixture("workable_search.json"))
    first = {"results": page["results"][:2], "total": 3, "nextPage": "tok-2"}
    second = {"results": page["results"][2:], "total": 3}

    def handler(method, url, kwargs):
        body = json.loads(kwargs["content"])
        return 200, json.dumps(second if body.get("token") == "tok-2" else first)

    fetcher = FakeFetcher(handler)
    postings = ADAPTER.search(fetcher, "mindex", "Mindex", ("devops",))
    assert len(fetcher.calls) == 2
    assert len(postings) == 3


def test_search_returns_none_when_the_board_fails():
    fetcher = FakeFetcher(lambda method, url, kw: (404, "Not Found"))
    assert ADAPTER.search(fetcher, "nosuchaccount", "Nobody", ("devops",)) is None


def test_search_returns_none_on_a_bad_board_without_fetching():
    fetcher = FakeFetcher(lambda method, url, kw: (200, "{}"))
    assert ADAPTER.search(fetcher, "../evil", "Evil", ("devops",)) is None
    assert fetcher.calls == []
