"""Oracle Recruiting Cloud adapter, against trimmed real responses."""
import json
import urllib.parse

import httpx
import pytest

from jobscout.ats import Gone, Unavailable
from jobscout.ats import oracle
from jobscout.ats.oracle import ADAPTER
from jobscout.descriptions import parse_description


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
    monkeypatch.setattr(oracle, "PAUSE", 0)


def _match(url):
    return ADAPTER.match(urllib.parse.urlsplit(url))


AMEX_POD = "egug.fa.us2.oraclecloud.com"
JPMC = "jpmc.fa.oraclecloud.com/CX_1001"
PROBE = "facetsList=LOCATIONS,limit=1"
QUERY = "keyword="


# --- match -----------------------------------------------------------------

@pytest.mark.parametrize("url, board, job_id, view", [
    (
        "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26016352",
        f"{AMEX_POD}/CX_1", "26016352",
        "https://careers.americanexpress.com/en/sites/CX_1/job/26016352",
    ),
    (
        "https://careers.americanexpress.com/en/sites/CX_1/job/26016352?utm_medium=jobshare",
        f"{AMEX_POD}/CX_1", "26016352",
        "https://careers.americanexpress.com/en/sites/CX_1/job/26016352",
    ),
    (
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210783874",
        JPMC, "210783874",
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210783874",
    ),
    (
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en-US/sites/CX_1001/requisitions/preview/210783874/?keyword=sre",
        JPMC, "210783874",
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210783874",
    ),
    (
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210783874/apply/email",
        JPMC, "210783874",
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210783874",
    ),
    (
        "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LateralHiring/job/156821",
        "hdpc.fa.us2.oraclecloud.com/LateralHiring", "156821",
        "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LateralHiring/job/156821",
    ),
    (
        "https://fa-abcd-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/fr_CA/sites/CX_2/jobs/preview/R-1234",
        "fa-abcd-saasfaprod1.fa.ocs.oraclecloud.com/CX_2", "R-1234",
        "https://fa-abcd-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/job/R-1234",
    ),
])
def test_match_positive(url, board, job_id, view):
    ref = _match(url)
    assert ref is not None
    assert ref.provider == "oracle"
    assert ref.board == board
    assert ref.job_id == job_id
    assert ref.url == view


def test_custom_domain_and_pod_share_a_dedupe_key():
    a = _match("https://careers.americanexpress.com/en/sites/CX_1/job/26016352")
    b = _match("https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26016352")
    assert a.provider_id == b.provider_id == f"oracle:{AMEX_POD}:26016352"


@pytest.mark.parametrize("url", [
    "https://egug.fa.us2.oraclecloud.com.evil.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    "https://oraclecloud.com.evil.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    "https://evil-oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    "https://evil.fa.us2.oraclecloud.co/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    "https://egug.fa.us2.oraclecloud.com@evil.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    "https://careers.americanexpress.com.evil.com/en/sites/CX_1/job/1",
    "https://evil.com/careers.americanexpress.com/en/sites/CX_1/job/1",
    # oraclecloud.com, but not a Fusion pod
    "https://objectstorage.us-ashburn-1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    "https://oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    # a pod, but not a Candidate Experience posting
    "https://egug.fa.us2.oraclecloud.com/en/sites/CX_1/job/1",
    "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs",
    "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/",
    "https://egug.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
    "https://careers.americanexpress.com/en/sites/CX_1/jobs?keyword=sre",
])
def test_match_negative(url):
    assert _match(url) is None


# --- detail ----------------------------------------------------------------

def test_detail_amex(fixture_json):
    ref = _match("https://careers.americanexpress.com/en/sites/CX_1/job/26016352")
    fetcher = FakeFetcher([("recruitingCEJobRequisitionDetails", 200, fixture_json("ats/oracle_amex_detail.json"))])
    detail = ADAPTER.detail(fetcher, ref)

    asked = fetcher.asked[0]
    assert asked.startswith(f"https://{AMEX_POD}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?")
    assert "finder=ById;Id=%2226016352%22,siteNumber=CX_1" in asked
    assert detail.company == "American Express"
    assert detail.title == "Site Reliability Engineer III"
    assert detail.location == "Phoenix, AZ, United States"
    assert detail.remote is False                    # ORA_HYBRID
    assert detail.employment_type == "FULL_TIME"     # JobSchedule "Full time"
    assert detail.deadline == "2026-12-01T08:00:00+00:00"
    assert isinstance(detail.age_days, int) and detail.age_days >= 0

    html = detail.description_html
    assert "Joining Amex Tech" in html
    assert "<h3>Responsibilities</h3>" in html and "<h3>Qualifications</h3>" in html
    assert "Salary Range: $103750 - $174750" in html
    parsed = parse_description(html, provider_deadline=detail.deadline)
    keys = [s["key"] for s in parsed.sections]
    assert "responsibilities" in keys and "requirements" in keys


