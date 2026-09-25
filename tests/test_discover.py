"""Slug derivation from apply URLs."""
import pytest

from jobscout.discover import extract, probe


def test_extract_finds_slugs_per_provider():
    html = """
    <table>
      <tr><th>Company</th><th>Role</th><th>Application</th></tr>
      <tr><td><strong>Acme</strong></td><td>Cloud Intern</td>
          <td><a href="https://job-boards.greenhouse.io/acmeco/jobs/1?utm_source=Simplify">Apply</a>
              <a href="https://simplify.jobs/p/abc">Simplify</a></td></tr>
      <tr><td>↳</td><td>SRE Intern</td>
          <td><a href="https://jobs.lever.co/AcmeLabs/013b4e53-9d70-4bca-9c0f-1231cfe46dbb">Apply</a></td></tr>
      <tr><td><strong>🔥 Beta</strong></td><td>Platform Intern</td>
          <td><a href="https://jobs.ashbyhq.com/BetaInc/013b4e53-9d70-4bca-9c0f-1231cfe46dbb">Apply</a></td></tr>
    </table>"""
    found = extract(html)
    assert found["greenhouse"] == {"acmeco": "Acme"}
    assert found["ashby"] == {"BetaInc": "Beta"}
    # The arrow row belongs to Acme, not to a company literally named "↳".
    assert found["lever"] == {"AcmeLabs": "Acme"}


def test_extract_ignores_simplify_links():
    html = """<table><tr><th>Company</th><th>Application</th></tr>
      <tr><td>Acme</td><td><a href="https://simplify.jobs/p/x">s</a></td></tr></table>"""
    assert all(v == {} for v in extract(html).values())


def test_locked_rows_still_contribute_slugs():
    """A closed Summer 2027 posting still proves the company has a live board."""
    html = """<table><tr><th>Company</th><th>Application</th></tr>
      <tr><td>Acme</td><td>🔒</td></tr>
      <tr><td>Beta</td><td><a href="https://boards.greenhouse.io/beta/jobs/2">a</a></td></tr>
    </table>"""
    assert extract(html)["greenhouse"] == {"beta": "Beta"}


class _Fetcher:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url):
        return self.payload


def test_probe_true_when_the_api_answers():
    assert probe(_Fetcher({"jobs": []}), "greenhouse", "acme") is True
    assert probe(_Fetcher([]), "lever", "acme") is True


def test_probe_false_on_404():
    assert probe(_Fetcher(None), "greenhouse", "nope") is False


@pytest.mark.parametrize("company,slug,expected", [
    # Real hits from the survey.
    ("Datadog", "datadog", True),
    ("Grafana Labs", "grafanalabs", True),
    ("Tailscale", "tailscale", True),
    ("MongoDB", "mongodb", True),
    ("Temporal Technologies", "temporal", True),
    # Live boards that belong to somebody else. Every one of these answered.
    ("Charles Schwab", "charles", False),
    ("General Dynamics IT", "general", False),
    ("Oak Ridge National Lab", "oak", False),
    ("Eli Lilly", "eli", False),
    ("Constellation Brands", "constellation", False),
])
def test_name_matches_rejects_a_borrowed_slug(company, slug, expected):
    from jobscout.discover import name_matches

    assert name_matches(company, slug) is expected


def test_probe_skips_the_api_when_the_name_is_wrong():
    """The cheap check comes first: a wrong-looking slug is not even fetched."""
    from jobscout.discover import probe

    class Boom:
        def get_json(self, url):  # pragma: no cover
            raise AssertionError("should not have been fetched")

    assert probe(Boom(), "greenhouse", "charles", "Charles Schwab") is False


@pytest.mark.parametrize("company,slug", [
    ("Atoms", "cssmerge"),
    ("Axon", "axontalentcommunity"),
    ("DRW", "drweng"),
    ("Chicago Trading Company", "ctccampusboard"),
    ("Flagship Pioneering", "fspco-op012325"),
])
def test_published_slugs_need_not_resemble_the_company(company, slug):
    """Guard against applying the name rule to Simplify-derived slugs.

    These are all real boards whose slug looks nothing like the employer. The
    rule must stay on guessed slugs only, or discovery drops a quarter of the
    boards it finds.
    """
    from jobscout.discover import name_matches

    assert name_matches(company, slug) is False
