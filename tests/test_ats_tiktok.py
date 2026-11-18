"""TikTok careers through api.lifeattiktok.com, against trimmed real answers."""
import json
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats import tiktok
from jobscout.descriptions import parse_description

ADAPTER = tiktok.ADAPTER
JOB = "7494051296631752978"


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
    monkeypatch.setattr(tiktok, "PAUSE", 0)


def _match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


@pytest.mark.parametrize(
    "url",
    [
        f"https://lifeattiktok.com/search/{JOB}",
        f"https://www.lifeattiktok.com/search/{JOB}?spread=ABC123",
        f"https://lifeattiktok.com/position/{JOB}/detail",
        f"https://careers.tiktok.com/position/{JOB}/detail",
        f"https://careers.tiktok.com/resume/{JOB}/apply",
        f"https://lifeattiktok.com/en/search/{JOB}",
    ],
)
def test_match_posting_urls(url):
    ref = _match(url)
    assert ref is not None
    assert (ref.provider, ref.board, ref.job_id) == ("tiktok", "tiktok", JOB)
    assert ref.url == f"https://lifeattiktok.com/search/{JOB}"
    assert ref.provider_id == f"tiktok:tiktok:{JOB}"


@pytest.mark.parametrize(
    "url",
    [
        f"https://lifeattiktok.com.evil.com/search/{JOB}",
        f"https://notlifeattiktok.com/search/{JOB}",
        f"https://careers.tiktok.com.evil.com/position/{JOB}/detail",
        f"https://www.tiktok.com/search/{JOB}",
        f"https://evilcareers.tiktok.com/position/{JOB}/detail",
        f"https://joinbytedance.com/search/{JOB}",
        "https://lifeattiktok.com/search?keyword=devops",
        "https://lifeattiktok.com/search/abc",
        f"https://lifeattiktok.com/blog/{JOB}",
    ],
)
def test_match_rejects_other_hosts_and_paths(url):
    assert _match(url) is None


