"""Google careers pages, against trimmed real server-rendered pages."""
import copy
import json
import pathlib
import re
import urllib.parse

import httpx
import pytest

from jobscout import ats
from jobscout.ats import Gone, Unavailable, google
from jobscout.ats.google import ADAPTER

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
TERMS = ("devops", "site reliability", "infrastructure", "platform", "cloud")
JOB = "107878807020413638"
VIEW = f"https://www.google.com/about/careers/applications/jobs/results/{JOB}"


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


def answer(status, body=""):
    return FakeFetcher(lambda method, url: (status, body))


def match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


def fixture_records():
    page = fixture("google_search.html")
    start = re.search(r"AF_initDataCallback\(\{key: 'ds:1', hash: '2', data:", page).end()
    data, _ = json.JSONDecoder().raw_decode(page, start)
    return data[0]


def results_page(records, total, size=20):
    """A results page in the shape the site serves: the RPC map, then the block."""
    return (
        "<!doctype html><html><head><script nonce=\"t\">var AF_initDataKeys = [\"ds:0\",\"ds:1\"]; "
        "var AF_dataServiceRequests = {'ds:0' : {id:'O4hThe',request:[]},"
        "'ds:1' : {id:'r06xKb',request:[[\"devops\",null,null,null,\"en-US\",null,[[\"United States\"]],1]]}};"
        "</script></head><body>"
        "<script class=\"ds:0\" nonce=\"t\">AF_initDataCallback({key: 'ds:0', hash: '1', data:[[]], sideChannel: {}});</script>"
        f"<script class=\"ds:1\" nonce=\"t\">AF_initDataCallback({{key: 'ds:1', hash: '2', "
        f"data:{json.dumps([records, None, total, size])}, sideChannel: {{}}}});</script>"
        "</body></html>"
    )


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(google, "PAUSE", 0)


@pytest.mark.parametrize(
    "url",
    [
        f"{VIEW}-partner-solutions-architect-ai-infrastructure-and-sovereign-cloud-google-cloud",
        VIEW,
        f"{VIEW}-partner-solutions-architect?location=United%20States&q=cloud",
        f"https://google.com/about/careers/applications/jobs/results/{JOB}",
        f"https://www.google.com/about/careers/applications/u/0/jobs/results/{JOB}-partner",
        # the old host, which still redirects to the new path
        f"https://careers.google.com/jobs/results/{JOB}-partner-solutions-architect/",
    ],
)
def test_matches_posting_urls(url):
    ref = match(url)
    assert (ref.provider, ref.board, ref.job_id) == ("google", "google", JOB)
    assert ref.url == VIEW
    assert ref.provider_id == f"google:google:{JOB}"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.google.com.evil.com/about/careers/applications/jobs/results/{JOB}",
        f"https://careers.google.com.evil.com/jobs/results/{JOB}",
        f"https://evilgoogle.com/about/careers/applications/jobs/results/{JOB}",
        f"https://www.google.co.uk/about/careers/applications/jobs/results/{JOB}",
        "https://www.google.com/about/careers/applications/jobs/results/?q=devops",
        f"https://www.google.com/url?q=https://www.google.com/about/careers/applications/jobs/results/{JOB}",
        f"https://www.google.com/about/careers/applications/jobs/results/{JOB}/extra",
        f"https://careers.google.com/about/careers/applications/jobs/results/{JOB}",
    ],
)
def test_rejects_other_hosts_and_paths(url):
    assert match(url) is None


def test_registry_never_claims_a_lookalike(monkeypatch):
    monkeypatch.setattr(ats, "_adapters", [ADAPTER])
    assert ats.match(VIEW)[1].job_id == JOB
    assert ats.match(f"https://www.google.com.evil.com/about/careers/applications/jobs/results/{JOB}") is None


def test_detail_reads_the_rendered_job():
    fetcher = answer(200, fixture("google_detail.html"))
    ref = match(f"{VIEW}-partner-solutions-architect")
    detail = ADAPTER.detail(fetcher, ref)
    method, url, kwargs = fetcher.calls[0]
    assert (method, url) == ("GET", VIEW)
    assert kwargs["headers"]["Accept-Language"].startswith("en-US")
    assert detail.ref == ref
    assert detail.company == "Google"
    assert detail.title == "Partner Solutions Architect, AI Infrastructure and Sovereign Cloud, Google Cloud"
    assert detail.location == "Atlanta, GA, USA; Austin, TX, USA; Boulder, CO, USA"
    assert detail.remote is False
    assert detail.employment_type is None
    # "open until at least November 8, 2026" is a floor, not a closing date
    assert detail.deadline is None
    assert isinstance(detail.age_days, int) and detail.age_days >= 20
    html = detail.description_html
    assert html.startswith("<h3>Minimum qualifications:</h3>")
    assert "<h3>About the job</h3>" in html and "<h3>Responsibilities</h3>" in html
    # the pay line sits after the last </p> on the page and must not be lost
    assert "<p>Individual pay is determined" in html
    assert "US: $183000 - $265000 (USD)" in html


