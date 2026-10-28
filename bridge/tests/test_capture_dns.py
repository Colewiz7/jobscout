import inspect
import re
import socket

import pytest

from jobseer_bridge.client import BridgeError
from jobseer_bridge.tools_confirm import CAPTURE_HOSTS, WORKDAY_HOST, check_capture_url


def resolver(*addresses):
    def resolve(host, port, type=0):
        return [(socket.AF_INET6 if ":" in item else socket.AF_INET, socket.SOCK_STREAM, 6, "", (item, port)) for item in addresses]

    return resolve


def test_public_answers_pass():
    check_capture_url("https://jobs.lever.co/acme/1", resolver("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"))


@pytest.mark.parametrize("answer", [
    ["10.0.0.5"], ["127.0.0.1"], ["169.254.169.254"], ["192.168.1.9"], ["::1"], ["fd00::1"], ["::ffff:10.0.0.1"], ["fe80::1%eth0"], ["100.64.0.1"],
])
def test_any_non_global_answer_is_refused(answer):
    with pytest.raises(BridgeError):
        check_capture_url("https://jobs.lever.co/acme/1", resolver(*answer))


def test_one_private_record_among_public_ones_is_refused():
    with pytest.raises(BridgeError):
        check_capture_url("https://jobs.lever.co/acme/1", resolver("93.184.216.34", "10.1.2.3"))


def test_resolution_failure_and_empty_answers_are_refused():
    def broken(host, port, type=0):
        raise socket.gaierror("nope")

    with pytest.raises(BridgeError):
        check_capture_url("https://jobs.lever.co/acme/1", broken)
    with pytest.raises(BridgeError):
        check_capture_url("https://jobs.lever.co/acme/1", resolver())


@pytest.mark.parametrize("url", ["http://jobs.lever.co/", "https://localhost/", "https://box.internal/", "https://10.0.0.1/", "https://u@jobs.lever.co/"])
def test_literal_checks_still_apply_and_skip_the_resolver(url):
    def never(*args, **kwargs):
        raise AssertionError("resolver must not run")

    with pytest.raises(BridgeError):
        check_capture_url(url, never)


def test_literal_public_ip_needs_no_lookup():
    check_capture_url("https://93.184.216.34/p", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))


def test_without_a_resolver_names_are_not_looked_up():
    check_capture_url("https://jobs.lever.co/acme/1")


def test_remote_bridge_uses_the_resolver(app):
    _, bridge = app
    bridge.remote = True
    bridge.resolver = resolver("10.0.0.9")
    with pytest.raises(BridgeError):
        bridge.job_capture("https://jobs.lever.co/acme/1")
    bridge.resolver = resolver("93.184.216.34")
    assert bridge.job_capture("https://jobs.lever.co/acme/1")["needs_confirmation"] is True


@pytest.mark.parametrize("url", [
    "https://jobs.lever.co/acme/1", "https://boards.greenhouse.io/acme/jobs/1", "https://job-boards.greenhouse.io/acme/jobs/1",
    "https://jobs.ashbyhq.com/acme/1", "https://acme.wd5.myworkdayjobs.com/site/job/x", "https://jobs.lever.co:443/acme/1",
])
def test_remote_mode_accepts_supported_sites(url):
    check_capture_url(url, resolver("93.184.216.34"), providers_only=True)


@pytest.mark.parametrize("url", [
    "https://example.com/careers/1", "https://jobs.lever.co.evil.example/acme", "https://evil.example/jobs.lever.co", "https://93.184.216.34/p",
    "https://jobs.lever.co:8443/acme/1", "https://myworkdayjobs.com/x", "https://a.b.c.myworkdayjobs.com/x", "https://xjobs.lever.co/acme",
])
def test_remote_mode_refuses_other_sites_before_any_lookup(url):
    def never(*args, **kwargs):
        raise AssertionError("resolver must not run")

    with pytest.raises(BridgeError) as caught:
        check_capture_url(url, never, providers_only=True)
    assert "Greenhouse" in str(caught.value)


def test_stdio_mode_still_takes_any_public_https_site():
    check_capture_url("https://example.com/careers/1", resolver("93.184.216.34"))


def test_site_list_matches_what_the_dashboard_fetches():
    from jobscout import capture

    source = inspect.getsource(capture.PostingCapture.capture)
    theirs = set(capture._GREENHOUSE_HOSTS) | set(re.findall(r'host == "([^"]+)"', source))
    assert CAPTURE_HOSTS == theirs
    assert WORKDAY_HOST.pattern == capture._WORKDAY_HOST.pattern
