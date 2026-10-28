def test_company_create_rename_and_note(call):
    created = call("company_save", action="create", name="Acme Labs").data
    assert created["created"] is True
    renamed = call("company_save", action="rename", company="acme labs", name="Acme Research").data
    assert renamed["company"]["name"] == "Acme Research"
    noted = call("company_save", action="note", company="Acme Research", body="met at fair").data
    assert noted["note"]["body"] == "met at fair"
    stale = call("company_save", action="note", company="Acme Research", body="x", expected_body="old")
    assert stale.body["conflict"] is True
    assert stale.body["current"] == {"note": {"body": "met at fair"}}


def test_company_rename_stale_revision_conflict(call):
    call("company_save", action="create", name="Acme Labs")
    call("company_save", action="rename", company="Acme Labs", name="Acme Research")
    reply = call("company_save", action="rename", company="Acme Research", name="Other", expected_revision=0)
    assert reply.body["conflict"] is True
    assert reply.body["current"]["company"]["name"] == "Acme Research"


def test_company_link_add_and_edit(call):
    added = call("company_save", action="link", company="Cloudflare", label="careers", url="https://example.com/jobs").data
    link_id = added["link"]["id"]
    edited = call("company_save", action="link", company="Cloudflare", link_id=link_id, label="team page").data
    assert edited["link"]["label"] == "team page"
    assert call("company_save", action="link", company="Cloudflare").error


def test_company_actions_need_a_known_company(call):
    assert call("company_save", action="rename", company="nobody", name="x").error
    assert call("company_save", action="create").error


def test_contact_create_and_edit(call):
    created = call("contact_save", company="Cloudflare", name="Sam Lee", title="Recruiter").data
    contact_id = created["contact"]["id"]
    edited = call("contact_save", company="Cloudflare", id=contact_id, title="Lead recruiter").data
    assert edited["contact"]["title"] == "Lead recruiter"
    stale = call("contact_save", company="Cloudflare", id=contact_id, title="x", expected_revision=0)
    assert stale.body["conflict"] is True
    assert stale.body["current"]["contact"]["title"] == "Lead recruiter"


def test_rule_create_edit_toggle_and_reorder(call):
    first = call("rule_save", name="drop sales", kind="archive_title", pattern="sales").data["rule"]
    second = call("rule_save", name="tag sre", kind="tag_title", pattern="sre", value="sre").data["rule"]
    edited = call("rule_save", id=first["id"], pattern="account exec").data
    assert edited["rule"]["pattern"] == "account exec"
    toggled = call("rule_save", id=first["id"], enabled=False).data
    assert toggled["rule"]["enabled"] is False
    ordered = call("rule_save", order=[second["id"], first["id"]]).data
    assert ordered["ids"] == [second["id"], first["id"]]
    stale = call("rule_save", order=[first["id"], second["id"]], expected_revision=0)
    assert stale.body["conflict"] is True


def test_rule_stale_revision_conflict_and_empty_update(call):
    rule = call("rule_save", name="a", kind="archive_title", pattern="x").data["rule"]
    call("rule_save", id=rule["id"], pattern="y")
    stale = call("rule_save", id=rule["id"], pattern="z", expected_revision=0)
    assert stale.body["conflict"] is True
    assert stale.body["current"]["rule"]["pattern"] == "y"
    assert call("rule_save", id=rule["id"]).error


def test_view_create_edit_reorder_and_name_clash(call):
    one = call("view_save", name="remote", filters={"remote": True}).data["saved_view"]
    two = call("view_save", name="new", sort="newest").data["saved_view"]
    clash = call("view_save", name="remote")
    assert clash.body["conflict"] is True
    assert clash.body["current"]["saved_view"]["id"] == one["id"]
    edited = call("view_save", id=two["id"], name="fresh").data
    assert edited["view"]["name"] == "fresh"
    ordered = call("view_save", order=[two["id"], one["id"]]).data
    assert ordered["ids"] == [two["id"], one["id"]]


def test_profile_context_override_and_account(call):
    first = call("profile_context_set", job="demo:1", company="Cloudflare", kind="override", name="why-role", value="because")
    assert first.data["value"] == "because"
    again = call("profile_context_set", job="demo:1", company="Cloudflare", kind="override", name="why-role", value="again")
    assert again.data["profile"]["answer_overrides"]["why-role"] == "again"
    stale = call("profile_context_set", job="demo:1", company="Cloudflare", kind="override", name="why-role", value="x", expected="old")
    assert stale.body["conflict"] is True
    assert stale.body["current"]["value"] == "again"
    account = call("profile_context_set", job="demo:1", company="Cloudflare", kind="account", name="sign_in_email", value="a@b.co")
    assert account.data["profile"]["company_account"]["sign_in_email"] == "a@b.co"


def test_rule_fields_and_enabled_in_one_call(call):
    rule = call("rule_save", name="a", kind="archive_title", pattern="x").data["rule"]
    reply = call("rule_save", id=rule["id"], pattern="y", enabled=False).data
    assert reply["rule"]["enabled"] is False
    assert reply["rule"]["pattern"] == "y"


def test_stale_revision_blocks_the_toggle_too(call):
    rule = call("rule_save", name="a", kind="archive_title", pattern="x").data["rule"]
    call("rule_save", id=rule["id"], pattern="y")
    stale = call("rule_save", id=rule["id"], enabled=False, expected_revision=0)
    assert stale.body["conflict"] is True
    assert stale.body["current"]["rule"]["enabled"] is True
    assert call("rules_list").data["rules"][0]["enabled"] is True


def test_stale_revision_with_fields_changes_nothing(call):
    rule = call("rule_save", name="a", kind="archive_title", pattern="x").data["rule"]
    call("rule_save", id=rule["id"], pattern="y")
    stale = call("rule_save", id=rule["id"], pattern="z", enabled=False, expected_revision=0)
    assert stale.body["conflict"] is True
    now = call("rules_list").data["rules"][0]
    assert now["pattern"] == "y" and now["enabled"] is True


def test_partial_failure_is_reported_clearly(call, client):
    from jobseer_bridge.client import ApiError

    rule = call("rule_save", name="a", kind="archive_title", pattern="x").data["rule"]
    original = client.send

    def flaky(method, path, body=None):
        if method == "PUT":
            raise ApiError(503, "try later")
        return original(method, path, body)

    client.send = flaky
    reply = call("rule_save", id=rule["id"], pattern="y", enabled=False)
    client.send = original
    assert reply.error and "fields were saved" in reply.text and "enabled only" in reply.text
    now = call("rules_list").data["rules"][0]
    assert now["pattern"] == "y" and now["enabled"] is True


def test_toggle_failure_alone_is_a_plain_error(call, client):
    from jobseer_bridge.client import ApiError

    rule = call("rule_save", name="a", kind="archive_title", pattern="x").data["rule"]
    client.send = lambda *args, **kwargs: (_ for _ in ()).throw(ApiError(503, "try later"))
    reply = call("rule_save", id=rule["id"], enabled=False)
    assert reply.error and "fields were saved" not in reply.text


def test_rule_unknown_id_is_an_error(call):
    assert call("rule_save", id=404, enabled=True).error
