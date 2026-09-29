import datetime as dt

from jobscout.descriptions import ProviderDescriptionFetcher, parse_deadline, parse_sections


class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.payload


def test_deadline_requires_an_explicit_label_and_full_date():
    assert parse_deadline("Apply by October 18, 2026") == dt.date(2026, 10, 18)
    assert parse_deadline("Applications close 10/18/2026") == dt.date(2026, 10, 18)
    assert parse_deadline("Start date October 18, 2026") is None
    assert parse_deadline("Apply by October 18") is None


def test_sections_follow_deterministic_headings():
    sections = parse_sections(
        "<h2>About</h2><p>Build calm tools.</p>"
        "<h2>Responsibilities</h2><ul><li>Ship reliable code.</li></ul>"
        "<h2>Requirements</h2><p>Python</p>"
    )
    assert [section["key"] for section in sections] == [
        "about", "responsibilities", "requirements"
    ]
    assert sections[1]["text"] == "Ship reliable code."


def test_greenhouse_detail_uses_public_job_endpoint_and_exact_deadline():
    client = FakeFetcher({
        "content": "<h2>Requirements</h2><p>Linux</p>",
        "application_deadline": "2026-10-20T23:59:00Z",
    })
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "greenhouse:42",
        "board": "acme",
        "url": "https://job-boards.greenhouse.io/acme/jobs/42",
    })
    assert client.urls == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/42"]
    assert detail.text == "Requirements\nLinux"
    assert detail.deadline == dt.date(2026, 10, 20)
    assert detail.deadline_source == "provider"


def test_lever_and_ashby_fetches_are_provider_specific():
    lever = FakeFetcher({"description": "<p>Lever body</p>"})
    lever_detail = ProviderDescriptionFetcher(lever, min_interval=0).fetch({
        "dedupe_key": "lever:abc",
        "board": "acme",
        "url": "https://jobs.lever.co/acme/abc",
    })
    assert lever.urls == ["https://api.lever.co/v0/postings/acme/abc"]
    assert lever_detail.text == "Lever body"

    ashby = FakeFetcher({"jobs": [
        {"id": "other", "descriptionHtml": "<p>Wrong</p>"},
        {"id": "abc", "descriptionHtml": "<p>Ashby body</p>"},
    ]})
    ashby_detail = ProviderDescriptionFetcher(ashby, min_interval=0).fetch({
        "dedupe_key": "ashby:abc",
        "board": "acme",
        "url": "https://jobs.ashbyhq.com/acme/abc",
    })
    assert ashby.urls == ["https://api.ashbyhq.com/posting-api/job-board/acme"]
    assert ashby_detail.text == "Ashby body"
