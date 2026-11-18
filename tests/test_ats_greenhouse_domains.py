"""Greenhouse boards on company domains, against trimmed real board API answers."""
import json
import pathlib
import urllib.parse

import httpx
import pytest

from jobscout import ats
from jobscout.ats import Gone, Unavailable
from jobscout.ats.greenhouse_domains import ADAPTER
from jobscout.models import provider_id_from_url
from jobscout.sources.boards import _greenhouse

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


def answer(status, body=""):
    return FakeFetcher(lambda method, url: (status, body))


def match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


@pytest.mark.parametrize(
    "url, board, job_id, canonical",
    [
        ("https://careers.datadoghq.com/detail/8203657/?gh_jid=8203657", "datadog", "8203657",
         "https://careers.datadoghq.com/detail/8203657/?gh_jid=8203657"),
        ("https://careers.datadoghq.com/detail/8203657/", "datadog", "8203657",
         "https://careers.datadoghq.com/detail/8203657/?gh_jid=8203657"),
        ("https://www.janestreet.com/join-jane-street/position/8868904002/", "janestreet", "8868904002",
         "https://www.janestreet.com/join-jane-street/apply/8868904002?gh_jid=8868904002"),
        ("https://janestreet.com/join-jane-street/apply/8868904002/?gh_jid=8868904002", "janestreet",
         "8868904002", "https://www.janestreet.com/join-jane-street/apply/8868904002?gh_jid=8868904002"),
        ("https://careers.withwaymo.com/jobs?gh_jid=8257205", "waymo", "8257205",
         "https://careers.withwaymo.com/jobs?gh_jid=8257205"),
        # where that link lands after Waymo's redirects
        ("https://careers.withwaymo.com/jobs/2027-summer-intern-bs-ms-phd-software-engineer-eval-data-infra"
         "-mountain-view-california-united-states?gh_jid=8257205", "waymo", "8257205",
         "https://careers.withwaymo.com/jobs?gh_jid=8257205"),
        ("https://careers.withwaymo.com/en-US/jobs/8257205", "waymo", "8257205",
         "https://careers.withwaymo.com/jobs?gh_jid=8257205"),
        ("https://www.zipline.com/open-roles/7895360003?gh_jid=7895360003", "flyzipline", "7895360003",
         "https://www.zipline.com/open-roles/7895360003?gh_jid=7895360003"),
        ("https://zipline.com/open-roles/7895360003", "flyzipline", "7895360003",
         "https://www.zipline.com/open-roles/7895360003?gh_jid=7895360003"),
    ],
)
def test_matches_verified_domains(url, board, job_id, canonical):
    ref = match(url)
    assert ref.provider == "greenhouse"
    assert (ref.board, ref.job_id, ref.url) == (board, job_id, canonical)
    assert ref.provider_id == f"greenhouse:{job_id}"


def test_ref_dedupes_with_the_board_scan():
    # The scout files the same job from the datadog board slug; both must
    # land on one row.
    scanned = _greenhouse(
        {"jobs": [{"id": 8203657, "title": "Research Science Intern (PhD)",
                   "absolute_url": "https://careers.datadoghq.com/detail/8203657/?gh_jid=8203657",
                   "location": {"name": "New York, New York, USA"}}]},
        "datadog", "Datadog",
    )[0]
    ref = match("https://careers.datadoghq.com/detail/8203657/")
    assert ref.provider_id == scanned.provider_id == scanned.dedupe_key
    assert ref.board == scanned.board
    for url in (
        "https://www.janestreet.com/join-jane-street/position/8868904002/",
        "https://careers.withwaymo.com/en-US/jobs/8257205",
        "https://zipline.com/open-roles/7895360003",
    ):
        ref = match(url)
        assert provider_id_from_url(ref.url) == ref.provider_id


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.datadoghq.com.evil.com/detail/8203657/?gh_jid=8203657",
        "https://evilcareers.datadoghq.com/detail/8203657/",
        "https://careers.withwaymo.com.attacker.io/jobs?gh_jid=8257205",
        "https://www.janestreet.com.example/join-jane-street/position/8868904002/",
        "https://notzipline.com/open-roles/7895360003",
        # Datadog's marketing site is not where its postings live
        "https://www.datadoghq.com/careers/?gh_jid=8203657",
        # plain Greenhouse hosts belong to the original Greenhouse support
        "https://boards.greenhouse.io/datadog/jobs/8203657",
        "https://careers.datadoghq.com/detail/abc/",
        "https://careers.datadoghq.com/",
        "https://www.zipline.com/careers?gh_jid=12ab",
        "https://www.janestreet.com/join-jane-street/open-roles/",
    ],
)
def test_rejects_other_hosts_and_paths(url):
    assert match(url) is None


