"""iCIMS adapter against trimmed live responses, with no network."""
import datetime
import pathlib
import urllib.parse

import httpx
import pytest
from bs4 import BeautifulSoup

from jobscout.ats import Gone, Ref, Unavailable, icims

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
ADAPTER = icims.ADAPTER


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeFetcher:
    """Answers by URL prefix, first match wins, and records every request."""

    def __init__(self, *routes):
        self.routes = routes
        self.urls = []

    def send(self, method, url, **kwargs):
        self.urls.append(url)
        for prefix, status, body in self.routes:
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return httpx.Response(status, content=body, request=httpx.Request(method, url))
        raise AssertionError(f"unexpected request to {url}")


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(icims, "REQUEST_PAUSE", 0)


def match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


def days_since(iso: str) -> int:
    stamp = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - stamp).days


GDMS_JOB = "https://careers-gdms.icims.com/jobs/77948/job?in_iframe=1"


# --- match ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://careers-gdms.icims.com/jobs/77948/devops-engineer/job",
        "https://careers-gdms.icims.com/jobs/77948/job?mobile=true&needsRedirect=false&utm_source=Simplify",
        "https://CAREERS-GDMS.icims.com/jobs/77948/devops-engineer/job?in_iframe=1",
        "http://careers-gdms.icims.com/jobs/77948/login",
        "https://careers-gdms.icims.com./jobs/77948",
    ],
)
def test_match_portal_url_forms(url):
    ref = match(url)
    assert ref == Ref(
        provider="icims",
        board="gdms",
        job_id="77948",
        url="https://careers-gdms.icims.com/jobs/77948/job",
    )
    assert ref.provider_id == "icims:gdms:77948"


def test_match_unknown_portal_keeps_its_own_subdomain():
    ref = match("https://careers-acme.icims.com/jobs/1234/site-reliability-engineer/job")
    assert ref.board == "careers-acme"
    assert ref.job_id == "1234"
    assert ref.url == "https://careers-acme.icims.com/jobs/1234/job"


def test_every_form_of_a_career_site_job_is_one_posting():
    # AMD posts US jobs on careers-amd, the rest on global-external-amd, and
    # all of them on careers.amd.com. Each link must dedupe to one row.
    forms = [
        "https://careers.amd.com/jobs/97759",
        "https://careers.amd.com/careers-home/jobs/97759?lang=en-us&icims=1",
        "https://careers-amd.icims.com/jobs/97759/devops-platform-engineer/job",
        "https://global-external-amd.icims.com/jobs/97759/login",
    ]
    refs = {match(url) for url in forms}
    assert refs == {Ref("icims", "amd", "97759", "https://careers.amd.com/jobs/97759")}


def test_match_regional_portal_and_site_agree():
    portal = match("https://poland-careers-aon.icims.com/jobs/110709/tech-support-engineer/job")
    site = match("https://jobs.aon.com/jobs/110709")
    assert portal == site
    assert site.provider_id == "icims:aon:110709"


@pytest.mark.parametrize(
    "url",
    [
        "https://icims.com.evil.com/jobs/77948/job",
        "https://careers-gdms.icims.com.evil.com/jobs/77948/job",
        "https://evilicims.com/jobs/77948/job",
        "https://careers-gdmsicims.com/jobs/77948/job",
        "https://icims.com/jobs/77948/job",
        "https://www.icims.com/jobs/77948/job",
        "https://cdn02.icims.com/jobs/77948/job",
        "https://deep.careers-gdms.icims.com/jobs/77948/job",
        "https://careers-gdms.icims.com@evil.com/jobs/77948/job",
        "https://evil.com@careers-gdms.icims.com/jobs/77948/job",
        "https://evil.com\\@careers-gdms.icims.com/jobs/77948/job",
        "https://evil.com/careers-gdms.icims.com/jobs/77948/job",
        "ftp://careers-gdms.icims.com/jobs/77948/job",
        "https://careers-gdms.icims.com/jobs/search?searchKeyword=devops",
        "https://careers-gdms.icims.com/jobs/intro",
        "https://careers-gdms.icims.com/jobs/77948abc/job",
        "https://careers.amd.com.evil.com/jobs/92826",
        "https://evilcareers.amd.com/jobs/92826",
        "https://careers.amd.com/jobs",
        "https://careers.amd.com/jobs/devops-engineer",
        "https://boards.greenhouse.io/acme/jobs/123",
    ],
)
def test_match_rejects_lookalikes_and_non_postings(url):
    assert match(url) is None


# --- detail --------------------------------------------------------------


