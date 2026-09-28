"""Message shape and the flood cap."""
import json

from jobscout.notify import format_posting, format_summary, push


class FakeFetcher:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def post(self, url, content, headers):
        self.calls.append((url, content.decode(), headers))
        return object() if self.ok else None


ROW = {
    "company": "Acme",
    "title": "Cloud Infrastructure Intern",
    "location": "Austin, TX",
    "terms": "Spring 2027",
    "url": "https://acme.example/jobs/1",
    "dedupe_key": "greenhouse:1",
}


def test_format_posting_puts_company_and_role_in_the_title():
    title, body = format_posting(ROW)
    assert title == "Acme: Cloud Infrastructure Intern"
    assert "Austin, TX" in body and "Spring 2027" in body
    assert body.endswith(ROW["url"])


def test_format_posting_survives_a_missing_location():
    title, body = format_posting({**ROW, "location": "", "terms": ""})
    assert "location not stated" in body


def test_summary_caps_the_preview():
    rows = [{**ROW, "title": f"Cloud Intern {i}"} for i in range(30)]
    title, body = format_summary(rows, cap=15)
    assert "30 new matches" in title
    assert body.count("- Acme:") == 10
    assert "and 20 more" in body


def test_push_sends_bearer_token_and_click():
    fetcher = FakeFetcher()
    assert push(fetcher, "http://ntfy.ntfy.svc.cluster.local", "jobs", "tk",
                "t", "b", click="https://x") is True
    url, body, headers = fetcher.calls[0]
    # The topic moves into the JSON body, so the URL is the base.
    assert url == "http://ntfy.ntfy.svc.cluster.local"
    assert headers["Authorization"] == "Bearer tk"
    payload = json.loads(body)
    assert payload["topic"] == "jobs"
    assert payload["click"] == "https://x"
    assert payload["title"] == "t"
    assert payload["message"] == "b"


def test_push_carries_a_title_that_is_not_ascii():
    """A single non-ASCII character used to take down the whole run.

    The title was an HTTP header, which must be ASCII. RIT writes its co-ops
    with en dashes and a campus posting arrived carrying a graduation cap, so
    this is the real shape of the data, not a contrived case.
    """
    fetcher = FakeFetcher()
    title = "RIT: Co-Op \u2013 IT \u2013 Windows Systems Administration \U0001f393"
    assert push(fetcher, "http://n", "jobs", "tk", title, "b") is True
    payload = json.loads(fetcher.calls[0][1])
    assert payload["title"] == title
    # and it is genuinely encodable on the wire
    fetcher.calls[0][1].encode("utf-8")


def test_push_reports_failure():
    assert push(FakeFetcher(ok=False), "http://n", "jobs", "tk", "t", "b") is False


