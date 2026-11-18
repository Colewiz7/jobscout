"""Eightfold PCSX adapter, against trimmed real responses."""
import json
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats import eightfold
from jobscout.ats.eightfold import ADAPTER


class FakeFetcher:
    """Answers by URL fragment, first match wins; anything else is a 404."""

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def send(self, method, url, **kw):
        self.asked.append(url)
        for fragment, status, body in self.routes:
            if fragment in url:
                if callable(body):
                    body = body(url)
                content = body if isinstance(body, bytes) else json.dumps(body).encode()
                return httpx.Response(status, content=content, request=httpx.Request(method, url))
        return httpx.Response(404, content=b"{}", request=httpx.Request(method, url))


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(eightfold, "PAUSE", 0)


def _match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


# --- match -----------------------------------------------------------------

@pytest.mark.parametrize("url, board, job_id", [
    ("https://jobs.nvidia.com/careers/job/893397724522", "jobs.nvidia.com/nvidia.com", "893397724522"),
    ("https://jobs.nvidia.com/careers/job/893397724522?hl=en&utm_source=x", "jobs.nvidia.com/nvidia.com", "893397724522"),
    ("https://apply.careers.microsoft.com/careers/job/1970393557007828", "apply.careers.microsoft.com/microsoft.com", "1970393557007828"),
    ("https://careers.deere.com/careers/job/137483990265", "careers.deere.com/johndeere.com", "137483990265"),
    ("https://careers.qualcomm.com/careers/job/446721109667", "careers.qualcomm.com/qualcomm.com", "446721109667"),
    # The eightfold.ai alias of a known tenant files under the company host.
    ("https://johndeere.eightfold.ai/careers/job/137483990265?domain=johndeere.com", "careers.deere.com/johndeere.com", "137483990265"),
    ("https://nvidia.eightfold.ai/careers?pid=893397724522&domain=nvidia.com", "jobs.nvidia.com/nvidia.com", "893397724522"),
    ("https://NVIDIA.eightfold.ai./careers/job?pid=893397724522", "jobs.nvidia.com/nvidia.com", "893397724522"),
    # An unknown tenant keeps its own host and states its domain.
    ("https://acme.eightfold.ai/careers/job/42?domain=acme.co.uk", "acme.eightfold.ai/acme.co.uk", "42"),
    ("https://acme.eightfold.ai/careers/job/42", "acme.eightfold.ai/acme.com", "42"),
    ("https://app.eightfold.ai/careers?pid=7&domain=acme.com", "app.eightfold.ai/acme.com", "7"),
])
def test_match_positive(url, board, job_id):
    ref = _match(url)
    assert ref is not None
    assert ref.provider == "eightfold"
    assert ref.board == board
    assert ref.job_id == job_id
    host = board.split("/")[0]
    assert ref.url == f"https://{host}/careers/job/{job_id}"


def test_alias_and_company_host_share_a_dedupe_key():
    a = _match("https://jobs.nvidia.com/careers/job/893397724522")
    b = _match("https://nvidia.eightfold.ai/careers?pid=893397724522&domain=nvidia.com")
    assert a.provider_id == b.provider_id == "eightfold:nvidia.com:893397724522"


@pytest.mark.parametrize("url", [
    "https://eightfold.ai.evil.com/careers/job/1",
    "https://nvidia.eightfold.ai.evil.com/careers/job/1?domain=nvidia.com",
    "https://evil-eightfold.ai/careers/job/1",
    "https://eightfold.ai/careers/job/1",
    "https://a.b.eightfold.ai/careers/job/1",
    "https://jobs.nvidia.com.evil.com/careers/job/1",
    "https://evil.com/jobs.nvidia.com/careers/job/1",
    "https://jobs.nvidia.com@evil.com/careers/job/1",
    "https://notjobs.nvidia.com/careers/job/1",
    "https://jobs.nvidia.com/careers",
    "https://jobs.nvidia.com/careers?pid=abc",
    "https://jobs.nvidia.com/about/job/1",
    "https://app.eightfold.ai/careers/job/1",
    # Old Microsoft links redirect to search, not the posting.
    "https://jobs.careers.microsoft.com/global/en/job/200031762/Site-Reliability",
    "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA/X_JR2025882",
])
def test_match_negative(url):
    assert _match(url) is None


