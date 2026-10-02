"""Company icons require a verified public site, never an ATS logo guess."""
import socket

import httpx

from jobscout import company_icons


def test_candidate_domains_use_exact_board_or_company_host():
    assert company_icons._candidate_domains(
        "Point72", ["https://boards.greenhouse.io/point72/jobs/123"]
    ) == ["point72.com"]
    assert company_icons._candidate_domains(
        "Stryten", ["https://jobs.stryten.com/jobs/5799"]
    ) == ["stryten.com"]
    assert "testnisc.com" not in company_icons._candidate_domains(
        "National Information Solutions Cooperative",
        ["https://job-boards.greenhouse.io/testnisc/jobs/8192033"],
    )


def test_site_brand_must_match_name_and_domain():
    assert company_icons._site_matches(
        "Point72", "point72.com", "<title>Home - Point72</title>"
    )
    assert not company_icons._site_matches(
        "Point72", "point72.com", "<title>Unrelated employer</title>"
    )
    assert not company_icons._site_matches(
        "Point72", "anothercompany.com", "<title>Point72</title>"
    )


def test_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    ])
    assert not company_icons._public_host("internal.example.com")


def test_only_google_favicon_redirect_is_followed(monkeypatch):
    monkeypatch.setattr(company_icons, "_public_host", lambda host: True)
    seen = []

    def respond(request):
        seen.append(str(request.url))
        if request.url.host == "www.google.com":
            return httpx.Response(301, headers={"location": "https://t3.gstatic.com/faviconV2?size=64"})
        return httpx.Response(200, content=b"icon")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert company_icons._read(client, "https://www.google.com/s2/favicons", 100,
                                   favicon_redirect=True) == b"icon"
    assert len(seen) == 2

    seen.clear()

    def unsafe(request):
        seen.append(str(request.url))
        return httpx.Response(301, headers={"location": "http://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(unsafe)) as client:
        assert company_icons._read(client, "https://www.google.com/s2/favicons", 100,
                                   favicon_redirect=True) is None
    assert len(seen) == 1

    def placeholder(request):
        if request.url.host == "www.google.com":
            return httpx.Response(301, headers={"location": "https://t0.gstatic.com/faviconV2?size=64"})
        return httpx.Response(404, headers={"content-type": "image/png"}, content=b"placeholder")

    with httpx.Client(transport=httpx.MockTransport(placeholder)) as client:
        assert company_icons._read(client, "https://www.google.com/s2/favicons", 100,
                                   favicon_redirect=True) == b"placeholder"
