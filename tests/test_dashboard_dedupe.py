"""Read-only dashboard dedupe and apply-site safeguards."""

from jobscout.config import Config
from jobscout.dashboard import _visible_scoped_job
from jobscout.db import collapse_dashboard_duplicates


def test_collapse_keeps_tracked_key_and_merges_sources():
    base = {
        "first_seen": None, "last_seen": None, "score": 100,
        "notified": False, "closed": False, "location": "Bloomfield, CT",
    }
    simplify = {**base, "dedupe_key": "sha256:one", "company": "Cigna Group",
                "title": "Data & Analytics Engineering Intern", "sources": "simplify-s27",
                "status": "saved", "url": "https://cigna.wd5.myworkdayjobs.com/cignacareers/job/Bloomfield-CT/Foo_26009533"}
    board = {**base, "dedupe_key": "workday:old", "company": "Cigna",
             "title": "Technology Development Program - Data & Analytics Engineering Internship",
             "sources": "workday", "status": "new",
             "url": "https://cigna.wd5.myworkdayjobs.com/en-US/cignacareers/job/Bloomfield-CT/Bar_26009533"}
    result = collapse_dashboard_duplicates([simplify, board])
    assert len(result) == 1
    assert result[0]["dedupe_key"] == "sha256:one"
    assert result[0]["sources"] == "simplify-s27, workday"


def test_separately_tracked_duplicates_remain_visible():
    base = {"company": "Intel", "title": "Platform Intern", "sources": "workday",
            "location": "Hillsboro, OR", "url": "https://example.invalid/job"}
    rows = collapse_dashboard_duplicates([
        {**base, "dedupe_key": "one", "status": "applied"},
        {**base, "dedupe_key": "two", "status": "saved"},
    ])
    assert len(rows) == 2


def test_private_workday_site_and_verified_public_route():
    private = {"dedupe_key": "sha256:rtx", "company": "RTX", "title": "Systems Intern",
               "status": "new", "sources": "simplify-s27", "location": "Tucson, AZ",
               "url": "https://globalhr.wd5.myworkdayjobs.com/fr-CA/Private_Posting_No_TMP/job/Tucson/Systems-Intern_01866497"}
    result = collapse_dashboard_duplicates([private])[0]
    assert result["nonpublic_site"] is True
    assert "/en-US/REC_RTX_Ext_Gateway/" in result["public_apply_url"]


def test_unverified_private_site_has_no_public_apply_url():
    private = {"dedupe_key": "sha256:ge", "company": "GE Vernova", "title": "CIC Co-op",
               "status": "new", "sources": "simplify-s27", "location": "Rochester, NY",
               "url": "https://gevernova.wd5.myworkdayjobs.com/only_confidential_executive_recruiting/job/Rochester/Foo_R5051807-1"}
    result = collapse_dashboard_duplicates([private])[0]
    assert result["nonpublic_site"] is True
    assert result["public_apply_url"] == ""


def test_old_new_rows_follow_current_scope_without_hiding_tracked_history():
    config = Config.load()
    foreign = {"company": "GE Vernova", "title": "DevSecOps Intern: Nov/Dec 2026 intake",
               "location": "4 Locations; Melbourne AUS; Perth, Australia; Remote",
               "sources": "workday", "status": "new", "url": "https://example.invalid"}
    graduate = {"company": "Intel", "title": "Platform Engineering Graduate Intern",
                "location": "Hillsboro, OR", "sources": "workday", "status": "new",
                "url": "https://example.invalid"}
    spring = {"company": "Acme", "title": "Platform Engineering Intern Spring 2027",
              "location": "Rochester, NY", "sources": "workday", "status": "new",
              "url": "https://example.invalid"}
    assert _visible_scoped_job(foreign, config) is False
    assert _visible_scoped_job(graduate, config) is False
    assert _visible_scoped_job(spring, config) is True
    assert _visible_scoped_job({**graduate, "status": "applied"}, config) is True
