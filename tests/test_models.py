"""Dedupe keys: provider id when we can get one, hash when we cannot."""
from jobscout.models import Posting, normalise, provider_id_from_url


def _posting(**kwargs):
    base = dict(
        source="simplify-s27",
        company="Acme",
        title="Cloud Infrastructure Intern",
        location="Austin, TX",
        url="",
    )
    base.update(kwargs)
    return Posting(**base)


def test_provider_id_from_greenhouse_board_url():
    assert provider_id_from_url("https://job-boards.greenhouse.io/acme/jobs/123") == "greenhouse:123"
    assert provider_id_from_url("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse:123"


def test_provider_id_from_embedded_gh_jid():
    url = "https://acme.com/careers/jobs/9?gh_jid=8172487"
    assert provider_id_from_url(url) == "greenhouse:8172487"


def test_provider_id_from_lever_and_ashby():
    uuid = "013b4e53-9d70-4bca-9c0f-1231cfe46dbb"
    assert provider_id_from_url(f"https://jobs.lever.co/Acme/{uuid}") == f"lever:{uuid}"
    assert provider_id_from_url(f"https://jobs.ashbyhq.com/Acme/{uuid}") == f"ashby:{uuid}"


def test_unknown_ats_falls_back_to_hash():
    key = _posting(url="https://careers-gdms.icims.com/jobs/74880/job").dedupe_key
    assert key.startswith("sha256:")


def test_same_job_on_two_hosts_shares_a_key():
    """The whole point: the README links the employer page, the board API links
    the provider page, and both must resolve to one posting."""
    readme = _posting(url="https://acme.com/careers?gh_jid=555")
    board = _posting(source="greenhouse", provider_id="greenhouse:555",
                     url="https://job-boards.greenhouse.io/acme/jobs/555")
    assert readme.dedupe_key == board.dedupe_key == "greenhouse:555"


def test_hash_ignores_url_entirely():
    """Host is deliberately not in the hash, so a tracking-param or domain
    change cannot re-file an existing job as new."""
    a = _posting(url="https://a.example/jobs/1")
    b = _posting(url="https://b.example/totally-different")
    assert a.dedupe_key == b.dedupe_key


def test_hash_includes_terms():
    spring = _posting(source="simplify-off", terms="Spring 2027")
    summer = _posting(source="simplify-off", terms="Summer 2027")
    assert spring.dedupe_key != summer.dedupe_key


def test_normalise_collapses_punctuation_and_case():
    assert normalise("Cloud/Platform  Engineer - Intern") == "cloud platform engineer intern"


def test_source_id_uses_native_id_for_boards():
    board = _posting(source="ashby", provider_id="ashby:abc")
    assert board.source_id == "ashby:abc"


def test_fallback_key_is_stable_whether_or_not_a_url_is_present():
    with_url = _posting(url="https://job-boards.greenhouse.io/acme/jobs/777")
    without = _posting(url="")
    assert with_url.dedupe_key == "greenhouse:777"
    assert with_url.fallback_key == without.fallback_key == without.dedupe_key


def test_parse_age_handles_days_and_months():
    from jobscout.models import age_label, merge_locations, parse_age

    assert parse_age("0d") == 0 and parse_age("9d") == 9
    assert parse_age("1mo") == 30 and parse_age("7mo") == 210
    assert parse_age("") is None and parse_age("soon") is None
    assert age_label(3) == "3d" and age_label(60) == "2mo" and age_label(None) == "?"


def test_merge_locations_unions_and_keeps_order():
    from jobscout.models import merge_locations

    assert merge_locations("Austin, TX", "Seattle, WA") == "Austin, TX; Seattle, WA"
    assert merge_locations("Austin, TX; Boston, MA", "Austin, TX") == "Austin, TX; Boston, MA"
    assert merge_locations("", "  ") == ""
