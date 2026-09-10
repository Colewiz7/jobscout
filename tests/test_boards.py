"""Board API normalisation, against trimmed real responses."""
from jobscout.sources.boards import PROVIDERS, _ashby, _greenhouse, _lever, fetch


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
    postings, fetched = fetch(fetcher, {"workday": ("acme",)})
    assert postings == [] and fetched == set()
    assert fetcher.asked == []


def test_every_provider_has_a_url_template():
    assert set(PROVIDERS) == {"greenhouse", "lever", "ashby"}


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
