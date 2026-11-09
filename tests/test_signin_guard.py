"""Nothing reachable from a posting URL may fetch a Career Connect host."""
import httpx
import pytest

from jobscout import company_icons
from jobscout.descriptions import ProviderDescriptionFetcher
from jobscout.http import Fetcher
from jobscout.liveness import PostingLivenessChecker
from jobscout.models import is_signin_url

HOST = "rit-csm.symplicity.com"

BLOCKED = [
    # real sign-in hosts, plain and dressed up
    f"https://{HOST}/students/app/jobs/detail/1",
    "https://careerconnect.rit.edu/jobs/1",
    "https://RIT-CSM.Symplicity.COM/x",
    "HTTPS://rit-csm.symplicity.com/x",
    f"https://{HOST}./x",
    f"https://{HOST}:443/x",
    f"https://{HOST}?x=1",
    f"https://{HOST}#frag",
    "https://careerconnect.rit.edu./x",
    "https://rit-csm.symplicity。com/x",
    "https://ｒit-csm.symplicity.com/x",
    # userinfo and backslash tricks
    f"https://user:pw@{HOST}/x",
    f"https://example.com@{HOST}/x",
    f"{'https://' + HOST}@example.com/x",
    f"https://{HOST}\\@example.com/x",
    f"https://{HOST}\\.example.com/x",
    f"https://{HOST}#@example.com/x",
    # percent encoding, any case, any depth
    "https:%2f%2frit-csm.symplicity.com/x",
    "https:%2F%2Frit-csm.symplicity.com/x",
    "https://rit-csm%2esymplicity.com/x",
    "https://rit-csm%2Esymplicity.com/x",
    "https://rit-csm.symplicity%2ecom/x",
    "https://rit-csm.symplicity%2Ecom/x",
    "https://rit-csm%252esymplicity.com/x",
    "https://rit-csm.symplicity%252Ecom/x",
    "https://rit%2dcsm.symplicity.com/x",
    f"https://{HOST}%2fx",
    "https://%72it-csm.symplicity.com/x",
    # not a clean http url
    "not a url",
    f"//{HOST}/x",
    f"{HOST}/x",
    f"ftp://{HOST}/x",
    f"javascript:{HOST}",
    "https:///x",
    "https://",
    "https:",
    "http://[bad/x",
    # whitespace and control characters
    f" https://{HOST}/x",
    f"https://{HOST}/x ",
    f"https://{HOST} /x",
    f"https://{HOST}\t/x",
    f"https://{HOST}\n/x",
    f"https://{HOST}/x\r\n",
    "https://rit-csm.symplicity\n.com/x",
    f"https://{HOST}\x00/x",
    f"https://{HOST} /x",
    f"https://{HOST} /x",
    # odd numeric and bracket forms fail closed
    "https://2130706433/x",
    "https://0x7f.0.0.1/x",
    "https://127.1/x",
    "https://01.2.3.4/x",
    "https://1.2.3/x",
    "https://1.2.3.4.5/x",
    "https://[not-ip]/x",
    "https://[::1/x",
    "https://::1/x",
    "https://[::1]x/x",
    "https://[::1]:abc/x",
    "https://example.com:abc/x",
    "https://example.com:/x",
    # ports must be plain ascii 1 to 65535
    "https://example.com:65536/x",
    "https://example.com:99999/x",
    "https://example.com:100000/x",
    "https://example.com:0/x",
    "https://example.com:00080/x",
    "https://example.com:080/x",
    "https://example.com:１２３/x",
    "https://example.com:١٢٣/x",
    "https://example.com:+80/x",
    "https://example.com: 80/x",
    "https://example.com:-1/x",
    "https://example.com:8_0/x",
    "https://[2001:db8::1]:65536/x",
    "https://[2001:db8::1]:８０/x",
    f"https://{HOST}:65536/x",
    "https://example..com/x",
    "https://-example.com/x",
    f"https://{'a' * 64}.example.com/x",
]

