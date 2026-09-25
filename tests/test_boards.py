import pytest
"""Board API normalisation, against trimmed real responses."""
from jobscout.sources.boards import (
    BOARD_TEMPLATES,
    PROVIDERS,
    _ashby,
    _greenhouse,
    _lever,
    fetch,
)


class FakeFetcher:
    """Answers configured URLs, 'None' for anything else, like a 404 board."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.asked = []

    def get_json(self, url):
        self.asked.append(url)
        for fragment, payload in self.payloads.items():
            if fragment in url:
                return payload
        return None


def test_greenhouse_normalisation(fixture_json):
    rows = _greenhouse(fixture_json("greenhouse_appian.json"), "appian", "Appian")
    assert rows
    first = rows[0]
    assert first.source == "greenhouse" and first.board == "appian"
    assert first.provider_id == f"greenhouse:{first.provider_id.split(':')[1]}"
    assert first.dedupe_key == first.provider_id
    assert first.location == "McLean, Virginia"


def test_ashby_keeps_secondary_locations(fixture_json):
    rows = _ashby(fixture_json("ashby_etched.json"), "Etched", "Etched")
    assert rows
    assert all(r.source == "ashby" for r in rows)
    assert all(r.dedupe_key.startswith("ashby:") for r in rows)


def test_ashby_skips_unlisted():
    payload = {"jobs": [
        {"id": "1", "title": "Cloud Intern", "location": "NY", "isListed": False,
         "jobUrl": "https://x", "isRemote": False},
        {"id": "2", "title": "Cloud Intern", "location": "NY", "isListed": True,
         "jobUrl": "https://y", "isRemote": True},
    ]}
    rows = _ashby(payload, "acme", "Acme")
    assert [r.provider_id for r in rows] == ["ashby:2"]
    assert rows[0].remote is True


def test_lever_normalisation(fixture_json):
    rows = _lever(fixture_json("lever_cesiumastro.json"), "CesiumAstro", "CesiumAstro")
    assert rows
    assert rows[0].source == "lever"
    assert rows[0].location  # comes out of categories.location


def test_fetch_reports_only_boards_that_answered(fixture_json):
    fetcher = FakeFetcher({"boards/appian/": fixture_json("greenhouse_appian.json")})
    postings, fetched = fetch(fetcher, {"greenhouse": ("appian", "doesnotexist")})
    assert fetched == {("greenhouse", "appian")}
    assert all(p.board == "appian" for p in postings)


def test_fetch_ignores_unknown_provider():
    fetcher = FakeFetcher({})
    postings, fetched = fetch(fetcher, {"taleo": ("acme",)})
    assert postings == [] and fetched == set()
    assert fetcher.asked == []


def test_provider_table():
    assert set(PROVIDERS) == {"greenhouse", "lever", "ashby", "workday", "amazon", "phenom"}
    # Workday is not addressable by one GET, so discovery cannot probe it.
    assert set(BOARD_TEMPLATES) == {"greenhouse", "lever", "ashby"}


def test_company_display_name_comes_from_the_map(fixture_json):
    """Board APIs never state the company, so without the map a push would read
    "morsecorpcoop" instead of "MORSE Corp"."""
    fetcher = FakeFetcher({"boards/appian/": fixture_json("greenhouse_appian.json")})
    postings, _ = fetch(fetcher, {"greenhouse": ("appian",)},
                        {"greenhouse": {"appian": "Appian Corporation"}})
    assert {p.company for p in postings} == {"Appian Corporation"}


def test_company_falls_back_to_the_slug_when_unmapped(fixture_json):
    fetcher = FakeFetcher({"boards/appian/": fixture_json("greenhouse_appian.json")})
    postings, _ = fetch(fetcher, {"greenhouse": ("appian",)}, {})
    assert {p.company for p in postings} == {"appian"}


# --- workday -------------------------------------------------------------

WD_PAGE = {
    "total": 2,
    "jobPostings": [
        {
            "title": "DevOps Engineer Co-op",
            "locationsText": "US-NY-Rochester",
            "postedOn": "Posted 3 Days Ago",
            "externalPath": "/job/Rochester-NY/DevOps-Engineer-Co-op_R1",
        },
        {
            "title": "Cloud Infrastructure Intern",
            "locationsText": "2 Locations",
            "postedOn": "Posted Yesterday",
            "externalPath": "/job/Buffalo-NY/Cloud-Infrastructure-Intern_R2",
        },
    ],
}


class FakePoster:
    """Records every POST and replies with the same page."""

    def __init__(self, page=WD_PAGE):
        self.page = page
        self.posts = []

    def post(self, url, content, headers):
        import json as _json

        self.posts.append((url, _json.loads(content)))
        return _Response(self.page)

    def get_json(self, url):  # pragma: no cover - workday never uses GET
        raise AssertionError("workday must not use GET")


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_workday_builds_postings_and_view_urls():
    poster = FakePoster()
    postings, fetched = fetch(
        poster, {"workday": ("mtb/wd5/MTB",)}, {"workday": {"mtb/wd5/MTB": "M&T Bank"}},
        ("devops",),
    )
    assert fetched == {("workday", "mtb/wd5/MTB")}
    assert {p.title for p in postings} == {
        "DevOps Engineer Co-op",
        "Cloud Infrastructure Intern",
    }
    first = next(p for p in postings if p.title == "DevOps Engineer Co-op")
    assert first.company == "M&T Bank"
    assert first.source == "workday"
    assert first.url == (
        "https://mtb.wd5.myworkdayjobs.com/en-US/MTB"
        "/job/Rochester-NY/DevOps-Engineer-Co-op_R1"
    )
    assert first.provider_id == "workday:mtb:/job/Rochester-NY/DevOps-Engineer-Co-op_R1"
    assert first.age_days == 3


def test_workday_posts_one_query_per_term_and_dedupes():
    poster = FakePoster()
    postings, _ = fetch(
        poster, {"workday": ("mtb/wd5/MTB",)}, None, ("devops", "infrastructure")
    )
    searches = [body["searchText"] for _, body in poster.posts]
    assert searches[:2] == ["devops", "infrastructure"]
    # The same two jobs come back for both terms and must not be duplicated.
    assert len(postings) == 2


def test_workday_rejects_a_malformed_spec():
    poster = FakePoster()
    postings, fetched = fetch(poster, {"workday": ("mtb",)}, None, ("devops",))
    assert postings == [] and fetched == set()
    assert poster.posts == []


@pytest.mark.parametrize("text,expected", [
    ("Posted Today", 0),
    ("Just Posted", 0),
    ("Posted Yesterday", 1),
    ("Posted 3 Days Ago", 3),
    ("Posted 30+ Days Ago", 30),
    ("", None),
])
def test_workday_relative_dates(text, expected):
    from jobscout.sources.boards import _workday_age

    assert _workday_age(text) is expected


class FlakyPoster(FakePoster):
    """Answers the first term and fails the second."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def post(self, url, content, headers):
        self.calls += 1
        if self.calls > 1:
            return None
        return super().post(url, content, headers)