def test_detail_from_api(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/tiktok_detail.json")))
    detail = ADAPTER.detail(fetcher, _match(f"https://careers.tiktok.com/position/{JOB}/detail"))

    method, url, kw = fetcher.calls[0]
    assert (method, url) == ("POST", f"https://api.lifeattiktok.com/api/v1/public/supplier/job/posts/{JOB}")
    assert kw["headers"]["website-path"] == "tiktok"
    assert _sent(fetcher.calls[0]) == {"job_post_id": JOB}

    assert detail.company == "TikTok"
    assert detail.title == "Site Reliability Engineer - Compute Platform"
    assert detail.location == "Seattle, Washington, United States of America"
    assert detail.remote is False
    assert detail.employment_type is None    # "Regular" is not stated as full time
    assert detail.deadline is None and detail.age_days is None
    assert detail.ref.url == f"https://lifeattiktok.com/search/{JOB}"

    body = detail.description_html
    assert "<h3>Responsibilities:</h3><ul><li>Responsible for the reliability" in body
    assert "<h3>Minimal Qualifications:</h3><ul><li>" in body
    assert "<h3>Compensation Description (Annually)</h3>" in body
    assert "$153900 - $368220" in body

    sections = parse_description(body).sections
    assert "responsibilities" in [section["key"] for section in sections]
    # TikTok's own wording is kept, even though descriptions only knows
    # "Minimum Qualifications" and so files this one under about.
    assert any(section["title"] == "Responsibilities" for section in sections)


def test_detail_gone_when_api_returns_null(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/tiktok_missing.json")))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, tiktok._ref("7494051296631752979"))


def test_detail_record_for_another_id_is_unavailable(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/tiktok_detail.json")))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, tiktok._ref("7494051296631752979"))


def test_detail_404_from_the_api_route_is_not_a_closed_posting():
    # The API answers a missing posting with a null record, so a bare 404 is
    # the route changing. Reading it as Gone would close every TikTok job.
    with pytest.raises(Unavailable) as caught:
        ADAPTER.detail(FakeFetcher(_answer(404, "")), tiktok._ref(JOB))
    assert caught.value.status == 404


def test_detail_unavailable_on_403():
    with pytest.raises(Unavailable) as caught:
        ADAPTER.detail(FakeFetcher(_answer(403, "denied")), tiktok._ref(JOB))
    assert caught.value.status == 403


def test_detail_unavailable_on_api_error():
    error = '{"code":-9000002,"data":null,"message":"failed","error":{"message":"invalid parameter"}}'
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher(_answer(200, error)), tiktok._ref(JOB))
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher(_answer(400, "invalid request")), tiktok._ref(JOB))


def test_search_keeps_us_postings(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/tiktok_search.json")))
    rows = ADAPTER.search(fetcher, "tiktok", "TikTok", ("devops",))

    method, url, kw = fetcher.calls[0]
    assert (method, url) == ("POST", tiktok.SEARCH)
    assert kw["headers"]["website-path"] == "tiktok"
    sent = _sent(fetcher.calls[0])
    assert sent["keyword"] == "devops" and sent["offset"] == 0 and sent["limit"] == tiktok.PAGE

    by_id = {row.provider_id: row for row in rows}
    # The Singapore posting in the same answer is dropped.
    assert set(by_id) == {"tiktok:tiktok:7704981006856145205", f"tiktok:tiktok:{JOB}"}
    for row in rows:
        assert row.source == "tiktok" and row.board == "tiktok" and row.company == "TikTok"
        assert row.url.startswith("https://lifeattiktok.com/search/")

    intern = by_id["tiktok:tiktok:7704981006856145205"]
    assert intern.employment_type == "INTERN"
    assert intern.location == "San Jose, California, United States of America"
    assert by_id[f"tiktok:tiktok:{JOB}"].title == "Site Reliability Engineer - Compute Platform"


def test_search_dedupes_across_terms(fixture_text):
    fetcher = FakeFetcher(_answer(200, fixture_text("ats/tiktok_search.json")))
    rows = ADAPTER.search(fetcher, "tiktok", "tiktok", ("devops", "site reliability"))
    assert len(fetcher.calls) == 2
    assert len(rows) == 2
    assert {row.company for row in rows} == {"TikTok"}


def test_search_pages_and_caps(monkeypatch, fixture_json):
    monkeypatch.setattr(tiktok, "PAGE", 1)
    monkeypatch.setattr(tiktok, "MAX_PAGES", 2)
    payload = fixture_json("ats/tiktok_search.json")
    jobs = payload["data"]["job_post_list"]
    payload["data"]["count"] = 500

    def handler(method, url, kw):
        offset = json.loads(kw["content"])["offset"]
        page = dict(payload, data=dict(payload["data"], job_post_list=jobs[offset:offset + 1]))
        return 200, json.dumps(page)

    fetcher = FakeFetcher(handler)
    rows = ADAPTER.search(fetcher, "tiktok", "TikTok", ("devops",))
    assert [_sent(call)["offset"] for call in fetcher.calls] == [0, 1]
    assert len(rows) == 1      # second page was the Singapore one


def test_search_none_when_board_fails():
    assert ADAPTER.search(FakeFetcher(_answer(403, "denied")), "tiktok", "TikTok", ("devops",)) is None
    assert ADAPTER.search(FakeFetcher(_answer(404, "")), "tiktok", "TikTok", ("devops",)) is None
    error = '{"code":1,"data":null,"message":"busy"}'
    assert ADAPTER.search(FakeFetcher(_answer(200, error)), "tiktok", "TikTok", ("devops",)) is None


def test_search_none_when_a_later_term_fails(fixture_text):
    good = fixture_text("ats/tiktok_search.json")

    def handler(method, url, kw):
        return (200, good) if json.loads(kw["content"])["keyword"] == "devops" else (503, "busy")

    assert ADAPTER.search(FakeFetcher(handler), "tiktok", "TikTok", ("devops", "cloud")) is None


def test_search_empty_answer_is_an_answer():
    empty = '{"code":0,"data":{"job_post_list":[],"count":0},"message":"ok"}'
    assert ADAPTER.search(FakeFetcher(_answer(200, empty)), "tiktok", "TikTok", ("devops",)) == []


def test_search_without_terms_is_not_an_answer():
    fetcher = FakeFetcher(_answer(200, "{}"))
    assert ADAPTER.search(fetcher, "tiktok", "TikTok", ()) is None
    assert fetcher.calls == []