def test_detail_intern_level_and_application_window():
    page = fixture("google_detail_intern.html")
    job = re.search(r'request:\["(\d+)"\]', page).group(1)
    detail = ADAPTER.detail(answer(200, page), match(f"https://www.google.com/about/careers/applications/jobs/results/{job}"))
    assert detail.title == "Software Engineering Intern, PhD, Summer 2027"
    assert detail.employment_type == "INTERN"
    # "The anticipated application window is open until February 26, 2027"
    assert detail.deadline == "2027-02-26"


def test_detail_gone_when_the_page_has_no_job():
    # An unknown id is still a 200, titled "Jobs search", with an empty block.
    ref = match("https://www.google.com/about/careers/applications/jobs/results/107878807020413639")
    with pytest.raises(Gone):
        ADAPTER.detail(answer(200, fixture("google_missing.html")), ref)


def test_detail_gone_on_404():
    with pytest.raises(Gone):
        ADAPTER.detail(answer(404, "Not Found"), match(VIEW))


def test_detail_unavailable_on_403():
    with pytest.raises(Unavailable) as error:
        ADAPTER.detail(answer(403, "Forbidden"), match(VIEW))
    assert error.value.status == 403


def test_detail_unavailable_without_job_data():
    with pytest.raises(Unavailable):
        ADAPTER.detail(answer(200, "<html><body>Our systems have detected unusual traffic</body></html>"), match(VIEW))


def test_detail_unavailable_when_the_page_is_for_another_job():
    # Never call a posting gone on the strength of somebody else's page.
    other = match("https://www.google.com/about/careers/applications/jobs/results/123456789012345678")
    with pytest.raises(Unavailable):
        ADAPTER.detail(answer(200, fixture("google_missing.html")), other)
    with pytest.raises(Unavailable):
        ADAPTER.detail(answer(200, fixture("google_detail.html")), other)


def test_search_reads_the_results_page():
    fetcher = answer(200, fixture("google_search.html"))
    rows = ADAPTER.search(fetcher, "google", "Google", ["site reliability"])
    assert len(fetcher.calls) == 1
    method, url, _ = fetcher.calls[0]
    assert method == "GET"
    assert url == ("https://www.google.com/about/careers/applications/jobs/results/"
                   "?q=%22site%20reliability%22&location=United%20States")
    assert len(rows) == 3
    first = rows[0]
    assert (first.source, first.board, first.company) == ("google", "google", "Google")
    assert first.title == "Site Reliability Manager, Android SRE"
    assert first.location == "San Francisco, CA, USA"
    assert first.url == "https://www.google.com/about/careers/applications/jobs/results/97563983497568966"
    assert first.provider_id == "google:google:97563983497568966"
    assert first.dedupe_key == match(first.url).provider_id
    assert all(isinstance(r.age_days, int) for r in rows)


def test_search_keeps_the_posting_company_and_level():
    records = copy.deepcopy(fixture_records())
    records[0][7] = "DeepMind"
    records[1][20] = 5
    rows = ADAPTER.search(answer(200, results_page(records, 3)), "google", "Google", ["devops"])
    assert [r.company for r in rows] == ["DeepMind", "Google", "Google"]
    assert [r.employment_type for r in rows] == [None, "INTERN", None]


def test_search_pages_up_to_the_cap():
    template = fixture_records()[0]

    def handler(method, url):
        page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("page", ["1"])[0])
        records = []
        for n in range(20):
            record = copy.deepcopy(template)
            record[0] = str(500000000000000000 + page * 100 + n)
            records.append(record)
        return 200, results_page(records, 405)

    fetcher = FakeFetcher(handler)
    rows = ADAPTER.search(fetcher, "google", "Google", ["devops"])
    pages = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query).get("page", ["1"])[0] for _, u, _ in fetcher.calls]
    assert pages == [str(n) for n in range(1, google.MAX_PAGES + 1)]
    assert len(rows) == 20 * google.MAX_PAGES


def test_search_none_when_the_board_fails():
    assert ADAPTER.search(answer(403, "Forbidden"), "google", "Google", TERMS) is None
    assert ADAPTER.search(answer(200, "<html>no data</html>"), "google", "Google", TERMS) is None


def test_search_none_when_a_later_query_fails():
    good = fixture("google_search.html")
    fetcher = FakeFetcher(lambda m, u: (200, good) if len(fetcher.calls) == 1 else (429, ""))
    assert ADAPTER.search(fetcher, "google", "Google", ["devops", "cloud"]) is None


def test_search_none_when_every_query_is_empty():
    assert ADAPTER.search(answer(200, results_page([], 0)), "google", "Google", TERMS) is None


def test_search_without_terms_did_not_answer():
    fetcher = answer(200, fixture("google_search.html"))
    assert ADAPTER.search(fetcher, "google", "Google", ()) is None
    assert fetcher.calls == []