def test_detail_reads_the_portal_job_page():
    fetcher = FakeFetcher((GDMS_JOB, 200, fixture("icims_portal_job.html")))
    detail = ADAPTER.detail(fetcher, match("https://careers-gdms.icims.com/jobs/77948/devops-engineer/job"))

    assert fetcher.urls == [GDMS_JOB]
    assert detail.company == "General Dynamics Mission Systems"
    assert detail.title == "DevOps Engineer"
    assert detail.location == "Bloomington, MN, US"
    # JSON-LD says OTHER, the portal's own field says Full Time.
    assert detail.employment_type == "FULL_TIME"
    assert detail.remote is False
    # validThrough is iCIMS's automatic one-year default, not a deadline.
    assert detail.deadline is None
    assert detail.age_days == days_since("2026-11-09T05:00:00.000Z")
    assert "Basic Qualifications" in detail.description_html
    assert detail.posting().provider_id == "icims:gdms:77948"


def test_detail_falls_back_to_the_page_html_without_jsonld():
    url = "https://careers-gdms.icims.com/jobs/74848/job?in_iframe=1"
    fetcher = FakeFetcher((url, 200, fixture("icims_portal_job_html_only.html")))
    detail = ADAPTER.detail(fetcher, match("https://careers-gdms.icims.com/jobs/74848/job"))

    assert detail.title == "Payload Control System - Software/Systems Engineering Intern (Summer 2027)"
    assert detail.location == "Manassas, VA, US"
    assert detail.employment_type == "INTERN"
    assert detail.age_days == days_since("2026-09-09T12:13:00")
    assert "Basic Qualifications" in detail.description_html
    assert "iCIMS_InfoMsg_Job" in detail.description_html


def test_detail_reads_the_career_site_page():
    url = "https://careers.amd.com/jobs/92826"
    fetcher = FakeFetcher((url, 200, fixture("icims_site_job.html")))
    detail = ADAPTER.detail(fetcher, match("https://global-external-amd.icims.com/jobs/92826/login"))

    assert fetcher.urls == [url]
    assert detail.company == "AMD"
    assert detail.title == "DevOps Platform Engineer"
    assert detail.location == "Hyderabad, Telangana, India"
    assert detail.employment_type == "FULL_TIME"
    assert detail.deadline is None
    assert "ADVANCE YOUR CAREER" in detail.description_html


def test_detail_deadline_when_valid_through_was_set_by_hand():
    page = fixture("icims_portal_job.html").replace(
        b'"validThrough": "2027-11-09T05:00:00.000Z"', b'"validThrough": "2026-12-15T05:00:00.000Z"'
    )
    detail = ADAPTER.detail(FakeFetcher((GDMS_JOB, 200, page)), match(GDMS_JOB))
    assert detail.deadline == "2026-12-15"


def test_detail_remote_from_telecommute_location_type():
    page = fixture("icims_portal_job.html").replace(
        b'"@type": "JobPosting"', b'"@type": "JobPosting", "jobLocationType": "TELECOMMUTE"'
    )
    detail = ADAPTER.detail(FakeFetcher((GDMS_JOB, 200, page)), match(GDMS_JOB))
    assert detail.remote is True


@pytest.mark.parametrize(
    "url, status, body",
    [
        ("https://careers.amd.com/jobs/92826", 404, b"<html>not found</html>"),
        (GDMS_JOB, 404, b""),
        (GDMS_JOB, 410, fixture("icims_portal_gone.html")),
        # iCIMS's own "no longer open" page, answered with a 200
        (GDMS_JOB, 200, fixture("icims_portal_gone.html")),
    ],
)
def test_detail_gone(url, status, body):
    ref = match(url.split("?")[0])
    with pytest.raises(Gone):
        ADAPTER.detail(FakeFetcher((url, status, body)), ref)


def test_detail_unavailable_on_403():
    with pytest.raises(Unavailable) as caught:
        ADAPTER.detail(FakeFetcher((GDMS_JOB, 403, b"blocked")), match(GDMS_JOB))
    assert caught.value.status == 403


@pytest.mark.parametrize("status", [200, 410])
def test_detail_redirect_stub_is_not_gone(status):
    # A portal fronted by a career site answers this for any job it does not
    # carry, closed or merely posted elsewhere, so it proves nothing.
    url = "https://careers-acme.icims.com/jobs/50000/job?in_iframe=1"
    fetcher = FakeFetcher((url, status, fixture("icims_portal_redirect.html")))
    with pytest.raises(Unavailable) as caught:
        ADAPTER.detail(fetcher, match("https://careers-acme.icims.com/jobs/50000/job"))
    assert "careers.jhuapl.edu" in str(caught.value)


