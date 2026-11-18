"""Apple's careers JSON, against trimmed real answers."""
import copy
import json
import pathlib
import urllib.parse

import httpx
import pytest

from jobscout import ats
from jobscout.ats import Gone, Unavailable, apple
from jobscout.ats.apple import ADAPTER

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
TERMS = ("devops", "site reliability", "infrastructure", "platform", "cloud")


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

    def bodies(self):
        return [json.loads(kwargs["content"]) for _, _, kwargs in self.calls]


def answer(status, body=""):
    return FakeFetcher(lambda method, url, kwargs: (status, body))


def match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(apple, "PAUSE", 0)


def test_matches_posting_urls():
    ref = match("https://jobs.apple.com/en-us/details/200695030-3956/build-release-infrastructure-engineer?team=HRDWR")
    assert (ref.provider, ref.board, ref.job_id) == ("apple", "apple", "200695030")
    assert ref.url == "https://jobs.apple.com/en-us/details/200695030-3956/build-release-infrastructure-engineer"
    assert ref.provider_id == "apple:apple:200695030"

    other_locale = match("https://jobs.apple.com/en-gb/details/200695296-0836")
    assert other_locale.job_id == "200695296"
    assert other_locale.url == "https://jobs.apple.com/en-us/details/200695296-0836"

    bare = match("https://jobs.apple.com/en-us/details/200695296/site-reliability-engineer-info-apps")
    assert bare.job_id == "200695296"

    # a slug that is not plain ASCII still names the posting; the id resolves
    accented = match("https://jobs.apple.com/fr-fr/details/200695296-0836/ing%C3%A9nieur-fiabilit%C3%A9")
    assert accented.job_id == "200695296"
    assert accented.url == "https://jobs.apple.com/en-us/details/200695296-0836"


def test_one_role_in_two_cities_is_one_posting():
    austin = match("https://jobs.apple.com/en-us/details/200695296-0157/site-reliability-engineer-info-apps")
    cupertino = match("https://jobs.apple.com/en-us/details/200695296-0836/site-reliability-engineer-info-apps")
    assert austin.provider_id == cupertino.provider_id == "apple:apple:200695296"


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.apple.com.evil.com/en-us/details/200695030-3956/build-release-infrastructure-engineer",
        "https://evil-jobs.apple.com/en-us/details/200695030-3956",
        "https://myjobs.apple.com/en-us/details/200695030-3956",
        "https://apple.com/en-us/details/200695030-3956",
        "https://www.apple.com/careers/us/",
        "https://jobs.apple.com/en-us/search?search=devops&location=united-states-USA",
        "https://jobs.apple.com/en-us/details/abc/x",
        "https://jobs.apple.com/en-us/details/200695030-3956/x/y",
    ],
)
def test_rejects_other_hosts_and_paths(url):
    assert match(url) is None


def test_registry_never_claims_a_lookalike(monkeypatch):
    monkeypatch.setattr(ats, "_adapters", [ADAPTER])
    assert ats.match("https://jobs.apple.com/en-us/details/200695030-3956")[1].job_id == "200695030"
    assert ats.match("https://jobs.apple.com.evil.com/en-us/details/200695030-3956") is None


def test_detail_reads_the_details_json():
    fetcher = answer(200, fixture("apple_detail.json"))
    ref = match("https://jobs.apple.com/en-us/details/200695030-3956/build-release-infrastructure-engineer")
    detail = ADAPTER.detail(fetcher, ref)
    assert fetcher.calls[0][:2] == ("GET", "https://jobs.apple.com/api/v1/jobDetails/200695030")
    assert detail.ref == ref
    assert detail.company == "Apple"
    assert detail.title == "Build, Release & Infrastructure Engineer"
    assert detail.location == "Sunnyvale, California, United States"
    assert detail.remote is False
    # "Standard" does not say full time or anything else
    assert detail.employment_type is None
    # "on an ongoing basis" is not a deadline
    assert detail.deadline is None
    assert isinstance(detail.age_days, int)
    html = detail.description_html
    for heading in ("Summary", "Description", "Responsibilities", "Minimum Qualifications",
                    "Preferred Qualifications", "Pay &amp; Benefits", "Application Deadline"):
        assert f"<h2>{heading}</h2>" in html
    assert "<li>CI/CD: Design, maintain, and monitor pipelines" in html
    # the bare-text pay footer ends up in a paragraph a block reader keeps
    assert "<p>At Apple, base pay is one part" in html
    assert "$150,400 and $277,600" in html