def test_detail_remote_and_secondary_locations(fixture_json):
    payload = fixture_json("ats/oracle_amex_detail.json")
    record = payload["items"][0]
    record["WorkplaceTypeCode"] = "ORA_REMOTE"
    record["JobType"] = "Internship"
    record["secondaryLocations"] = [{"Name": "New York, NY, United States"}, {"Name": "Phoenix, AZ, United States"}]
    ref = _match("https://careers.americanexpress.com/en/sites/CX_1/job/26016352")
    detail = ADAPTER.detail(FakeFetcher([("Details", 200, payload)]), ref)
    assert detail.remote is True
    assert detail.employment_type == "INTERN"
    assert detail.location == "Phoenix, AZ, United States; New York, NY, United States"


def test_detail_gone_on_empty_items(fixture_json):
    ref = _match("https://careers.americanexpress.com/en/sites/CX_1/job/99999901")
    fetcher = FakeFetcher([("Details", 200, fixture_json("ats/oracle_empty_detail.json"))])
    with pytest.raises(Gone):
        ADAPTER.detail(fetcher, ref)


def test_detail_gone_on_404():
    ref = _match("https://careers.americanexpress.com/en/sites/CX_1/job/26016352")
    with pytest.raises(Gone):
        ADAPTER.detail(FakeFetcher([("Details", 404, {"title": "Not Found"})]), ref)


def test_detail_unavailable_on_403():
    ref = _match("https://careers.americanexpress.com/en/sites/CX_1/job/26016352")
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher([("Details", 403, b"Forbidden")]), ref)


def test_detail_unavailable_on_wrong_id(fixture_json):
    ref = _match("https://careers.americanexpress.com/en/sites/CX_1/job/11111111")
    with pytest.raises(Unavailable):
        ADAPTER.detail(FakeFetcher([("Details", 200, fixture_json("ats/oracle_amex_detail.json"))]), ref)


def test_detail_unknown_pod_name_falls_back():
    ref = _match("https://acme.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/7")
    payload = {"items": [{"Id": "7", "Title": "Cloud Engineer", "PrimaryLocation": "Austin, TX, United States"}]}
    detail = ADAPTER.detail(FakeFetcher([("Details", 200, payload)]), ref)
    assert detail.company == "Acme"
    assert detail.description_html is None and detail.deadline is None


# --- search ----------------------------------------------------------------

def test_search_probes_for_us_then_queries_each_term(fixture_json):
    fetcher = FakeFetcher([
        (PROBE, 200, fixture_json("ats/oracle_jpmc_probe.json")),
        (QUERY, 200, fixture_json("ats/oracle_jpmc_search.json")),
    ])
    rows = ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("site reliability", "infrastructure"))

    assert len(fetcher.asked) == 3                 # probe, then one page per term
    assert PROBE in fetcher.asked[0]
    first = fetcher.asked[1]
    assert "finder=findReqs;siteNumber=CX_1001," in first
    assert "keyword=%22site%20reliability%22" in first
    assert "selectedLocationsFacet=300000000289738" in first
    assert "offset=0" in first and "limit=25" in first
    assert "expand=requisitionList.secondaryLocations" in first
    assert "keyword=%22infrastructure%22" in fetcher.asked[2]

    assert len(rows) == 3                           # both terms answer the same three
    by_id = {r.provider_id: r for r in rows}
    lead = by_id["oracle:jpmc.fa.oraclecloud.com:210783874"]
    assert lead.source == "oracle" and lead.board == JPMC and lead.company == "JPMorgan Chase"
    assert lead.title == "Lead Infrastructure Engineer - Network Connectivity Services"
    assert lead.location == "Seattle, WA, United States; Plano, TX, United States"
    assert lead.url == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210783874"
    assert isinstance(lead.age_days, int)
    assert lead.provider_id == _match(lead.url).provider_id


