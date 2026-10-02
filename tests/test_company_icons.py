"""Company icons require a verified public site, never an ATS logo guess."""
import socket

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