def test_detail_students_team_is_an_internship():
    detail = ADAPTER.detail(answer(200, fixture("apple_detail_intern.json")),
                            match("https://jobs.apple.com/en-us/details/200663981-3810"))
    assert detail.title == "Hardware Undergrad Engineering Internships"
    assert detail.employment_type == "INTERN"
    assert detail.location == "United States"


def test_detail_reads_a_dated_deadline_footer():
    payload = json.loads(fixture("apple_detail.json"))
    for footer in payload["res"]["postingFooters"]:
        for item in footer["localizations"]["en_US"]:
            if item["name"] == "Application Deadline":
                item["content"] = "<p>Apple accepts applications to this posting until Dec 12, 2026.</p>"
    detail = ADAPTER.detail(answer(200, json.dumps(payload)), match("https://jobs.apple.com/en-us/details/200695030-3956"))
    assert detail.deadline == "2026-12-12"


def test_detail_home_office_is_remote():
    payload = json.loads(fixture("apple_detail.json"))
    payload["res"]["homeOffice"] = True
    detail = ADAPTER.detail(answer(200, json.dumps(payload)), match("https://jobs.apple.com/en-us/details/200695030-3956"))
    assert detail.remote is True and detail.posting().remote is True


def test_detail_gone_on_404():
    with pytest.raises(Gone):
        ADAPTER.detail(answer(404, fixture("apple_404.json")), match("https://jobs.apple.com/en-us/details/200000001-0836"))


def test_detail_unavailable_on_403():
    with pytest.raises(Unavailable) as error:
        ADAPTER.detail(answer(403, "Forbidden"), match("https://jobs.apple.com/en-us/details/200695030-3956"))
    assert error.value.status == 403


def test_detail_unavailable_on_an_unexpected_body():
    with pytest.raises(Unavailable):
        ADAPTER.detail(answer(200, '{"res": null}'), match("https://jobs.apple.com/en-us/details/200695030-3956"))


def test_search_merges_cities_and_sends_the_site_body():
    fetcher = answer(200, fixture("apple_search.json"))
    rows = ADAPTER.search(fetcher, "apple", "Apple", ["site reliability"])
    assert len(fetcher.calls) == 1
    method, url, _ = fetcher.calls[0]
    assert (method, url) == ("POST", "https://jobs.apple.com/api/v1/search")
    body = fetcher.bodies()[0]
    assert body["query"] == '"site reliability"'
    assert body["filters"] == {"locations": ["postLocation-USA"]}
    assert body["page"] == 1 and body["sort"] == "relevance"
    # without this block the API answers with nothing
    assert body["format"]

    assert [r.provider_id for r in rows] == ["apple:apple:200695296", "apple:apple:200695686"]
    first = rows[0]
    assert (first.source, first.board, first.company) == ("apple", "apple", "Apple")
    assert first.title == "Site Reliability Engineer — Info Apps"
    assert first.location == "Austin, United States of America; Cupertino, United States of America"
    assert first.url == "https://jobs.apple.com/en-us/details/200695296-0157/site-reliability-engineer-info-apps"
    assert first.dedupe_key == match(first.url).provider_id
    assert isinstance(first.age_days, int)


def test_search_quotes_phrases_only():
    fetcher = answer(200, fixture("apple_search.json"))
    ADAPTER.search(fetcher, "apple", "Apple", ["devops", "site reliability"])
    assert [b["query"] for b in fetcher.bodies()] == ["devops", '"site reliability"']


def test_search_labels_student_roles_as_internships():
    payload = json.loads(fixture("apple_search.json"))
    payload["res"]["searchResults"][2]["team"] = {"teamName": "Students", "teamCode": "STDNT"}
    rows = ADAPTER.search(answer(200, json.dumps(payload)), "apple", "Apple", ["devops"])
    assert [r.employment_type for r in rows] == [None, "INTERN"]