def test_detail_unavailable_when_the_page_is_not_a_job():
    fetcher = FakeFetcher((GDMS_JOB, 200, b"<html><body>Maintenance</body></html>"))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match(GDMS_JOB))


def test_detail_unavailable_when_the_page_names_another_job():
    url = "https://careers-gdms.icims.com/jobs/12345/job?in_iframe=1"
    fetcher = FakeFetcher((url, 200, fixture("icims_portal_job.html")))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match("https://careers-gdms.icims.com/jobs/12345/job"))


def test_detail_unavailable_when_the_fetcher_gives_up():
    fetcher = FakeFetcher((GDMS_JOB, 0, RuntimeError("giving up after 90s")))
    with pytest.raises(Unavailable):
        ADAPTER.detail(fetcher, match(GDMS_JOB))


# --- search: classic portal ---------------------------------------------

PORTAL_SEARCH = "https://careers-gdms.icims.com/jobs/search?"


def test_search_portal_reads_cards_and_follows_the_pager():
    # Page two answers with the same markup, whose pager has no page three.
    fetcher = FakeFetcher((PORTAL_SEARCH, 200, fixture("icims_portal_search.html")))
    rows = ADAPTER.search(fetcher, "gdms", "gdms", ("devops",))

    assert len(fetcher.urls) == 2
    queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query) for u in fetcher.urls]
    assert [q["pr"] for q in queries] == [["0"], ["1"]]
    assert all(q["searchKeyword"] == ["devops"] and q["in_iframe"] == ["1"] for q in queries)

    by_id = {row.provider_id: row for row in rows}
    assert list(by_id) == ["icims:gdms:77948", "icims:gdms:75771", "icims:gdms:75769"]
    devops = by_id["icims:gdms:77948"]
    assert devops.source == "icims"
    assert devops.board == "gdms"
    assert devops.company == "General Dynamics Mission Systems"
    assert devops.title == "DevOps Engineer"
    assert devops.location == "Bloomington, MN, US"
    assert devops.url == "https://careers-gdms.icims.com/jobs/77948/job"
    assert devops.remote is False
    assert devops.age_days == days_since("2026-11-09T18:00:00")
    assert by_id["icims:gdms:75771"].location == "Telework, US"
    assert by_id["icims:gdms:75771"].remote is True
    # A search result and a captured link for the same job are one row.
    assert match(devops.url).provider_id == devops.provider_id


def test_search_portal_keeps_the_configured_company_name():
    fetcher = FakeFetcher((PORTAL_SEARCH, 200, fixture("icims_portal_search.html")))
    rows = ADAPTER.search(fetcher, "gdms", "General Dynamics", ("devops",))
    assert {row.company for row in rows} == {"General Dynamics"}


def _cards_without_locations() -> bytes:
    soup = BeautifulSoup(fixture("icims_portal_search.html"), "html.parser")
    for tag in soup.select(".iCIMS_JobHeaderTag"):
        if "Location" in tag.get_text():
            tag.decompose()
    return str(soup).encode()


def test_search_portal_opens_job_pages_only_for_incomplete_cards(monkeypatch):
    monkeypatch.setattr(icims, "PORTAL_MAX_DETAILS", 2)
    fetcher = FakeFetcher(
        (PORTAL_SEARCH, 200, _cards_without_locations()),
        (GDMS_JOB, 200, fixture("icims_portal_job.html")),
        ("https://careers-gdms.icims.com/jobs/", 404, b""),
    )
    rows = ADAPTER.search(fetcher, "gdms", "gdms", ("devops",))

    opened = [u for u in fetcher.urls if "/jobs/search" not in u]
    assert opened == [GDMS_JOB, "https://careers-gdms.icims.com/jobs/75771/job?in_iframe=1"]
    by_id = {row.provider_id: row for row in rows}
    assert by_id["icims:gdms:77948"].location == "Bloomington, MN, US"
    # Past the cap, or unreadable, a card still counts with what it had.
    assert by_id["icims:gdms:75771"].location == ""
    assert by_id["icims:gdms:75769"].title == "DevOps Engineer Intern"


def test_search_portal_that_moved_to_a_career_site_did_not_answer():
    fetcher = FakeFetcher(
        ("https://careers-acme.icims.com/jobs/search?", 200, fixture("icims_portal_redirect.html"))
    )
    assert ADAPTER.search(fetcher, "careers-acme", "Acme", ("devops",)) is None


# --- search: career site -------------------------------------------------


