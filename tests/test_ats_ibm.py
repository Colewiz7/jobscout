"""IBM careers through the www-api search index, against trimmed real answers."""
import json
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats import ibm

ADAPTER = ibm.ADAPTER


class FakeFetcher:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def send(self, method, url, **kw):
        self.calls.append((method, url, kw))
        status, body = self.handler(method, url, kw)
        if isinstance(body, str):
            body = body.encode()
        return httpx.Response(status, content=body, request=httpx.Request(method, url))


def _answer(status, body=b""):
    return lambda method, url, kw: (status, body)


def _sent(call):
    return json.loads(call[2]["content"])


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(ibm, "PAUSE", 0)


def _match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.ibm.com/careers/JobDetail?jobId=137275",
        "https://careers.ibm.com/en_US/careers/JobDetail?jobId=137275&source=WEB_Search_NA",
        "https://careers.ibm.com/careers/JobDetail/DevOps-Engineer/137275",
        "https://ibmglobal.avature.net/careers/JobDetail?jobId=137275",
        "https://CAREERS.IBM.COM/careers/JobDetail?jobId=137275",
    ],
)
def test_match_posting_urls(url):
    ref = _match(url)
    assert ref is not None
    assert (ref.provider, ref.board, ref.job_id) == ("ibm", "ibm", "137275")
    assert ref.url == "https://careers.ibm.com/careers/JobDetail?jobId=137275"
    assert ref.provider_id == "ibm:ibm:137275"


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.ibm.com.evil.com/careers/JobDetail?jobId=137275",
        "https://careers-ibm.com/careers/JobDetail?jobId=137275",
        "https://evilcareers.ibm.com/careers/JobDetail?jobId=137275",
        "https://ibmglobal.avature.net.evil.com/careers/JobDetail?jobId=137275",
        "https://www.ibm.com/careers/search?q=devops",
        "https://careers.ibm.com/careers/SearchJobs?jobKeyword=devops",
        "https://careers.ibm.com/careers/JobDetail?jobId=abc",
        "https://careers.ibm.com/careers/JobDetail",
        "https://careers.ibm.com/internalmobility/JobDetail?jobId=137275",
    ],
)
def test_match_rejects_other_hosts_and_paths(url):
    assert _match(url) is None