def test_a_failed_query_makes_the_whole_board_absent():
    """A board is several requests now. A partial result must not look like a
    board that answered, or the close rule retires what the failure hid."""
    poster = FlakyPoster()
    postings, fetched = fetch(
        poster, {"workday": ("mtb/wd5/MTB",)}, None, ("devops", "infrastructure")
    )
    assert postings == []
    assert fetched == set()


# --- amazon ---------------------------------------------------------------

AMZ_PAGE = {
    "hits": 2,
    "jobs": [
        {
            "title": "Systems Development Engineer Intern - Summer 2027",
            "normalized_location": "Seattle, Washington, USA",
            "posted_date": "September 24, 2026",
            "job_path": "/en/jobs/10559746/systems-development-engineer-intern",
            "id_icims": "10559746",
        },
        {
            "title": "Data Center Engineering Operations Intern",
            "normalized_location": "Virtual, USA",
            "posted_date": "September 16, 2026",
            "job_path": "/en/jobs/10550494/data-center-engineering-operations-intern",
            "id_icims": "10550494",
        },
    ],
}


class FakeAmazon:
    def __init__(self, page=AMZ_PAGE):
        self.page = page
        self.asked = []

    def get_json(self, url):
        self.asked.append(url)
        return self.page


def test_amazon_makes_one_request_per_query():
    """The endpoint is undocumented, so the request count has to stay flat."""
    fetcher = FakeAmazon()
    postings, fetched = fetch(fetcher, {"amazon": ("intern",)}, {"amazon": {"intern": "Amazon"}})
    assert len(fetcher.asked) == 1
    assert "base_query=intern" in fetcher.asked[0]
    assert "country=USA" in fetcher.asked[0]
    assert fetched == {("amazon", "intern")}
    assert len(postings) == 2
    first = postings[0]
    assert first.company == "Amazon"
    assert first.source == "amazon"
    assert first.url == (
        "https://www.amazon.jobs/en/jobs/10559746/systems-development-engineer-intern"
    )
    assert first.provider_id == "amazon:10559746"
    assert first.remote is False
    assert postings[1].remote is True          # "Virtual, USA"


