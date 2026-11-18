import pathlib
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats import successfactors
from jobscout.ats.successfactors import ADAPTER

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"


def fixture(name):
    return (FIXTURES / name).read_text()


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(successfactors, "PAUSE", 0)


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


def query_of(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


# --- match --------------------------------------------------------------------

def test_matches_a_career_site_job_url():
    ref = match("https://jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/?feedId=null&utm_source=J2WRSS")
    assert ref.provider == "successfactors"
    assert ref.board == "jobs.mcdonalds.com"
    assert ref.job_id == "1408247300"
    assert ref.url == "https://jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/"
    assert ref.provider_id == "successfactors:jobs.mcdonalds.com:1408247300"

    acuity = match("https://careers.acuityinc.com/job/Conyers-Advanced-Manufacturing-Engineer-GA-30012/1441848400")
    assert (acuity.board, acuity.job_id) == ("careers.acuityinc.com", "1441848400")


def test_matches_a_requisition_link_on_a_career_site():
    ref = match("https://careers.acuityinc.com/job-invite/16832/?locale=en_US")
    assert (ref.board, ref.job_id) == ("careers.acuityinc.com", "req-16832")
    assert ref.url == "https://careers.acuityinc.com/job-invite/16832/"


def test_matches_legacy_links_for_a_known_company():
    for url in (
        "https://career41.sapsf.com/career?career_ns=job_listing&company=mcdonaldsc&navBarLevel=JOB_SEARCH&career_job_req_id=16832",
        "https://career41.sapsf.com/sfcareer/jobreqcareer?jobId=16832&company=mcdonaldsc",
    ):
        ref = match(url)
        assert ref.board == "jobs.mcdonalds.com"
        assert ref.job_id == "req-16832"
        assert ref.url == "https://jobs.mcdonalds.com/job-invite/16832/"


def test_matches_legacy_links_for_an_unknown_company():
    ref = match("https://career4.successfactors.com/career?company=AcmeDefenseP&career_job_req_id=4242&career_ns=job_listing")
    assert ref.board == "career4.successfactors.com/AcmeDefenseP"
    assert ref.job_id == "req-4242"
    assert ref.url == (
        "https://career4.successfactors.com/career?career_ns=job_listing"
        "&company=AcmeDefenseP&career_job_req_id=4242"
    )
    eu = match("https://career5.successfactors.eu/career?company=acme&career_job_req_id=7")
    assert eu.board == "career5.successfactors.eu/acme"


@pytest.mark.parametrize(
    "url",
    [
        # lookalike hosts
        "https://jobs.mcdonalds.com.evil.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/",
        "https://evil-jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/",
        "https://career41.sapsf.com.evil.com/career?company=mcdonaldsc&career_job_req_id=1",
        "https://career4.successfactors.com.example/career?company=x&career_job_req_id=1",
        "https://evilsuccessfactors.com/career?company=x&career_job_req_id=1",
        # a CSB-shaped path on a domain nobody has checked
        "https://jobs.example.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/",
        # SAP hosts that are not the career portal
        "https://hcm41.sapsf.com/career?company=mcdonaldsc&career_job_req_id=1",
        "https://rmkcdn.successfactors.com/career?company=x&career_job_req_id=1",
        # not a posting
        "https://jobs.mcdonalds.com/search/?q=cloud",
        "https://jobs.mcdonalds.com/",
        "https://career41.sapsf.com/career?company=mcdonaldsc",
        "https://career41.sapsf.com/career?company=mcdonaldsc&career_job_req_id=abc",
        "https://career41.sapsf.com/career?company=../x&career_job_req_id=1",
    ],
)
def test_rejects_other_urls(url):
    assert match(url) is None


# --- detail -------------------------------------------------------------------

def test_detail_reads_the_job_page():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("successfactors_job.html")))
    ref = match("https://jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/")
    detail = ADAPTER.detail(fetcher, ref)

    assert fetcher.calls[0][1] == "https://jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/?locale=en_US"
    assert detail.ref == ref
    assert detail.company == "McDonald's"
    assert detail.title == "Cloud Engineer III"
    assert detail.location == "Chicago, IL, US"
    assert detail.remote is False
    assert detail.deadline == "2027-01-02T06:00:00+00:00"
    assert isinstance(detail.age_days, int) and detail.age_days >= 0
    assert detail.description_html.startswith("<p><strong>Company Description:")
    assert "McDonald’s Global Technology" in detail.description_html
    assert detail.posting().provider_id == "successfactors:jobs.mcdonalds.com:1408247300"


def test_detail_resolves_a_requisition_to_the_job_id():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("successfactors_job.html")))
    ref = match("https://career41.sapsf.com/career?company=mcdonaldsc&career_job_req_id=16832")
    detail = ADAPTER.detail(fetcher, ref)

    assert fetcher.calls[0][1] == "https://jobs.mcdonalds.com/job-invite/16832/?locale=en_US"
    assert detail.ref.provider_id == "successfactors:jobs.mcdonalds.com:1408247300"
    assert detail.ref.url == "https://jobs.mcdonalds.com/job/Cloud-Engineer-III/1408247300/"
    assert detail.title == "Cloud Engineer III"


