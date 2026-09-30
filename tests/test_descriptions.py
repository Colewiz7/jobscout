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


def test_provider_heading_variants_preserve_source_order_and_labels():
    sections = parse_sections(
        "<p>Please Note:</p><p>US work authorization is required.</p>"
        "<h2>About Invesco</h2><p>We manage investments.</p>"
        "<h2>What’s in it for you?</h2><p>Mentorship and support.</p>"
        "<h2>Job Description</h2><p>Join the platform team.</p>"
        "<h2>What you’ll do</h2><p>Build internal tools.</p>"
        "<h2>What We're Looking For</h2><p>Python experience.</p>"
    )
    assert [section["key"] for section in sections] == [
        "logistics", "about", "benefits", "role", "responsibilities", "requirements",
    ]
    assert sections[3]["text"] == "Join the platform team."
    assert sections[4]["title"] == "What you’ll do"


def test_plain_text_workday_headings_are_segmented():
    sections = parse_sections(
        "Your Team, Your Impact\nBuild satellite software.\n"
        "What You Can Expect\nWork with flight engineers.\n"
        "What We're Looking For\nExperience with Python."
    )
    assert [section["key"] for section in sections] == ["role", "role", "requirements"]


def test_short_what_and_who_lines_divide_sections_but_sentences_do_not():
    sections = parse_sections(
        "What makes this role different?\nBuild systems with the team.\n"
        "Who you'll work with\nCollaborate with senior engineers.\n"
        "What you'll need to succeed\nBring Python experience.\n"
        "Who we are\nWe build useful software.\n"
        "What you see here is a normal sentence. It stays in the paragraph."
    )
    assert [section["key"] for section in sections] == [
        "responsibilities", "role", "requirements", "about",
    ]
    assert "normal sentence" in sections[-1]["text"]


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


def test_workday_is_inferred_from_a_simplify_posting_url():
    client = FakeFetcher({"jobPostingInfo": {"jobDescription": "<p>Workday body</p>"}})
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:100e91f1bfc56f33a2a31feee6ff087f",
        "board": "simplify-s27",
        "url": (
            "https://globalhr.wd5.myworkdayjobs.com/rec_rtx_ext_gateway/"
            "job/US-IA-CEDAR-RAPIDS-182/Systems-Engineer-Co-Op_01873686"
        ),
    })
    assert client.urls == [
        "https://globalhr.wd5.myworkdayjobs.com/wday/cxs/globalhr/"
        "rec_rtx_ext_gateway/job/US-IA-CEDAR-RAPIDS-182/"
        "Systems-Engineer-Co-Op_01873686"
    ]
    assert detail.text == "Workday body"