def test_registry_never_claims_a_lookalike(monkeypatch):
    monkeypatch.setattr(ats, "_adapters", [ADAPTER])
    adapter, ref = ats.match("https://careers.withwaymo.com/jobs?gh_jid=8257205")
    assert adapter is ADAPTER and ref.provider_id == "greenhouse:8257205"
    assert ats.match("https://careers.withwaymo.com.evil.com/jobs?gh_jid=8257205") is None


def test_adapter_shape():
    assert ADAPTER.name == "greenhouse-domains"
    # The slug scan covers these boards already.
    assert ADAPTER.search is None


def test_detail_reads_the_board_api():
    fetcher = answer(200, fixture("greenhouse_domains_datadog.json"))
    ref = match("https://careers.datadoghq.com/detail/8203657/?gh_jid=8203657")
    detail = ADAPTER.detail(fetcher, ref)
    assert fetcher.calls[0][:2] == ("GET", "https://boards-api.greenhouse.io/v1/boards/datadog/jobs/8203657")
    assert detail.ref == ref
    assert detail.company == "Datadog"
    assert detail.title == "Research Science Intern (PhD)"
    assert detail.location == "New York, New York, USA; Pittsburgh, Pennsylvania, USA"
    # "Time Type: Full time" sits next to "Early Career Time Type: Internship"
    assert detail.employment_type == "INTERN"
    assert detail.remote is False
    assert detail.deadline is None
    assert isinstance(detail.age_days, int) and detail.age_days >= 60
    # the API's entity-escaped markup comes back as real HTML
    assert detail.description_html.startswith("<p><strong>Datadog AI Research")
    assert "&lt;" not in detail.description_html
    posting = detail.posting()
    assert (posting.source, posting.board, posting.dedupe_key) == ("greenhouse", "datadog", "greenhouse:8203657")


def test_detail_jane_street_employment_type():
    fetcher = answer(200, fixture("greenhouse_domains_janestreet.json"))
    detail = ADAPTER.detail(fetcher, match("https://www.janestreet.com/join-jane-street/position/8868904002/"))
    assert fetcher.calls[0][1] == "https://boards-api.greenhouse.io/v1/boards/janestreet/jobs/8868904002"
    assert detail.company == "Jane Street"
    assert detail.title == "Talent Development, Infrastructure and Operations Specialist"
    assert detail.location == "New York, New York, United States"
    assert detail.employment_type == "FULL_TIME"


def test_detail_reads_a_stated_deadline():
    payload = json.loads(fixture("greenhouse_domains_janestreet.json"))
    payload["application_deadline"] = "2026-12-15T23:59:00Z"
    detail = ADAPTER.detail(answer(200, json.dumps(payload)), match("https://janestreet.com/join-jane-street/position/8868904002/"))
    assert detail.deadline == "2026-12-15T23:59:00+00:00"


def test_detail_gone_on_404():
    with pytest.raises(Gone):
        ADAPTER.detail(answer(404, fixture("greenhouse_domains_404.json")),
                       match("https://careers.datadoghq.com/detail/1/?gh_jid=1"))


def test_detail_unavailable_on_403():
    with pytest.raises(Unavailable) as error:
        ADAPTER.detail(answer(403, "denied"), match("https://careers.datadoghq.com/detail/8203657/"))
    assert error.value.status == 403


def test_detail_unavailable_on_an_unexpected_body():
    with pytest.raises(Unavailable):
        ADAPTER.detail(answer(200, "{}"), match("https://careers.datadoghq.com/detail/8203657/"))
    with pytest.raises(Unavailable):
        ADAPTER.detail(answer(200, "<html>maintenance</html>"), match("https://careers.datadoghq.com/detail/8203657/"))