def test_search_site_reads_the_api():
    fetcher = FakeFetcher(("https://careers.amd.com/api/jobs?", 200, fixture("icims_site_jobs.json")))
    rows = ADAPTER.search(fetcher, "amd", "amd", ("devops", "platform"))

    assert fetcher.urls == [
        "https://careers.amd.com/api/jobs?keywords=devops&page=1&limit=50",
        "https://careers.amd.com/api/jobs?keywords=platform&page=1&limit=50",
    ]
    assert [row.provider_id for row in rows] == ["icims:amd:92826", "icims:amd:97759", "icims:amd:94331"]
    first = rows[0]
    assert first.source == "icims"
    assert first.board == "amd"
    assert first.company == "AMD"
    assert first.title == "DevOps Platform Engineer"
    assert first.location == "Hyderabad, India"
    assert first.url == "https://careers.amd.com/jobs/92826"
    assert first.employment_type == "FULL_TIME"
    assert first.age_days == days_since("2026-10-13T15:19:00+00:00")
    # the US job lives on careers-amd, and a link to it still dedupes
    assert match("https://careers-amd.icims.com/jobs/97759/job").provider_id == rows[1].provider_id


@pytest.mark.parametrize(
    "board, prefix, status, body",
    [
        ("amd", "https://careers.amd.com/api/jobs?", 403, b"blocked"),
        ("amd", "https://careers.amd.com/api/jobs?", 404, b""),
        ("amd", "https://careers.amd.com/api/jobs?", 200, b"<html>not json</html>"),
        ("amd", "https://careers.amd.com/api/jobs?", 200, b'{"error": "nope"}'),
        ("gdms", PORTAL_SEARCH, 503, b""),
        ("gdms", PORTAL_SEARCH, 200, b"<html><body>Maintenance</body></html>"),
    ],
)
def test_search_returns_none_when_the_board_fails(board, prefix, status, body):
    assert ADAPTER.search(FakeFetcher((prefix, status, body)), board, board, ("devops",)) is None


def test_search_partial_failure_is_none():
    # One term answered and the next did not: a partial board would let the
    # close rule retire whatever the failed query would have found.
    class Flaky(FakeFetcher):
        def send(self, method, url, **kwargs):
            if "keywords=cloud" in url:
                self.urls.append(url)
                return httpx.Response(403, request=httpx.Request(method, url))
            return super().send(method, url, **kwargs)

    fetcher = Flaky(("https://careers.amd.com/api/jobs?", 200, fixture("icims_site_jobs.json")))
    assert ADAPTER.search(fetcher, "amd", "AMD", ("devops", "cloud")) is None


def test_search_lets_a_fetcher_that_gave_up_propagate():
    # The board scanner counts these to tell a dead network from one quiet board.
    fetcher = FakeFetcher(("https://careers.amd.com/api/jobs?", 0, RuntimeError("rate limited")))
    with pytest.raises(RuntimeError):
        ADAPTER.search(fetcher, "amd", "AMD", ("devops",))


def test_search_without_terms_or_with_a_bad_board_is_none():
    fetcher = FakeFetcher()
    assert ADAPTER.search(fetcher, "amd", "AMD", ()) is None
    assert ADAPTER.search(fetcher, "not a board", "Acme", ("devops",)) is None
    assert fetcher.urls == []


@pytest.mark.parametrize("board", ["careers-amd", "careers-amd.icims.com", "careers.amd.com"])
def test_search_accepts_other_names_for_a_known_company(board):
    fetcher = FakeFetcher(("https://careers.amd.com/api/jobs?", 200, fixture("icims_site_jobs.json")))
    rows = ADAPTER.search(fetcher, board, "AMD", ("devops",))
    # board stays as configured, for the close rule; the id stays canonical
    assert {row.board for row in rows} == {board}
    assert rows[0].provider_id == "icims:amd:92826"


def test_general_dynamics_units_on_icims_have_their_names():
    import urllib.parse

    from jobscout.ats import icims as module
    for url, board, name in (
        ("https://careers-gdeb.icims.com/jobs/123456/engineer/job", "gdeb", "General Dynamics Electric Boat"),
        ("https://careers-gdbiw.icims.com/jobs/123456/engineer/job", "gdbiw", "General Dynamics Bath Iron Works"),
        ("https://careers-gd-ots.icims.com/jobs/123456/engineer/job", "ots", "General Dynamics OTS"),
    ):
        ref = module.ADAPTER.match(urllib.parse.urlsplit(url))
        assert ref.board == board and ref.provider_id == f"icims:{board}:123456"
        assert module.COMPANIES[board].name == name
