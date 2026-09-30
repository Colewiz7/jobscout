from jobscout.liveness import PostingLivenessChecker


class Response:
    def __init__(self, status, payload=None, url="https://example.invalid/job"):
        self.status_code = status
        self._payload = payload
        self.url = url

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class Fetcher:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def probe(self, url):
        self.urls.append(url)
        return self.response


def test_greenhouse_404_is_confirmed_closed():
    fetcher = Fetcher(Response(404))
    result = PostingLivenessChecker(fetcher, min_interval=0).check({
        "dedupe_key": "greenhouse:42",
        "board": "acme",
        "url": "https://job-boards.greenhouse.io/acme/jobs/42",
    })
    assert result.status == "closed"
    assert "404" in result.evidence


def test_ashby_requires_the_job_to_remain_in_the_board_payload():
    fetcher = Fetcher(Response(200, {"jobs": [{"id": "another-job"}]}))
    result = PostingLivenessChecker(fetcher, min_interval=0).check({
        "dedupe_key": "ashby:wanted-job",
        "board": "acme",
        "url": "https://jobs.ashbyhq.com/acme/wanted-job",
    })
    assert result.status == "closed"
    assert "no longer lists" in result.evidence


def test_provider_failure_stays_unknown_and_generic_redirect_is_not_closed():
    unavailable = PostingLivenessChecker(Fetcher(Response(403)), min_interval=0).check({
        "dedupe_key": "lever:abc",
        "board": "acme",
        "url": "https://jobs.lever.co/acme/abc",
    })
    redirected = PostingLivenessChecker(
        Fetcher(Response(200, url="https://example.invalid/")), min_interval=0
    ).check({"dedupe_key": "sha256:abc", "url": "https://example.invalid/jobs/abc"})
    assert unavailable.status == "unknown"
    assert redirected.status == "unknown"


def test_missing_application_url_is_closed():
    result = PostingLivenessChecker(Fetcher(Response(200)), min_interval=0).check({
        "dedupe_key": "sha256:abc",
        "url": "",
    })
    assert result.status == "closed"
