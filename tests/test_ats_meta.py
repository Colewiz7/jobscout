"""Meta careers job pages, against a trimmed real page and sitemap."""
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats import meta
from jobscout.descriptions import parse_description

ADAPTER = meta.ADAPTER
JOB = "1981338825764645"
MISSING = "1000000000000001"


class FakeFetcher:
    def __init__(self, routes):
        # url fragment -> (status, body); first match wins
        self.routes = routes
        self.calls = []

    def send(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for fragment, (status, body) in self.routes.items():
            if fragment in url:
                break
        else:
            status, body = 599, "no route"
        if isinstance(body, str):
            body = body.encode()
        return httpx.Response(status, content=body, request=httpx.Request(method, url))


def _match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.metacareers.com/profile/job_details/{JOB}/",
        f"https://www.metacareers.com/profile/job_details/{JOB}",
        f"https://www.metacareers.com/jobs/{JOB}/",
        f"https://metacareers.com/v2/jobs/{JOB}/?fbclid=abc",
        f"https://WWW.METACAREERS.COM/jobs/{JOB}/",
    ],
)
def test_match_posting_urls(url):
    ref = _match(url)
    assert ref is not None
    assert (ref.provider, ref.board, ref.job_id) == ("meta", "meta", JOB)
    assert ref.url == f"https://www.metacareers.com/profile/job_details/{JOB}/"


@pytest.mark.parametrize(
    "url",
    [
        f"https://metacareers.com.evil.com/jobs/{JOB}/",
        f"https://www.metacareers.com.evil.com/profile/job_details/{JOB}/",
        f"https://evilmetacareers.com/jobs/{JOB}/",
        f"https://careers.metacareers.com/jobs/{JOB}/",
        f"https://www.facebook.com/careers/v2/jobs/{JOB}/",
        "https://www.metacareers.com/jobs/",
        "https://www.metacareers.com/jobsearch?q=production%20engineer",
        "https://www.metacareers.com/jobs/abc/",
        f"https://www.metacareers.com/profile/job_details/{JOB}/apply",
    ],
)
def test_match_rejects_other_hosts_and_paths(url):
    assert _match(url) is None


def test_meta_has_no_search():
    assert ADAPTER.search is None


def test_detail_from_jsonld(fixture_text):
    fetcher = FakeFetcher({"/profile/job_details/": (200, fixture_text("ats/meta_job.html"))})
    detail = ADAPTER.detail(fetcher, _match(f"https://www.metacareers.com/jobs/{JOB}/"))

    assert fetcher.calls[0][:2] == ("GET", f"https://www.metacareers.com/profile/job_details/{JOB}/")
    assert len(fetcher.calls) == 1
    assert detail.company == "Meta"
    assert detail.title == "Supply Chain Capacity Engineer"
    assert detail.location == "Menlo Park, CA; Fremont, CA; Remote, US"
    assert detail.remote is True             # jobLocationType TELECOMMUTE
    assert detail.employment_type == "FULL_TIME"
    assert detail.deadline is None
    assert isinstance(detail.age_days, int) and detail.age_days >= 0

    body = detail.description_html
    assert body.startswith("<p>Meta is seeking a Planning &amp; Capacity Engineer")
    assert "<h3>Responsibilities</h3><ul><li>Own infrastructure supply chain planning" in body
    assert "<h3>Qualifications</h3>" in body
    assert body.count("<li>") == 6           # three of each, split on Meta's &nbsp;
    assert "&amp;nbsp;" not in body and "&nbsp;" not in body

    parsed = parse_description(body)
    keys = [section["key"] for section in parsed.sections]
    assert "responsibilities" in keys and "requirements" in keys


def test_detail_deadline_when_stated(fixture_text):
    page = fixture_text("ats/meta_job.html").replace(
        '"employmentType"', '"validThrough":"2026-12-31T23:59:00-08:00","employmentType"'
    )
    detail = ADAPTER.detail(FakeFetcher({"/profile/": (200, page)}), meta._ref(JOB))
    assert detail.deadline == "2026-12-31T23:59:00-08:00"


def test_detail_gone_when_page_and_sitemap_agree(fixture_text):
    fetcher = FakeFetcher({
        "/profile/job_details/": (200, fixture_text("ats/meta_missing.html")),
        "/jobsearch/sitemap.xml": (200, fixture_text("ats/meta_sitemap.xml")),
    })
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, meta._ref(MISSING))
    assert [call[1] for call in fetcher.calls] == [
        f"https://www.metacareers.com/profile/job_details/{MISSING}/",
        meta.SITEMAP,
    ]


def test_detail_not_gone_while_sitemap_still_lists_it(fixture_text):
    sitemap = fixture_text("ats/meta_sitemap.xml").replace(JOB, MISSING)
    fetcher = FakeFetcher({
        "/profile/job_details/": (200, fixture_text("ats/meta_missing.html")),
        "/jobsearch/sitemap.xml": (200, sitemap),
    })
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, meta._ref(MISSING))


def test_detail_not_gone_when_sitemap_fails(fixture_text):
    for status in (403, 404):
        fetcher = FakeFetcher({
            "/profile/job_details/": (200, fixture_text("ats/meta_missing.html")),
            "/jobsearch/sitemap.xml": (status, "nope"),
        })
        with pytest.raises(Unavailable):
            ADAPTER.detail(fetcher, meta._ref(MISSING))


def test_detail_not_gone_when_sitemap_is_empty(fixture_text):
    fetcher = FakeFetcher({
        "/profile/job_details/": (200, fixture_text("ats/meta_missing.html")),
        "/jobsearch/sitemap.xml": (200, "<html>maintenance</html>"),
    })
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, meta._ref(MISSING))


def test_detail_unrecognised_page_is_unavailable_without_asking_sitemap():
    fetcher = FakeFetcher({"/profile/": (200, "<html><title>Log in</title></html>")})
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, meta._ref(JOB))
    assert len(fetcher.calls) == 1


def test_detail_shell_for_another_id_is_unavailable(fixture_text):
    fetcher = FakeFetcher({"/profile/": (200, fixture_text("ats/meta_missing.html"))})
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, meta._ref(JOB))


def test_detail_gone_on_404():
    with pytest.raises(Gone):
        ADAPTER.detail(FakeFetcher({"/profile/": (404, "")}), meta._ref(JOB))


def test_detail_unavailable_on_403():
    with pytest.raises(Unavailable) as caught:
        ADAPTER.detail(FakeFetcher({"/profile/": (403, "denied")}), meta._ref(JOB))
    assert caught.value.status == 403


def test_detail_posting_round_trip(fixture_text):
    fetcher = FakeFetcher({"/profile/": (200, fixture_text("ats/meta_job.html"))})
    posting = ADAPTER.detail(fetcher, meta._ref(JOB)).posting()
    assert posting.source == "meta" and posting.provider_id == f"meta:meta:{JOB}"
    assert posting.remote is True and posting.employment_type == "FULL_TIME"