# --- detail ----------------------------------------------------------------

def test_detail_nvidia(fixture_json):
    ref = _match("https://jobs.nvidia.com/careers/job/893397724522")
    fetcher = FakeFetcher([("/api/pcsx/position_details", 200, fixture_json("ats/eightfold_nvidia_detail.json"))])
    detail = ADAPTER.detail(fetcher, ref)

    asked = _query(fetcher.asked[0])
    assert fetcher.asked[0].startswith("https://jobs.nvidia.com/api/pcsx/position_details?")
    assert asked["position_id"] == "893397724522" and asked["domain"] == "nvidia.com"
    assert detail.company == "NVIDIA"
    assert detail.title == "Software DevOps Engineer, Networking"
    assert detail.location == "US, CA, Santa Clara"
    assert detail.remote is False
    assert detail.employment_type == "FULL_TIME"
    assert detail.deadline is None
    assert isinstance(detail.age_days, int) and detail.age_days >= 0
    assert "NVLink" in detail.description_html
    posting = detail.posting()
    assert posting.source == "eightfold" and posting.provider_id == "eightfold:nvidia.com:893397724522"


def test_detail_microsoft_employment_type(fixture_json):
    ref = _match("https://apply.careers.microsoft.com/careers/job/1970393557007828")
    fetcher = FakeFetcher([("position_details", 200, fixture_json("ats/eightfold_microsoft_detail.json"))])
    detail = ADAPTER.detail(fetcher, ref)
    assert detail.company == "Microsoft"
    assert detail.title == "Site Reliability Engineer - CTJ - Secret"
    assert detail.location == "United States, Virginia, Reston"
    assert detail.employment_type == "FULL_TIME"
    assert detail.description_html.startswith("<b>Overview</b>")


def test_detail_unknown_tenant_falls_back_to_domain_name(fixture_json):
    payload = fixture_json("ats/eightfold_nvidia_detail.json")
    payload["data"]["id"] = 42
    ref = _match("https://acme.eightfold.ai/careers/job/42?domain=acme.com")
    detail = ADAPTER.detail(FakeFetcher([("position_details", 200, payload)]), ref)
    assert detail.company == "Acme"


def test_detail_gone_on_404():
    ref = _match("https://jobs.nvidia.com/careers/job/893300000001")
    body = {"status": 404, "error": {"message": "Position not found"}, "data": {}, "metadata": None}
    with pytest.raises(Gone):
        ADAPTER.detail(FakeFetcher([("position_details", 404, body)]), ref)


def test_detail_gone_when_body_says_404():
    ref = _match("https://jobs.nvidia.com/careers/job/893300000001")
    body = {"status": 404, "error": {"message": "Position not found"}, "data": {}}
    with pytest.raises(Gone):
        ADAPTER.detail(FakeFetcher([("position_details", 200, body)]), ref)


def test_detail_unavailable_on_403():
    ref = _match("https://jobs.nvidia.com/careers/job/893397724522")
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher([("position_details", 403, {"message": "Forbidden"})]), ref)


def test_detail_unavailable_on_html_error_page():
    # A domain the host does not serve gets the site's HTML back, not JSON.
    ref = _match("https://acme.eightfold.ai/careers/job/42")
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher([("position_details", 200, b"<!DOCTYPE html><html></html>")]), ref)


def test_detail_unavailable_on_empty_data_without_status():
    ref = _match("https://jobs.nvidia.com/careers/job/893397724522")
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher([("position_details", 200, {"data": {}})]), ref)


# --- search ----------------------------------------------------------------