def test_amazon_absent_when_the_endpoint_fails():
    class Dead:
        def get_json(self, url):
            return None

    postings, fetched = fetch(Dead(), {"amazon": ("intern",)}, None)
    assert postings == [] and fetched == set()


def test_workday_warns_when_the_tenant_is_not_the_employer(caplog):
    """Discover's careers page advertises Capital One's board."""
    poster = FakePoster()
    with caplog.at_level("WARNING"):
        fetch(poster, {"workday": ("capitalone/wd12/Capital_One",)},
              {"workday": {"capitalone/wd12/Capital_One": "Discover"}}, ("devops",))
    assert any("does not look like" in str(r.msg) for r in caplog.records)


@pytest.mark.parametrize("company,spec", [
    ("NASA JPL", "citjpl/wd5/Jobs"),                 # Caltech runs the lab
    ("Booz Allen Hamilton", "bah/wd1/BAH_Jobs"),     # initials
    ("Southwest Airlines", "swa/wd1/external"),      # airline code
    ("M&T Bank", "mtb/wd5/MTB"),                     # contraction
])
def test_workday_stays_quiet_for_a_legitimate_odd_tenant(caplog, company, spec):
    poster = FakePoster()
    with caplog.at_level("WARNING"):
        fetch(poster, {"workday": (spec,)}, {"workday": {spec: company}}, ("devops",))
    assert not [r for r in caplog.records if "does not look like" in str(r.msg)]


# --- phenom ---------------------------------------------------------------

PHENOM_SITEMAP_XML = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://careers.example.com/us/en/job/AAA/IT-Platform-Intern</loc></url>
  <url><loc>https://careers.example.com/us/en/job/BBB/Cloud-Platform-Sales</loc></url>
  <url><loc>https://careers.example.com/us/en/job/CCC/Barista-Weekend</loc></url>
  <url><loc>https://careers.example.com/us/en/search-results</loc></url>
</urlset>"""


def _ld(title, employment="FULL_TIME", posted="2026-09-20", valid=None):
    import json as _json
    doc = {"@type": "JobPosting", "title": title, "datePosted": posted,
           "employmentType": [employment],
           "jobLocation": {"address": {"addressLocality": "Rochester",
                                       "addressRegion": "New York",
                                       "addressCountry": "United States"}}}
    if valid:
        doc["validThrough"] = valid
    return f'<script type="application/ld+json">{_json.dumps(doc)}</script>'


class FakePhenom:
    def __init__(self, sitemap=PHENOM_SITEMAP_XML, pages=None):
        self.sitemap = sitemap
        self.pages = pages or {}
        self.asked = []

    def get_text(self, url):
        self.asked.append(url)
        if url.endswith("/sitemap.xml"):
            return self.sitemap
        return self.pages.get(url)


def test_phenom_opens_only_plausible_slugs(monkeypatch):
    """The barista is never fetched; the sales job is, and the title rejects it."""
    monkeypatch.setattr("jobscout.sources.boards.PHENOM_DETAIL_PAUSE", 0)
    pages = {
        "https://careers.example.com/us/en/job/AAA/IT-Platform-Intern":
            _ld("College Intern - Summer 2027 - IT Platform", "PART_TIME"),
        "https://careers.example.com/us/en/job/BBB/Cloud-Platform-Sales":
            _ld("Cloud Platform Sales Specialist I"),
    }
    fetcher = FakePhenom(pages=pages)
    postings, fetched = fetch(fetcher, {"phenom": ("careers.example.com",)},
                              {"phenom": {"careers.example.com": "Example"}})
    opened = [u for u in fetcher.asked if "/job/" in u]
    assert not any("Barista" in u for u in opened)
    assert any("Cloud-Platform-Sales" in u for u in opened)
    assert fetched == {("phenom", "careers.example.com")}
    titles = {p.title for p in postings}
    assert "College Intern - Summer 2027 - IT Platform" in titles
    assert "Cloud Platform Sales Specialist I" in titles   # provider keeps it,
                                                           # the filter drops it


def test_phenom_reads_employment_type_and_age(monkeypatch):
    monkeypatch.setattr("jobscout.sources.boards.PHENOM_DETAIL_PAUSE", 0)
    pages = {"https://careers.example.com/us/en/job/AAA/IT-Platform-Intern":
             _ld("IT Platform Intern", "INTERN", posted="2026-09-20")}
    postings, _ = fetch(FakePhenom(pages=pages), {"phenom": ("careers.example.com",)}, None)
    row = next(p for p in postings if p.title == "IT Platform Intern")
    assert row.employment_type == "INTERN"
    assert row.age_days is not None
    assert row.location == "Rochester, New York, United States"


def test_phenom_skips_a_closed_posting(monkeypatch):
    monkeypatch.setattr("jobscout.sources.boards.PHENOM_DETAIL_PAUSE", 0)
    pages = {"https://careers.example.com/us/en/job/AAA/IT-Platform-Intern":
             _ld("IT Platform Intern", "INTERN", valid="2020-01-01")}
    postings, _ = fetch(FakePhenom(pages=pages), {"phenom": ("careers.example.com",)}, None)
    assert postings == []


def test_phenom_sitemap_failure_makes_the_board_absent():
    """Otherwise a failed fetch reads as a board with nothing left open."""
    class NoSitemap(FakePhenom):
        def get_text(self, url):
            return None

    postings, fetched = fetch(NoSitemap(), {"phenom": ("careers.example.com",)}, None)
    assert postings == [] and fetched == set()


CISCO_SITEMAP = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://jobs.cisco.com/jobs/ProjectDetail/Site-Reliability-Engineer/1437351</loc></url>
  <url><loc>https://jobs.cisco.com/jobs/ProjectDetail/Senior-SRE-Platform/1429102</loc></url>
  <url><loc>https://jobs.cisco.com/jobs/ProjectDetail/Barista/1412345</loc></url>
</urlset>"""


