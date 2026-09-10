"""Slug derivation from apply URLs."""
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