def test_search_parses_and_dedupes_across_terms(fixture_json):
    payload = fixture_json("ats/eightfold_nvidia_search.json")
    fetcher = FakeFetcher([("/api/pcsx/search", 200, payload)])
    rows = ADAPTER.search(fetcher, "jobs.nvidia.com/nvidia.com", "NVIDIA", ("devops", "site reliability"))

    # count is 3, so one request per term, and the overlap collapses
    assert len(fetcher.asked) == 2
    first = _query(fetcher.asked[0])
    assert first == {"domain": "nvidia.com", "query": "devops", "location": "United States", "start": "0"}
    assert _query(fetcher.asked[1])["query"] == "site reliability"

    assert len(rows) == 3
    by_id = {r.provider_id: r for r in rows}
    sre = by_id["eightfold:nvidia.com:893393669632"]
    assert sre.source == "eightfold" and sre.board == "jobs.nvidia.com/nvidia.com"
    assert sre.company == "NVIDIA"
    assert sre.title == "Senior Site Reliability Engineer - Kubernetes Platform"
    assert sre.location == "US, CA, Santa Clara; US, NC, Durham; US, TX, Austin"
    assert sre.url == "https://jobs.nvidia.com/careers/job/893393669632"
    assert sre.remote is False
    assert isinstance(sre.age_days, int)
    assert by_id["eightfold:nvidia.com:893398602245"].remote is True   # remote_local
    # the scout's Posting and a captured URL agree on identity
    assert sre.provider_id == _match(sre.url).provider_id


def test_search_pages_until_count_and_stops_at_cap():
    def page(url):
        start = int(_query(url)["start"])
        return {"status": 200, "data": {
            "count": 1000,
            "positions": [{"id": start + i, "name": f"Cloud {start + i}", "locations": ["US, TX, Austin"]}
                          for i in range(eightfold.PAGE)],
        }}

    fetcher = FakeFetcher([("/api/pcsx/search", 200, page)])
    rows = ADAPTER.search(fetcher, "jobs.nvidia.com/nvidia.com", "NVIDIA", ("cloud",))
    assert len(fetcher.asked) == eightfold.MAX_PAGES
    assert [_query(u)["start"] for u in fetcher.asked] == [str(i * 10) for i in range(eightfold.MAX_PAGES)]
    assert len(rows) == eightfold.MAX_PAGES * eightfold.PAGE


def test_search_accepts_a_bare_known_domain(fixture_json):
    fetcher = FakeFetcher([("/api/pcsx/search", 200, fixture_json("ats/eightfold_nvidia_search.json"))])
    rows = ADAPTER.search(fetcher, "nvidia.com", "nvidia.com", ("devops",))
    assert fetcher.asked[0].startswith("https://jobs.nvidia.com/api/pcsx/search?")
    assert rows and rows[0].company == "NVIDIA" and rows[0].board == "nvidia.com"


def test_search_none_when_board_fails():
    fetcher = FakeFetcher([("/api/pcsx/search", 403, {"message": "Not authorized for PCSX"})])
    assert ADAPTER.search(fetcher, "jobs.nvidia.com/nvidia.com", "NVIDIA", ("devops",)) is None


def test_search_none_when_a_later_query_fails(fixture_json):
    payload = fixture_json("ats/eightfold_nvidia_search.json")
    fetcher = FakeFetcher([
        ("query=devops", 200, payload),
        ("/api/pcsx/search", 503, b"busy"),
    ])
    assert ADAPTER.search(fetcher, "jobs.nvidia.com/nvidia.com", "NVIDIA", ("devops", "cloud")) is None


def test_search_none_on_unexpected_shape():
    fetcher = FakeFetcher([("/api/pcsx/search", 200, {"messages": {"domain": ["Missing data"]}})])
    assert ADAPTER.search(fetcher, "jobs.nvidia.com/nvidia.com", "NVIDIA", ("devops",)) is None


def test_search_empty_board_is_an_answer():
    fetcher = FakeFetcher([("/api/pcsx/search", 200, {"status": 200, "data": {"positions": [], "count": 0}})])
    assert ADAPTER.search(fetcher, "jobs.nvidia.com/nvidia.com", "NVIDIA", ("devops",)) == []


@pytest.mark.parametrize("board", ["", "nvidia", "evil.com/nvidia.com", "jobs.nvidia.com.evil.com/nvidia.com", "unknown.com"])
def test_search_none_for_a_board_it_does_not_own(board):
    fetcher = FakeFetcher([])
    assert ADAPTER.search(fetcher, board, "X", ("devops",)) is None
    assert fetcher.asked == []


def test_search_none_without_terms():
    fetcher = FakeFetcher([])
    assert ADAPTER.search(fetcher, "jobs.nvidia.com/nvidia.com", "NVIDIA", ()) is None
    assert fetcher.asked == []
