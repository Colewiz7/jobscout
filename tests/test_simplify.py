"""The README parser, against real rows lifted from both files."""
from jobscout.sources.simplify import clean_url, parse


def test_s27_five_column_table(fixture_text):
    rows = parse(fixture_text("simplify_s27.html"), "simplify-s27")
    assert len(rows) == 4
    assert all(r.source == "simplify-s27" for r in rows)
    # No Terms column in this file, so terms stay empty rather than picking up
    # whatever happens to sit at that index.
    assert all(r.terms == "" for r in rows)


def test_off_season_six_column_table_reads_terms(fixture_text):
    rows = parse(fixture_text("simplify_off.html"), "simplify-off")
    by_company = {r.company: r for r in rows}
    assert by_company["The Nuclear Company"].terms == "Spring 2027"
    assert by_company["Grass Valley"].terms == "Winter 2027"
    # Same index (3) is Application in the S27 file. Reading columns by
    # position instead of by header would put a URL in the terms field.
    assert "http" not in by_company["The Nuclear Company"].terms


def test_continuation_arrow_inherits_previous_company(fixture_text):
    rows = parse(fixture_text("simplify_s27.html"), "simplify-s27")
    shure = [r for r in rows if r.title == "Cloud Applications Development Intern"]
    assert len(shure) == 1
    assert shure[0].company == "Shure"


def test_br_between_locations_becomes_a_separator(fixture_text):
    rows = parse(fixture_text("simplify_s27.html"), "simplify-s27")
    multi = [r for r in rows if "Middletown" in r.location][0]
    assert ";" in multi.location
    # The bug this guards: "Middletown, RIManassas, VA".
    assert "RIManassas" not in multi.location


def test_locked_row_is_closed_and_has_no_url(fixture_text):
    rows = parse(fixture_text("simplify_off.html"), "simplify-off")
    tesla = [r for r in rows if r.company == "Tesla"][0]
    assert tesla.closed is True
    assert tesla.url == ""


def test_fire_emoji_is_stripped_from_company(fixture_text):
    rows = parse(fixture_text("simplify_off.html"), "simplify-off")
    assert "Tesla" in {r.company for r in rows}
    assert not any(r.company.startswith("\U0001f525") for r in rows)


def test_clean_url_drops_tracking_but_keeps_gh_jid():
    dirty = "https://epicgames.com/careers/jobs/618?gh_jid=618&utm_source=Simplify&ref=Simplify"
    cleaned = clean_url(dirty)
    assert "gh_jid=618" in cleaned
    assert "utm_source" not in cleaned and "ref=" not in cleaned


def test_apply_url_prefers_employer_over_simplify_mirror(fixture_text):
    rows = parse(fixture_text("simplify_s27.html"), "simplify-s27")
    assert all("simplify.jobs/p/" not in r.url for r in rows if r.url)


OPEN_ROW = (
    "<tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th>"
    "<th>Age</th></tr>"
    "<tr><td>Acme</td><td>Cloud Intern</td><td>Austin, TX</td>"
    "<td><a href=\"https://boards.greenhouse.io/acme/jobs/1\">Apply</a></td>"
    "<td>0d</td></tr>"
)


def test_headerless_rows_pass_through_unharmed():
    """A header row has to arrive before rows mean anything; a stray row
    before the header is not a posting."""
    html = "<table><tr><td>Spam Corp</td></tr>" + OPEN_ROW + "</table>"
    rows = parse(html, "simplify-s27")
    assert len(rows) == 1
    assert rows[0].company == "Acme"


def test_missing_application_column_means_closed():
    """A table whose columns drift (Application gone) must not crash, and a
    row without an application cell is treated as closed rather than open."""
    html = (
        "<tr><th>Company</th><th>Role</th></tr>"
        "<tr><td>Acme</td><td>Cloud Intern</td></tr>"
    )
    rows = parse(html, "simplify-s27")
    assert len(rows) == 1
    assert rows[0].closed is True
    assert rows[0].url == ""


def test_open_row_without_a_link_is_skipped():
    """An open row whose application cell has no anchor cannot be applied to,
    so it is not worth a row at all."""
    html = (
        "<tr><th>Company</th><th>Role</th><th>Application</th></tr>"
        "<tr><td>Acme</td><td>Cloud Intern</td><td>mailto:hr@acme.example</td></tr>"
    )
    rows = parse(html, "simplify-s27")
    assert rows == []


def test_fetch_skips_a_source_that_does_not_answer(caplog):
    from jobscout.sources import simplify as simplify_module

    class NoBody:
        def get_text(self, _url):
            return None

    fetched = simplify_module.fetch(NoBody())
    assert fetched == {}
    assert "unavailable" in caplog.text