def test_detail_from_index(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/ibm_detail.json")))
    detail = ADAPTER.detail(fetcher, _match("https://careers.ibm.com/careers/JobDetail?jobId=137275"))

    assert detail.company == "IBM"
    assert detail.title == "DevOps Engineer"
    assert detail.location == "Dallas, United States"
    assert detail.remote is False            # IBM says Hybrid
    assert detail.employment_type is None    # "Professional" is a level, not a type
    assert detail.deadline is None
    assert isinstance(detail.age_days, int) and detail.age_days >= 0
    assert detail.description_html.startswith("<p>DevOps Engineer, IBM Corporation")
    assert detail.ref.url == "https://careers.ibm.com/careers/JobDetail?jobId=137275"

    method, url, kw = fetcher.calls[0]
    assert (method, url) == ("POST", ibm.SEARCH)
    sent = _sent(fetcher.calls[0])
    assert sent["appId"] == "careers" and sent["scopes"] == ["careers2"]
    assert sent["query"]["bool"]["must"] == [{"term": {"field_text_01": 137275}}]


def test_detail_gone_when_index_no_longer_lists_it(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/ibm_missing.json")))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, ibm._ref("1"))


def test_detail_hit_for_another_id_is_not_an_answer(fixture_text):
    # The term query should only ever return the id it asked for. When it
    # does not, the index changed, which says nothing about this posting.
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/ibm_detail.json")))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, ibm._ref("999999"))


def test_detail_404_from_the_search_endpoint_is_not_a_closed_posting():
    # The index URL does not name the posting, so a 404 there is the endpoint
    # moving. Reading it as Gone would close every IBM job at once.
    with pytest.raises(Unavailable) as caught:
        ADAPTER.detail(FakeFetcher(_answer(404, "not found")), ibm._ref("137275"))
    assert caught.value.status == 404


def test_detail_unavailable_on_403():
    with pytest.raises(Unavailable) as caught:
        ADAPTER.detail(FakeFetcher(_answer(403, "denied")), ibm._ref("137275"))
    assert caught.value.status == 403


def test_detail_unavailable_on_unexpected_shape():
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher(_answer(200, '{"errors": []}')), ibm._ref("137275"))


def test_search_parses_us_postings(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/ibm_search.json")))
    rows = ADAPTER.search(fetcher, "ibm", "IBM", ("site reliability",))

    assert len(fetcher.calls) == 1
    sent = _sent(fetcher.calls[0])
    query = sent["query"]["bool"]
    assert query["must"][0]["simple_query_string"]["query"] == "site reliability"
    assert query["must"][0]["simple_query_string"]["default_operator"] == "and"
    assert query["filter"] == [{"term": {"field_keyword_05": "United States"}}]
    assert sent["from"] == 0 and sent["size"] == ibm.PAGE

    by_id = {row.provider_id: row for row in rows}
    assert set(by_id) == {"ibm:ibm:133504", "ibm:ibm:130917", "ibm:ibm:134057"}
    for row in rows:
        assert row.source == "ibm" and row.board == "ibm" and row.company == "IBM"
        assert row.url.startswith("https://careers.ibm.com/careers/JobDetail?jobId=")
        assert isinstance(row.age_days, int)

    intern = by_id["ibm:ibm:133504"]
    assert intern.title == "Quantum Infrastructure Intern 2027 - SRE"
    assert intern.location == "Yorktown Heights, United States"
    assert intern.employment_type == "INTERN"
    assert intern.remote is False

    confluent = by_id["ibm:ibm:130917"]
    assert confluent.remote is True
    assert confluent.location == "Multiple Cities, United States"


def test_search_display_name_defaults_when_config_has_none(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/ibm_search.json")))
    rows = ADAPTER.search(fetcher, "ibm", "ibm", ("devops",))
    assert {row.company for row in rows} == {"IBM"}


def test_search_dedupes_across_terms(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/ibm_search.json")))
    rows = ADAPTER.search(fetcher, "ibm", "IBM", ("devops", "site reliability"))
    assert len(fetcher.calls) == 2
    assert len(rows) == 3


def test_search_pages_until_short_page(monkeypatch, fixture_json):
    monkeypatch.setattr(ibm, "PAGE", 2)
    payload = fixture_json("ats/ibm_search.json")
    hits = payload["hits"]["hits"]
    payload["hits"]["total"]["value"] = 50

    def handler(method, url, kw):
        offset = json.loads(kw["content"])["from"]
        page = dict(payload, hits=dict(payload["hits"], hits=hits[offset:offset + 2]))
        return 200, json.dumps(page)

    fetcher = FakeFetcher(handler)
    rows = ADAPTER.search(fetcher, "ibm", "IBM", ("cloud",))
    assert [_sent(call)["from"] for call in fetcher.calls] == [0, 2]
    assert len(rows) == 3


def test_search_stops_at_page_cap(monkeypatch, fixture_json):
    monkeypatch.setattr(ibm, "PAGE", 1)
    monkeypatch.setattr(ibm, "MAX_PAGES", 2)
    payload = fixture_json("ats/ibm_search.json")
    payload["hits"]["total"]["value"] = 900
    fetcher = FakeFetcher(_answer(200, json.dumps(payload)))
    # Every page is full and the total is large, so only the cap stops it.
    ADAPTER.search(fetcher, "ibm", "IBM", ("cloud",))
    assert len(fetcher.calls) == 2


def test_search_none_when_board_fails():
    assert ADAPTER.search(FakeFetcher(_answer(403, "denied")), "ibm", "IBM", ("devops",)) is None
    assert ADAPTER.search(FakeFetcher(_answer(404, "gone")), "ibm", "IBM", ("devops",)) is None
    assert ADAPTER.search(FakeFetcher(_answer(200, "<html>")), "ibm", "IBM", ("devops",)) is None


def test_search_none_when_a_later_term_fails(fixture_text):
    good = fixture_text("ats/ibm_search.json")

    def handler(method, url, kw):
        term = json.loads(kw["content"])["query"]["bool"]["must"][0]["simple_query_string"]["query"]
        return (200, good) if term == "devops" else (503, "busy")

    assert ADAPTER.search(FakeFetcher(handler), "ibm", "IBM", ("devops", "cloud")) is None


def test_search_without_terms_is_not_an_answer():
    fetcher = FakeFetcher(_answer(200, "{}"))
    assert ADAPTER.search(fetcher, "ibm", "IBM", ()) is None
    assert fetcher.calls == []


def test_detail_posting_round_trip(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/ibm_detail.json")))
    posting = ADAPTER.detail(fetcher, ibm._ref("137275")).posting()
    assert posting.source == "ibm" and posting.provider_id == "ibm:ibm:137275"
    assert posting.dedupe_key == "ibm:ibm:137275"
