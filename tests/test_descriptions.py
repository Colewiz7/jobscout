import datetime as dt

from jobscout.descriptions import ProviderDescriptionFetcher, parse_deadline, parse_sections


class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.payload


class FakeHtmlFetcher(FakeFetcher):
    def get_text(self, url):
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
    assert [section["key"] for section in sections] == ["role", "benefits", "requirements"]


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


def test_combined_qualifications_requirements_heading_starts_a_new_section():
    sections = parse_sections(
        "Job Description\nCoordinate testing with other teams.\n"
        "Qualifications/Requirements\nPursuing a systems engineering degree."
    )
    assert [section["key"] for section in sections] == ["role", "requirements"]


def test_real_posting_heading_shapes_split_work_requirements_and_benefits():
    caci = parse_sections(
        "The Opportunity:\nThe internship begins in May and lasts 12 weeks. Responsibilities:\n"
        "Build software with Python.\nQualifications: Required:\nExperience with Linux.\n"
        "Desired:\nGPA 3.0 preferred.\nWhat You Can Expect:\nA culture of integrity."
    )
    assert [section["key"] for section in caci] == [
        "role", "responsibilities", "requirements", "nice_to_have", "benefits",
    ]
    nike = parse_sections(
        "WHO YOU’LL WORK WITH\nMeet the team.\nWHAT YOU WILL WORK ON\n"
        "Design cushioning systems.\nWHO WE ARE LOOKING FOR\n"
        "Experience with Design of Experiments (DOE)."
    )
    assert [section["key"] for section in nike] == ["role", "responsibilities", "requirements"]
    audax = parse_sections(
        "POSITION SUMMARY:\nSupport IT.\nRESPONSIBILITIES:\nHelp users.\n"
        "COMPETENCIES:\nWindows OS and macOS.\nREQUIREMENTS/QUALIFICATIONS:\n"
        "Currently enrolled in a BS/BA program."
    )
    assert [section["key"] for section in audax] == ["role", "responsibilities", "requirements", "requirements"]


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


def test_embedded_greenhouse_job_id_resolves_the_real_board():
    client = FakeFetcher({"content": "<h2>Responsibilities</h2><p>Build safe systems.</p>"})
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:abc", "board": None,
        "url": "https://careers.withwaymo.com/jobs?gh_jid=8231711",
    })
    assert client.urls == ["https://boards-api.greenhouse.io/v1/boards/waymo/jobs/8231711"]
    assert detail.sections[0]["key"] == "responsibilities"


def test_simplify_lever_url_uses_lever_detail_api():
    client = FakeFetcher({"description": "<p>Design hardware.</p>"})
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:abc", "board": None,
        "url": "https://jobs.lever.co/acme/00000000-0000-4000-8000-000000000001",
    })
    assert client.urls == ["https://api.lever.co/v0/postings/acme/00000000-0000-4000-8000-000000000001"]
    assert detail.text == "Design hardware."


def test_smartrecruiters_sections_are_readable():
    client = FakeFetcher({"jobAd": {"sections": {
        "jobDescription": {"title": "Job Description", "text": "<p>Ship useful software.</p>"},
        "qualifications": {"title": "Qualifications", "text": "<p>Python experience.</p>"},
    }}})
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:abc", "board": None,
        "url": "https://jobs.smartrecruiters.com/AbbVie/3743990015684476",
    })
    assert client.urls == ["https://api.smartrecruiters.com/v1/companies/AbbVie/postings/3743990015684476"]
    assert [section["key"] for section in detail.sections] == ["role", "requirements"]
    assert "Ship useful software" in detail.text


def test_allowlisted_jobposting_jsonld_is_used_without_generic_url_fetching():
    page = '<script type="application/ld+json">{"@type":"JobPosting","description":"' + ("Build safe systems. " * 8) + '"}</script>'
    client = FakeHtmlFetcher(page)
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:abc", "board": None,
        "url": "https://careers.amd.com/jobs/90950?icims=1",
    })
    assert detail.text.startswith("Build safe systems")
    assert client.urls == ["https://careers.amd.com/jobs/90950?icims=1"]
    client.urls.clear()
    assert ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:abc", "board": None,
        "url": "https://example.invalid/jobs/90950",
    }) is None
    assert client.urls == []


def test_amazon_public_job_page_reads_only_the_job_content():
    client = FakeHtmlFetcher('<nav>Not the posting</nav><div id="job-detail-body"><div class="content"><h2>Description</h2><p>' + ("Work on quantum systems. " * 6) + '</p></div></div>')
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:abc", "board": None,
        "url": "https://amazon.jobs/en/jobs/10556930/role",
    })
    assert detail.text.startswith("Description")
    assert "Not the posting" not in detail.text


def test_allowlisted_successfactors_page_uses_only_the_job_body():
    client = FakeHtmlFetcher('<nav>Not the job</nav><div class="joblayouttoken"><h2>Responsibilities</h2><p>' + ("Build dependable devices. " * 5) + '</p></div>')
    detail = ProviderDescriptionFetcher(client, min_interval=0).fetch({
        "dedupe_key": "sha256:abc", "board": None,
        "url": "https://careers.acuityinc.com/job/Example/123?ats=successfactors",
    })
    assert detail.sections[0]["key"] == "responsibilities"
    assert "Not the job" not in detail.text