def test_search_without_a_us_facet_searches_unfiltered(fixture_json):
    probe = fixture_json("ats/oracle_jpmc_probe.json")
    probe["items"][0]["locationsFacet"] = [{"Id": 1, "Name": "India", "TotalCount": 5}]
    fetcher = FakeFetcher([(PROBE, 200, probe), (QUERY, 200, fixture_json("ats/oracle_jpmc_search.json"))])
    rows = ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("cloud",))
    assert rows and "selectedLocationsFacet" not in fetcher.asked[1]


def test_search_pages_and_stops_at_cap():
    def page(url):
        offset = int(url.split("offset=")[1].split(",")[0])
        return {"items": [{"TotalJobsCount": 5000, "requisitionList": [
            {"Id": str(offset + i), "Title": "Cloud", "PrimaryLocation": "Austin, TX, United States"}
            for i in range(oracle.PAGE)
        ]}]}

    fetcher = FakeFetcher([(PROBE, 200, {"items": [{"locationsFacet": []}]}), (QUERY, 200, page)])
    rows = ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("cloud",))
    assert len(fetcher.asked) == 1 + oracle.MAX_PAGES
    assert len(rows) == oracle.MAX_PAGES * oracle.PAGE


def test_search_skips_postings_past_their_end_date(fixture_json):
    payload = fixture_json("ats/oracle_jpmc_search.json")
    payload["items"][0]["requisitionList"][0]["PostingEndDate"] = "2001-01-01"
    fetcher = FakeFetcher([(PROBE, 200, fixture_json("ats/oracle_jpmc_probe.json")), (QUERY, 200, payload)])
    rows = ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("cloud",))
    assert len(rows) == 2


def test_search_board_on_custom_host_resolves_to_pod(fixture_json):
    fetcher = FakeFetcher([
        (PROBE, 200, fixture_json("ats/oracle_jpmc_probe.json")),
        (QUERY, 200, fixture_json("ats/oracle_jpmc_search.json")),
    ])
    rows = ADAPTER.search(fetcher, "careers.americanexpress.com/CX_1", "careers.americanexpress.com/CX_1", ("cloud",))
    assert fetcher.asked[0].startswith(f"https://{AMEX_POD}/hcmRestApi/")
    assert rows[0].company == "American Express"


def test_search_strips_finder_syntax_from_terms(fixture_json):
    fetcher = FakeFetcher([
        (PROBE, 200, fixture_json("ats/oracle_jpmc_probe.json")),
        (QUERY, 200, fixture_json("ats/oracle_jpmc_search.json")),
    ])
    ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ('cloud",siteNumber=CX_1;x',))
    assert "keyword=%22cloud%20siteNumber%20CX_1%20x%22" in fetcher.asked[1]


def test_search_none_when_board_fails():
    fetcher = FakeFetcher([(PROBE, 403, b"Forbidden")])
    assert ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("cloud",)) is None
    assert len(fetcher.asked) == 1


def test_search_none_when_a_query_fails(fixture_json):
    fetcher = FakeFetcher([
        (PROBE, 200, fixture_json("ats/oracle_jpmc_probe.json")),
        ("keyword=%22cloud%22", 200, fixture_json("ats/oracle_jpmc_search.json")),
        (QUERY, 503, b"busy"),
    ])
    assert ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("cloud", "devops")) is None


def test_search_none_on_unexpected_shape():
    fetcher = FakeFetcher([(PROBE, 200, {"items": []})])
    assert ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("cloud",)) is None


def test_search_empty_board_is_an_answer(fixture_json):
    fetcher = FakeFetcher([
        (PROBE, 200, fixture_json("ats/oracle_jpmc_probe.json")),
        (QUERY, 200, {"items": [{"TotalJobsCount": 0, "requisitionList": []}]}),
    ])
    assert ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ("cloud",)) == []


@pytest.mark.parametrize("board", ["", "jpmc", "evil.com/CX_1", "jpmc.fa.oraclecloud.com.evil.com/CX_1", "jpmc.fa.oraclecloud.com/", "jpmc.fa.oraclecloud.com/CX 1"])
def test_search_none_for_a_board_it_does_not_own(board):
    fetcher = FakeFetcher([])
    assert ADAPTER.search(fetcher, board, "X", ("cloud",)) is None
    assert fetcher.asked == []


def test_search_none_without_terms():
    fetcher = FakeFetcher([])
    assert ADAPTER.search(fetcher, JPMC, "JPMorgan Chase", ()) is None
    assert fetcher.asked == []
