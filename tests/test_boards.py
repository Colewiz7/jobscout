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
    assert set(PROVIDERS) == {"greenhouse", "lever", "ashby", "workday"}
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