def test_detail_follows_sap_for_an_unknown_company():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("successfactors_job.html")))
    ref = match("https://career4.successfactors.com/career?company=AcmeDefenseP&career_job_req_id=4242")
    detail = ADAPTER.detail(fetcher, ref)

    url = fetcher.calls[0][1]
    assert url.startswith("https://career4.successfactors.com/career?")
    assert query_of(url)["company"] == "AcmeDefenseP"
    assert query_of(url)["career_job_req_id"] == "4242"
    # no known site to file it under, so it keeps its requisition identity
    assert detail.ref == ref
    assert detail.company == "McDonald's Corporation"


def test_detail_filled_position_is_gone():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("successfactors_filled.html")))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, match("https://jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-I-IL-60607/1352285000/"))


def test_detail_error_page_is_unavailable_not_gone():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("successfactors_errorpage.html")))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match("https://jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-III-IL-60607/1000000000/"))


def test_detail_page_without_a_posting_is_unavailable():
    fetcher = FakeFetcher(lambda method, url: (200, "<html><body><h1>Welcome to careers</h1></body></html>"))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match("https://jobs.mcdonalds.com/job/x/1408247300/"))


def test_detail_reads_json_ld_when_present():
    page = """<html><head><script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "JobPosting", "title": "Site Reliability Engineer",
     "description": "<p>Keep the lights on.</p>", "datePosted": "2026-11-01",
     "employmentType": "FULL_TIME", "hiringOrganization": {"@type": "Organization", "name": "Acuity Inc."},
     "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Conyers", "addressRegion": "GA", "addressCountry": "US"}},
                     {"@type": "Place", "address": {"addressLocality": "Remote", "addressCountry": "US"}}]}
    </script></head><body></body></html>"""
    fetcher = FakeFetcher(lambda method, url: (200, page))
    detail = ADAPTER.detail(fetcher, match("https://careers.acuityinc.com/job/x/1441848400/"))
    assert detail.title == "Site Reliability Engineer"
    assert detail.company == "Acuity"
    assert detail.location == "Conyers, GA, US; Remote, US"
    assert detail.remote is True
    assert detail.employment_type == "FULL_TIME"
    assert detail.description_html == "<p>Keep the lights on.</p>"


def test_detail_404_is_gone():
    fetcher = FakeFetcher(lambda method, url: (404, "not found"))
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, match("https://jobs.mcdonalds.com/job/x/1408247300/"))


def test_detail_403_is_unavailable():
    fetcher = FakeFetcher(lambda method, url: (403, "Access Denied"))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match("https://jobs.mcdonalds.com/job/x/1408247300/"))


# --- search -------------------------------------------------------------------

def test_search_pages_through_us_results_per_term():
    def handler(method, url):
        start = query_of(url).get("startrow")
        return 200, fixture("successfactors_search_p2.html" if start == "3" else "successfactors_search_p1.html")

    fetcher = FakeFetcher(handler)
    postings = ADAPTER.search(fetcher, "jobs.mcdonalds.com", "McDonald's", ("cloud",))

    assert len(fetcher.calls) == 2
    first, second = (query_of(url) for _, url, _ in fetcher.calls)
    assert fetcher.calls[0][1].startswith("https://jobs.mcdonalds.com/search/?")
    assert first == {"q": "cloud", "optionsFacetsDD_country": "US", "locale": "en_US"}
    assert second["startrow"] == "3"

    assert [p.title for p in postings] == [
        "Cloud Engineer III",
        "Platform Architect, Cloud Security",
        "Data Engineer III",
        "Cybersecurity Architect",
        "Director, Tech Deployment",
    ]
    top = postings[0]
    assert top.source == "successfactors"
    assert top.board == "jobs.mcdonalds.com"
    assert top.company == "McDonald's"
    assert top.location == "Chicago, IL, US, 60607"
    assert top.url == "https://jobs.mcdonalds.com/job/Chicago-Cloud-Engineer-III-IL-60607/1408247300/"
    assert top.provider_id == "successfactors:jobs.mcdonalds.com:1408247300"
    assert isinstance(top.age_days, int)
    # the search row and a captured URL dedupe to the same key
    captured = match("https://jobs.mcdonalds.com/job/whatever/1408247300/")
    assert captured.provider_id == top.provider_id


def test_search_ignores_the_recent_jobs_shown_under_no_results():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("successfactors_search_noresults.html")))
    assert ADAPTER.search(fetcher, "jobs.mcdonalds.com", "McDonald's", ("kubernetes",)) == []
    assert len(fetcher.calls) == 1


def test_search_returns_none_when_the_board_fails():
    fetcher = FakeFetcher(lambda method, url: (403, "Access Denied"))
    assert ADAPTER.search(fetcher, "jobs.mcdonalds.com", "McDonald's", ("cloud",)) is None


def test_search_returns_none_when_the_page_is_not_a_search_page():
    fetcher = FakeFetcher(lambda method, url: (200, "<html><title>Just a moment...</title></html>"))
    assert ADAPTER.search(fetcher, "jobs.mcdonalds.com", "McDonald's", ("cloud",)) is None


def test_search_returns_none_for_a_board_that_is_not_a_host():
    fetcher = FakeFetcher(lambda method, url: (200, fixture("successfactors_search_p1.html")))
    assert ADAPTER.search(fetcher, "career4.successfactors.com/AcmeDefenseP", "Acme", ("cloud",)) is None
    assert fetcher.calls == []
