"""Career sites read from a jobs sitemap and JobPosting, against Gulfstream's pages."""
import pathlib
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable, careersite

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
JOB = "https://careers.gulfstream.com/en/jobs/237680/operations-engineer-spec-i/"


class Fetcher:
    def __init__(self, answers):
        self.answers = answers      # url -> (status, body)
        self.urls = []

    def send(self, method, url, **kwargs):
        self.urls.append(url)
        status, body = self.answers.get(url, (404, "not found"))
        return httpx.Response(status, text=body, request=httpx.Request(method, url))


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(careersite, "PAUSE", 0)


def match(url):
    return careersite.ADAPTER.match(urllib.parse.urlsplit(url))


def test_matches_only_listed_career_sites():
    ref = match(JOB)
    assert (ref.provider, ref.board, ref.job_id) == ("careersite", "careers.gulfstream.com", "237680")
    assert ref.url == JOB and ref.provider_id == "careersite:careers.gulfstream.com:237680"
    assert match("https://careers.gulfstream.com/jobs/237680/").job_id == "237680"
    for other in (
        "https://careers.gulfstream.com.evil.com/en/jobs/237680/x/",
        "https://evil.careers.gulfstream.com/en/jobs/237680/x/",
        "https://careers.gulfstream.com/en/search/?q=engineer",
        "https://careers.gulfstream.com/en/jobs/",
        "https://other.example.com/en/jobs/237680/x/",
    ):
        assert match(other) is None, other


def test_detail_reads_the_job_posting():
    fetcher = Fetcher({JOB: (200, (FIXTURES / "careersite_gulfstream_job.html").read_text())})
    detail = careersite.ADAPTER.detail(fetcher, match(JOB))
    assert detail.company == "Gulfstream"
    assert detail.title == "Operations Engineer Spec I"
    assert detail.location == "Savannah, GA, US"
    assert detail.employment_type == "FULL_TIME"
    assert detail.age_days is not None and detail.description_html


def test_a_missing_job_is_gone_and_a_page_without_markup_is_unavailable():
    with pytest.raises(Gone):
        careersite.ADAPTER.detail(Fetcher({}), match(JOB))
    with pytest.raises(Unavailable):
        careersite.ADAPTER.detail(Fetcher({JOB: (200, "<html><h1>Maintenance</h1></html>")}), match(JOB))
    with pytest.raises(Unavailable):
        careersite.ADAPTER.detail(Fetcher({JOB: (403, "blocked")}), match(JOB))


def test_an_expired_posting_is_gone():
    page = (FIXTURES / "careersite_gulfstream_job.html").read_text().replace('"datePosted"', '"validThrough": "2020-01-01", "datePosted"', 1)
    with pytest.raises(Gone):
        careersite.ADAPTER.detail(Fetcher({JOB: (200, page)}), match(JOB))


def test_search_opens_only_slugs_that_name_a_term():
    fetcher = Fetcher({
        "https://careers.gulfstream.com/en/jobs/sitemap.xml": (200, (FIXTURES / "careersite_gulfstream_sitemap.xml").read_text()),
        JOB: (200, (FIXTURES / "careersite_gulfstream_job.html").read_text()),
    })
    rows = careersite.ADAPTER.search(fetcher, "careers.gulfstream.com", "Gulfstream", ("operations engineer", "intern", "co-op"))
    assert [r.title for r in rows] == ["Operations Engineer Spec I"]
    assert rows[0].provider_id == "careersite:careers.gulfstream.com:237680" and rows[0].board == "careers.gulfstream.com"
    # "intern" did not open .../airworthiness-specialist-international-certification/
    assert fetcher.urls == ["https://careers.gulfstream.com/en/jobs/sitemap.xml", JOB]


def test_slug_matching_is_by_whole_words():
    assert careersite._slug_matches("it-co-op-student", ("coop",))
    assert careersite._slug_matches("site-reliability-engineer", ("site reliability",))
    assert not careersite._slug_matches("international-certification", ("intern",))
    assert not careersite._slug_matches("reliability-analyst", ("site reliability",))


def test_search_is_absent_when_the_sitemap_fails_or_the_board_is_unknown():
    assert careersite.ADAPTER.search(Fetcher({}), "careers.gulfstream.com", "Gulfstream", ("engineer",)) is None
    assert careersite.ADAPTER.search(Fetcher({}), "careers.example.com", "X", ("engineer",)) is None
    assert careersite.ADAPTER.search(Fetcher({}), "careers.gulfstream.com", "Gulfstream", ()) is None