def test_phenom_reads_the_cisco_jobs_url_spelling(monkeypatch):
    """Cisco publishes job URLs as /jobs/ProjectDetail/<slug>/<id>. The old
    "/job/" filter and _JOB_SLUG regex never matched that shape, so an
    explicitly-targeted employer silently yielded zero postings."""
    monkeypatch.setattr("jobscout.sources.boards.PHENOM_DETAIL_PAUSE", 0)
    pages = {
        "https://jobs.cisco.com/jobs/ProjectDetail/Site-Reliability-Engineer/1437351":
            _ld("Site Reliability Engineer Intern", "INTERN"),
        "https://jobs.cisco.com/jobs/ProjectDetail/Senior-SRE-Platform/1429102":
            _ld("Senior SRE Platform Intern", "INTERN"),
    }
    fetcher = FakePhenom(sitemap=CISCO_SITEMAP, pages=pages)
    postings, fetched = fetch(fetcher, {"phenom": ("jobs.cisco.com",)},
                              {"phenom": {"jobs.cisco.com": "Cisco"}})
    titles = sorted(p.title for p in postings)
    assert titles == ["Senior SRE Platform Intern", "Site Reliability Engineer Intern"]
    assert fetched == {("phenom", "jobs.cisco.com")}
    # The id is the last segment; both postings must keep distinct keys.
    keys = {p.dedupe_key for p in postings}
    assert len(keys) == 2


def test_phenom_dedupe_key_uses_the_job_not_the_department(monkeypatch):
    """Two jobs under the same /job/ path prefix (same department, different
    roles) previously shared the second-to-last segment as provider_id, so the
    DB upsert silently overwrote one posting with the other."""
    monkeypatch.setattr("jobscout.sources.boards.PHENOM_DETAIL_PAUSE", 0)
    sitemap = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://careers.example.com/us/en/job/engineering/software-engineer-intern</loc></url>
  <url><loc>https://careers.example.com/us/en/job/engineering/platform-intern</loc></url>