def _page_of(template, page, size=20, total=1000):
    rows = []
    for n in range(size):
        row = copy.deepcopy(template)
        position = str(300000000 + page * 100 + n)
        row["id"] = row["reqId"] = f"{position}-0836"
        row["positionId"] = position
        rows.append(row)
    return json.dumps({"res": {"searchResults": rows, "totalRecords": total}})


def test_search_stops_at_the_page_cap():
    template = json.loads(fixture("apple_search.json"))["res"]["searchResults"][0]
    fetcher = FakeFetcher(lambda m, u, kw: (200, _page_of(template, json.loads(kw["content"])["page"])))
    rows = ADAPTER.search(fetcher, "apple", "Apple", ["infrastructure"])
    assert [b["page"] for b in fetcher.bodies()] == list(range(1, apple.MAX_PAGES + 1))
    assert len(rows) == 20 * apple.MAX_PAGES


def test_search_stops_when_the_total_is_reached():
    template = json.loads(fixture("apple_search.json"))["res"]["searchResults"][0]
    fetcher = FakeFetcher(lambda m, u, kw: (200, _page_of(template, json.loads(kw["content"])["page"], total=40)))
    ADAPTER.search(fetcher, "apple", "Apple", ["devops"])
    assert [b["page"] for b in fetcher.bodies()] == [1, 2]


def test_search_none_when_the_board_fails():
    assert ADAPTER.search(answer(403, "Forbidden"), "apple", "Apple", TERMS) is None
    assert ADAPTER.search(answer(200, "<html></html>"), "apple", "Apple", TERMS) is None


def test_search_none_when_a_later_query_fails():
    good = fixture("apple_search.json")
    fetcher = FakeFetcher(lambda m, u, kw: (200, good) if len(fetcher.calls) == 1 else (503, ""))
    assert ADAPTER.search(fetcher, "apple", "Apple", ["devops", "cloud"]) is None


def test_search_none_when_every_query_is_empty():
    # What the API answers when the request shape is off, e.g. no "format".
    empty = json.dumps({"res": {"searchResults": [], "totalRecords": 0}})
    assert ADAPTER.search(answer(200, empty), "apple", "Apple", TERMS) is None


def test_search_without_terms_did_not_answer():
    fetcher = answer(200, fixture("apple_search.json"))
    assert ADAPTER.search(fetcher, "apple", "Apple", ()) is None
    assert fetcher.calls == []


def test_alive_confirms_a_posting_through_search_and_never_closes_one():
    import json as _json

    import httpx as _httpx

    from jobscout.ats import apple as module

    ref = module.ADAPTER.match(urllib.parse.urlsplit("https://jobs.apple.com/en-us/details/200631016-0157/devops-engineer-agents"))

    class Search:
        def __init__(self, rows, status=200):
            self.rows, self.status, self.calls = rows, status, []

        def send(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs.get("timeout")))
            body = _json.dumps({"res": {"searchResults": self.rows, "totalRecords": len(self.rows)}})
            return _httpx.Response(self.status, text=body, request=_httpx.Request(method, url))

    hit = Search([{"positionId": "200631016", "id": "200631016-0157", "postingTitle": "DevOps Engineer - Agents"}])
    assert module.ADAPTER.alive(hit, ref, "DevOps Engineer - Agents") is True
    assert hit.calls[0][:2] == ("POST", module.SEARCH)
    assert module.ADAPTER.alive(Search([{"positionId": "999"}]), ref, "DevOps Engineer - Agents") is None
    assert module.ADAPTER.alive(Search([], status=503), ref, "DevOps Engineer - Agents") is None
    assert module.ADAPTER.alive(hit, ref, "") is None


def test_detail_asks_for_the_long_timeout():
    import httpx as _httpx

    from jobscout.ats import apple as module

    seen = {}

    class Slow:
        def send(self, method, url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _httpx.Response(404, text="{}", request=_httpx.Request(method, url))

    ref = module.ADAPTER.match(urllib.parse.urlsplit("https://jobs.apple.com/en-us/details/200631016-0157/devops-engineer-agents"))
    with pytest.raises(module.Gone):
        module.ADAPTER.detail(Slow(), ref)
    assert seen["timeout"] == module.DETAIL_TIMEOUT