ALLOWED = [
    "https://evilsymplicity.com/x",
    "https://symplicity.com.evil.example/x",
    "https://notcareerconnect.rit.edu.example/x",
    "https://careerconnect.rit.edu.evil.example/x",
    "https://rit.edu/x",
    "https://boards.greenhouse.io/acme/jobs/1",
    "http://jobs.example.com/x",
    "https://jobs.example.com./x",
    "https://jobs.example.com:8443/x",
    "https://example.com:1/x",
    "https://example.com:65535/x",
    "https://example.com/symplicity.com",
    f"https://example.com/?next=https://{HOST}/",
    f"https://example.com/#{HOST}",
    "https://xn--symplicty-q5a.com/x",
    "https://bücher.example/x",
    "https://1.2.3.4/x",
    "https://[2001:db8::1]/x",
    "https://[2001:db8::1]:8443/x",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_classifier_blocks_and_fails_closed(url):
    assert is_signin_url(url)


@pytest.mark.parametrize("url", ALLOWED)
def test_classifier_keeps_clean_urls_allowed(url):
    assert not is_signin_url(url)


@pytest.mark.parametrize("value", ["", None, 5, b"https://example.com/"])
def test_classifier_fails_closed_on_no_url(value):
    assert is_signin_url(value)


class Wire:
    def __init__(self, redirect_to=""):
        self.hosts = []
        self.redirect_to = redirect_to

    def __call__(self, request):
        self.hosts.append(request.url.host)
        if self.redirect_to and len(self.hosts) == 1:
            # raw bytes like a real server sends
            return httpx.Response(302, headers=[(b"location", self.redirect_to.encode("utf-8", "replace"))])
        return httpx.Response(200, text="<html></html>")

    def touched_sign_in(self):
        return [host for host in self.hosts if "symplicity" in host or "careerconnect.rit" in host]


def make_fetcher(wire):
    return Fetcher(retry_seconds=0, transport=httpx.MockTransport(wire))


def test_candidate_domains_skip_sign_in_urls():
    urls = ["https://rit-csm.symplicity.com/students/app/jobs/detail/1", "https://careerconnect.rit.edu/x"]
    assert company_icons._candidate_domains("Acme", urls) == ["acme.com"]


@pytest.mark.parametrize("url", BLOCKED)
def test_candidate_domains_skip_every_blocked_url(url):
    assert company_icons._candidate_domains("Acme", [url]) == ["acme.com"]


@pytest.mark.parametrize("url", BLOCKED)
def test_icon_read_never_sends_a_request(url):
    wire = Wire()
    with httpx.Client(transport=httpx.MockTransport(wire)) as client:
        assert company_icons._read(client, url, 1000) is None
    assert wire.hosts == []


@pytest.mark.parametrize("url", BLOCKED)
def test_fetcher_refuses_blocked_urls(url):
    wire = Wire()
    with make_fetcher(wire) as fetcher:
        assert fetcher.get(url) is None
        assert fetcher.probe(url).status_code == 451
        assert fetcher.get_text(url) is None
        assert fetcher.get_json(url) is None
    assert wire.hosts == []


@pytest.mark.parametrize("url", BLOCKED)
def test_fetcher_refuses_a_redirect_into_a_blocked_url(url):
    wire = Wire(redirect_to=url)
    with make_fetcher(wire) as fetcher:
        fetcher.get("https://jobs.example.com/a")
    assert wire.hosts[0] == "jobs.example.com"
    assert wire.touched_sign_in() == []


# --- malformed locations from an allowed host ---

LOCATIONS = BLOCKED + [
    "https:\\\\rit-csm.symplicity.com\\x",
    "https:\\rit-csm.symplicity.com\\x",
    "http:\\\\[bad\\x",
    "http://[bad/x",
    "https://exam\x00ple.com/x",
    "\x00",
    "https://" + "a" * 100000 + ".com/x",
    "/" + "a" * 100000,
    "javascript:alert(1)",
    "",
    "//rit-csm.symplicity.com/x",
    "///rit-csm.symplicity.com/x",
    "https://rit-csm%2esymplicity.com/x",
    "https://example.com:65536/x",
    "https://example.com:８０/x",
    "https://[::1/x",
    "https://[v1.x]/x",
    "?",
    "#",
]


def one_hop(location):
    return Wire(redirect_to=location)


@pytest.mark.parametrize("location", LOCATIONS)
def test_fetcher_survives_a_malformed_location(location):
    for call in ("get", "probe", "get_text", "get_json"):
        wire = one_hop(location)
        with make_fetcher(wire) as fetcher:
            getattr(fetcher, call)("https://jobs.example.com/a")
        assert wire.touched_sign_in() == []


@pytest.mark.parametrize("location", LOCATIONS)
def test_descriptions_survive_a_malformed_location(location):
    for key in ("other:1", "greenhouse:1"):
        wire = one_hop(location)
        with make_fetcher(wire) as fetcher:
            service = ProviderDescriptionFetcher(fetcher, min_interval=0)
            service.fetch({"dedupe_key": key, "board": "acme", "url": "https://careers.amd.com/job/1"})
            service.fetch({"dedupe_key": key, "board": "acme", "url": "https://boards.greenhouse.io/acme/jobs/1"})
        assert wire.touched_sign_in() == []


@pytest.mark.parametrize("location", LOCATIONS)
def test_liveness_survives_a_malformed_location(location):
    wire = one_hop(location)
    with make_fetcher(wire) as fetcher:
        checker = PostingLivenessChecker(fetcher, min_interval=0)
        checker.check({"dedupe_key": "other:1", "url": "https://jobs.example.com/a"})
        checker.check({"dedupe_key": "greenhouse:1", "board": "acme", "url": "https://jobs.example.com/a"})
    assert wire.touched_sign_in() == []


@pytest.mark.parametrize("location", LOCATIONS)
def test_icon_read_survives_a_malformed_location(location):
    for options in ({}, {"favicon_redirect": True}, {"html_origin": "example.com"}):
        wire = one_hop(location)
        with httpx.Client(transport=httpx.MockTransport(wire)) as client:
            company_icons._read(client, "https://example.com/", 1000, **options)
        # a same-site hop is fine, nothing else is
        assert set(wire.hosts) == {"example.com"}


@pytest.mark.parametrize("url", BLOCKED)
def test_liveness_never_probes_blocked_urls(url):
    wire = Wire()
    with make_fetcher(wire) as fetcher:
        checker = PostingLivenessChecker(fetcher, min_interval=0)
        assert checker.check({"dedupe_key": "careerconnect:1", "url": url}).status == "unknown"
        assert checker.check({"dedupe_key": "other:1", "url": url}).status == "unknown"
        assert checker.check({"dedupe_key": "greenhouse:1", "board": "acme", "url": url}).status == "unknown"
    assert wire.hosts == []


@pytest.mark.parametrize("url", BLOCKED)
def test_descriptions_never_fetch_blocked_urls(url):
    wire = Wire()
    with make_fetcher(wire) as fetcher:
        service = ProviderDescriptionFetcher(fetcher, min_interval=0)
        assert service.fetch({"dedupe_key": "careerconnect:1", "url": url}) is None
        assert service.fetch({"dedupe_key": "greenhouse:1", "board": "acme", "url": url}) is None
        assert service._get_html("x", url) is None
        assert service._get_json("x", url) is None
    assert wire.hosts == []


# --- no entry point may raise on a weird url ---

WEIRD = BLOCKED + ALLOWED + [
    "https://example.com:65536",
    "http://",
    "https://[",
    "https://]/x",
    "https://[]/x",
    "https://example.com/%zz",
    "https://example.com/\u0000",
    "https://\u0000/x",
    "https://\ud800.example/x",
    "https://" + "a" * 300 + ".com/x",
    "https://" + "a.b" * 100 + "/x",
    "h" * 2000,
    "https://example.com/" + "%2e" * 500,
    "https://☃.example/x",
    "https://xn--/x",
    "https://xn--a.example/x",
    "mailto:a@example.com",
    "data:text/html,hi",
    "file:///etc/passwd",
]


@pytest.mark.parametrize("url", WEIRD)
def test_no_entry_point_raises_on_a_weird_url(url):
    wire = Wire()
    blocked = is_signin_url(url)
    with make_fetcher(wire) as fetcher, httpx.Client(transport=httpx.MockTransport(wire)) as client:
        company_icons._candidate_domains("Acme", [url])
        company_icons._read(client, url, 1000)
        for call in (fetcher.get, fetcher.probe, fetcher.get_text, fetcher.get_json):
            try:
                call(url)
            except (RuntimeError, httpx.HTTPError):
                # an allowed url may fail on the wire
                assert not blocked
            except httpx.InvalidURL:
                assert not blocked
        PostingLivenessChecker(fetcher, min_interval=0).check({"dedupe_key": "other:1", "url": url})
        PostingLivenessChecker(fetcher, min_interval=0).check({"dedupe_key": "greenhouse:1", "board": "acme", "url": url})
        service = ProviderDescriptionFetcher(fetcher, min_interval=0)
        service.fetch({"dedupe_key": "other:1", "url": url})
        service.fetch({"dedupe_key": "greenhouse:1", "board": "acme", "url": url})
    if blocked:
        assert wire.hosts == []


# --- no url is nothing to fetch, not an error ---

def test_no_url_rows_keep_their_old_behaviour():
    wire = Wire()
    with make_fetcher(wire) as fetcher:
        for url in ("", None):
            result = PostingLivenessChecker(fetcher, min_interval=0).check({"dedupe_key": "other:1", "url": url})
            assert result.status == "closed"
            assert ProviderDescriptionFetcher(fetcher, min_interval=0).fetch({"dedupe_key": "other:1", "url": url}) is None
    assert wire.hosts == []
    assert company_icons._candidate_domains("Acme", [""]) == ["acme.com"]


def test_no_url_careerconnect_row_is_still_skipped():
    wire = Wire()
    with make_fetcher(wire) as fetcher:
        result = PostingLivenessChecker(fetcher, min_interval=0).check({"dedupe_key": "careerconnect:1", "url": ""})
        assert result.status == "unknown"
        assert ProviderDescriptionFetcher(fetcher, min_interval=0).fetch({"dedupe_key": "careerconnect:1"}) is None
    assert wire.hosts == []


def test_lookalike_host_is_still_probed():
    wire = Wire()
    with make_fetcher(wire) as fetcher:
        result = PostingLivenessChecker(fetcher, min_interval=0).check({"dedupe_key": "other:1", "url": "https://evilsymplicity.com/job/1"})
    assert wire.hosts == ["evilsymplicity.com"]
    assert result.status == "live"