</urlset>"""
    pages = {
        "https://careers.example.com/us/en/job/engineering/software-engineer-intern":
            _ld("Software Engineer Intern", "INTERN"),
        "https://careers.example.com/us/en/job/engineering/platform-intern":
            _ld("Platform Intern", "INTERN"),
    }
    fetcher = FakePhenom(sitemap=sitemap, pages=pages)
    postings, _ = fetch(fetcher, {"phenom": ("careers.example.com",)}, None)
    keys = sorted(p.dedupe_key for p in postings)
    # Distinct roles under one department must not share a dedupe key: both
    # survive as separate postings instead of the second overwriting the first.
    assert len(keys) == 2
    assert keys[0].endswith("engineering/platform-intern")
    assert keys[1].endswith("engineering/software-engineer-intern")


# --- workday location resolution -----------------------------------------

WD_DETAIL_US = {
    "jobPostingInfo": {
        "location": "Folsom, CA",
        "additionalLocations": ["Richardson, TX"],
        "country": {"descriptor": "United States of America"},
    }
}
WD_DETAIL_ABROAD = {
    "jobPostingInfo": {
        "location": "Miaoli - Tongluo",
        "additionalLocations": ["Taichung - Fab 16, Taiwan"],
        "country": {"descriptor": "Taiwan"},
    }
}


class FakeDetail:
    def __init__(self, payload):
        self.payload = payload
        self.asked = []

    def get_json(self, url):
        self.asked.append(url)
        return self.payload


def _collapsed(path):
    from jobscout.models import Posting

    return Posting(source="workday", board="micron/wd1/External", company="Micron",
                   title="Intern", location="2 Locations", url="https://x/1",
                   provider_id=f"workday:micron:{path}")


def test_workday_detail_names_the_hidden_locations():
    from jobscout.sources.boards import workday_resolve_location

    fetcher = FakeDetail(WD_DETAIL_US)
    places = workday_resolve_location(fetcher, _collapsed("/job/Folsom-CA/x_JR1"))
    assert places == "Folsom, CA, United States of America; Richardson, TX"
    assert fetcher.asked == [
        "https://micron.wd1.myworkdayjobs.com/wday/cxs/micron/External/job/Folsom-CA/x_JR1"
    ]


def test_workday_detail_exposes_a_foreign_posting(config):
    from jobscout.filters import location_matches
    from jobscout.sources.boards import workday_resolve_location

    places = workday_resolve_location(FakeDetail(WD_DETAIL_ABROAD),
                                      _collapsed("/job/Miaoli/x_JR2"))
    assert "Taiwan" in places
    # The collapsed cell passed the location rule; the resolved one must not.
    assert location_matches("2 Locations", config) is True
    assert location_matches(places, config) is False


def test_workday_detail_failure_leaves_the_posting_alone():
    from jobscout.sources.boards import workday_resolve_location

    assert workday_resolve_location(FakeDetail(None), _collapsed("/job/x/y_JR3")) is None


def test_workday_detail_without_a_leading_slash_is_none():
    """A malformed provider_id must not turn into a URL fragment lookup."""
    import dataclasses

    from jobscout.sources.boards import workday_resolve_location

    posting = dataclasses.replace(_collapsed("/job/x/y_JR4"), provider_id="workday:micron:no-slash")
    assert workday_resolve_location(FakeDetail(WD_DETAIL_US), posting) is None


def test_workday_detail_non_json_payload_is_none():
    from jobscout.sources.boards import workday_resolve_location

    class BadJSON(FakeDetail):
        def get_json(self, url):
            return ["not", "a", "dict"]

    assert workday_resolve_location(BadJSON(None), _collapsed("/job/x/y_JR5")) is None


def test_age_days_treats_an_invalid_stamp_as_none():
    from jobscout.sources.boards import _age_days

    assert _age_days("not-a-date") is None
    assert _age_days("") is None
    assert _age_days(None) is None


def test_age_days_naive_iso_stamp_is_utc():
    from jobscout.sources.boards import _age_days

    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    naive = (now - datetime.timedelta(days=2)).replace(tzinfo=None)
    assert _age_days(naive.isoformat()) == 2


def test_tenant_matches_empty_sides_are_trusted():
    """An unknown tenant for an unknown employer is not worth a warning."""
    from jobscout.sources.boards import _tenant_matches

    assert _tenant_matches("", "") is True
    assert _tenant_matches("M&T Bank", "") is True


def test_workday_page_non_json_body_is_absent():
    from jobscout.sources.boards import _workday_page

    class NotJSON:
        def json(self):
            raise ValueError("no json")

    class Poster:
        def post(self, url, content, headers):
            return NotJSON()

    assert _workday_page(Poster(), "https://x", "devops", 0) is None


def test_amazon_age_handles_blank_and_garbage():
    from jobscout.sources.boards import _amazon_age

    assert _amazon_age("") is None
    assert _amazon_age("not a date") is None


def test_amazon_skips_a_job_without_an_id_or_path(monkeypatch):
    from jobscout.sources.boards import fetch

    page = {"hits": 1, "jobs": [{"title": "No Path", "location": "Seattle"}]}
    fetcher = FakeAmazon(page)
    postings, fetched = fetch(fetcher, {"amazon": ("devops intern",)}, None)
    assert postings == []
    assert fetched == {("amazon", "devops intern")}  # board answered, zero rows
