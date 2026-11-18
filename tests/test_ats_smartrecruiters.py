import json
import pathlib
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats.smartrecruiters import ADAPTER

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
        status, body = self.handler(method, url)
        return httpx.Response(status, content=body.encode(), request=httpx.Request(method, url))


def match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


def test_matches_posting_urls():
    ref = match("https://jobs.smartrecruiters.com/LLNL/3743990015408206-computing-undergraduate-student-intern")
    assert ref.provider == "smartrecruiters"
    assert ref.board == "LLNL"
    assert ref.job_id == "3743990015408206"
    assert ref.url == "https://jobs.smartrecruiters.com/LLNL/3743990015408206"
    assert ref.provider_id == "smartrecruiters:LLNL:3743990015408206"

    bare = match("https://jobs.smartrecruiters.com/WellmarkInc/744000150385195")
    assert (bare.board, bare.job_id) == ("WellmarkInc", "744000150385195")


def test_matches_publication_uuid_urls():
    uuid = "1547c6f7-b7d4-4ed0-aae1-279864054546"
    for url in (
        f"https://jobs.smartrecruiters.com/ni/LLNL/{uuid}-computing-undergraduate",
        f"https://jobs.smartrecruiters.com/oneclick-ui/company/LLNL/publication/{uuid}?dcr_ci=LLNL",
    ):
        ref = match(url)
        assert (ref.board, ref.job_id) == ("LLNL", uuid)
        assert ref.url == f"https://jobs.smartrecruiters.com/ni/LLNL/{uuid}"


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.smartrecruiters.com.evil.com/LLNL/3743990015408206",
        "https://evil-jobs.smartrecruiters.com.example/LLNL/3743990015408206",
        "https://smartrecruiters.com/LLNL/3743990015408206",
        "https://careers.smartrecruiters.com/WellmarkInc",
        "https://jobs.smartrecruiters.com/LLNL",
        "https://jobs.smartrecruiters.com/LLNL/not-a-posting",
        "https://api.smartrecruiters.com/v1/companies/LLNL/postings/3743990015408206",
    ],
)
def test_rejects_other_urls(url):
    assert match(url) is None


def test_detail_reads_the_posting():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("smartrecruiters_detail.json")))
    ref = match("https://jobs.smartrecruiters.com/LLNL/3743990015408206-computing")
    detail = ADAPTER.detail(fetcher, ref)

    assert fetcher.calls[0][1] == "https://api.smartrecruiters.com/v1/companies/LLNL/postings/3743990015408206"
    assert detail.ref == ref
    assert detail.company == "Lawrence Livermore National Laboratory"
    assert detail.title == "Computing Undergraduate Student Intern: DevOps Internship Program - Summer 2027"
    assert detail.location == "Livermore, CA, United States"
    assert detail.remote is False
    assert detail.employment_type == "INTERN"
    assert detail.deadline == "2027-01-16T08:00:00+00:00"
    assert isinstance(detail.age_days, int) and detail.age_days >= 0
    html = detail.description_html
    assert html.index("<h2>Company Description</h2>") < html.index("<h2>Job Description</h2>")
    assert "Positions will close to applicants on January 15, 2027" in html
    assert "<h2>Qualifications</h2>" in html and "<h2>Additional Information</h2>" in html
    assert detail.posting().provider_id == "smartrecruiters:LLNL:3743990015408206"


def test_detail_canonicalises_a_uuid_ref():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("smartrecruiters_detail.json")))
    ref = match("https://jobs.smartrecruiters.com/ni/llnl/1547c6f7-b7d4-4ed0-aae1-279864054546")
    detail = ADAPTER.detail(fetcher, ref)
    assert detail.ref.provider_id == "smartrecruiters:LLNL:3743990015408206"
    assert detail.ref.url == "https://jobs.smartrecruiters.com/LLNL/3743990015408206"


def test_detail_inactive_posting_is_gone():
    payload = json.loads(fixture("smartrecruiters_detail.json"))
    payload["active"] = False
    fetcher = FakeFetcher(lambda method, url: (200, json.dumps(payload)))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, match("https://jobs.smartrecruiters.com/LLNL/3743990015408206"))


def test_detail_404_is_gone():
    body = '{"httpCode":404,"code":"RESOURCE_NOT_FOUND","message":"Resource not found"}'
    fetcher = FakeFetcher(lambda method, url: (404, body))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, match("https://jobs.smartrecruiters.com/LLNL/3743990000000001"))


def test_detail_403_is_unavailable():
    fetcher = FakeFetcher(lambda method, url: (403, "blocked"))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match("https://jobs.smartrecruiters.com/LLNL/3743990015408206"))


def test_search_queries_each_term_in_the_us():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("smartrecruiters_search.json")))
    postings = ADAPTER.search(fetcher, "WellmarkInc", "Wellmark", ("cloud", "platform"))

    assert len(fetcher.calls) == 2
    for call, term in zip(fetcher.calls, ("cloud", "platform")):
        method, url, _ = call
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert method == "GET"
        assert url.startswith("https://api.smartrecruiters.com/v1/companies/WellmarkInc/postings?")
        assert query["q"] == [term]
        assert query["country"] == ["us"]

    # the same three postings came back for both terms
    assert len(postings) == 3
    first = postings[0]
    assert first.source == "smartrecruiters"
    assert first.board == "WellmarkInc"
    assert first.company == "Wellmark"
    assert first.title == "Software Engineer Internship – AI Platform Team"
    assert first.location == "Des Moines, IA, United States"
    assert first.url == "https://jobs.smartrecruiters.com/WellmarkInc/744000159260670"
    assert first.provider_id == "smartrecruiters:WellmarkInc:744000159260670"
    assert first.employment_type == "INTERN"
    assert isinstance(first.age_days, int)


def test_search_returns_none_when_the_board_fails():
    fetcher = FakeFetcher(lambda method, url: (503, "down"))
    assert ADAPTER.search(fetcher, "WellmarkInc", "Wellmark", ("cloud",)) is None


def test_search_returns_none_when_a_later_term_fails():
    answers = iter([(200, fixture("smartrecruiters_search.json")), (403, "blocked")])
    fetcher = FakeFetcher(lambda method, url: next(answers))
    assert ADAPTER.search(fetcher, "WellmarkInc", "Wellmark", ("cloud", "devops")) is None


def test_search_with_no_terms_did_not_answer():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("smartrecruiters_search.json")))
    assert ADAPTER.search(fetcher, "WellmarkInc", "Wellmark", ()) is None
    assert fetcher.calls == []
