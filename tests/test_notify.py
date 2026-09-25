"""Message shape and the flood cap."""
from jobscout.notify import _headers, format_posting, format_held, push


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


def test_format_held_lists_the_tail_without_hiding_it():
    rows = [{**ROW, "title": f"Cloud Intern {i}"} for i in range(12)]
    title, body = format_held(rows)
    assert title == "12 more queued"
    assert body.count("- Acme:") == 8
    assert "and 4 more" in body
    assert "Held for the next run" in body


def test_push_sends_bearer_token_and_click():
    fetcher = FakeFetcher()
    assert push(fetcher, "http://ntfy.ntfy.svc.cluster.local", "jobs", "tk",
                "t", "b", click="https://x") is True
    url, body, headers = fetcher.calls[0]
    assert url == "http://ntfy.ntfy.svc.cluster.local/jobs"
    assert headers["Authorization"] == "Bearer tk"
    assert headers["Click"] == "https://x"
    assert body == "b"


def test_push_reports_failure():
    assert push(FakeFetcher(ok=False), "http://n", "jobs", "tk", "t", "b") is False


def test_headers_omit_authorization_when_there_is_no_token():
    assert "Authorization" not in _headers("", "t")
