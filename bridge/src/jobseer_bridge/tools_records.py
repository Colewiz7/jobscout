from typing import Literal

import httpx

from .client import ApiError, BridgeError, Conflict, key_path
from .common import drop_none, need
from .registry import tool


class RecordTools:
    def _company(self, name: str) -> dict:
        company = self.client.get("/api/v1/companies", name=name).get("company")
        if not company:
            raise BridgeError("company not found: %s" % name)
        return company

    @tool(
        "write",
        "Create, rename, annotate or link a company. create: name. rename: company and name. note: company and body. link: company, label and url; add link_id to edit that link. Current revision or note text is sent as the expected value unless you pass one.",
        routes=("/api/v1/companies", "/api/v1/companies/", "/links", "/api/v1/company-links/"),
    )
    def company_save(self, action: Literal["create", "rename", "note", "link"], company: str | None = None, name: str | None = None, body: str | None = None, label: str | None = None, url: str | None = None, link_id: int | None = None, expected_revision: int | None = None, expected_body: str | None = None) -> dict:
        if action == "create":
            return self.client.send("POST", "/api/v1/companies", {"name": need(name, "name")})
        found = self._company(need(company, "company"))
        if action == "rename":
            revision = found["revision"] if expected_revision is None else expected_revision
            return self.client.send("PATCH", "/api/v1/companies/" + key_path(found["key"]), {"name": need(name, "name"), "expected_revision": revision})
        if action == "note":
            if body is None:
                raise BridgeError("body is required")
            if expected_body is None:
                expected_body = (found.get("note") or {}).get("body") or ""
            return self.client.send("PUT", "/api/v1/companies/note", {"company": found["name"], "body": body, "expected_body": expected_body})
        if link_id is None:
            return self.client.send("POST", "/api/v1/companies/%s/links" % key_path(found["key"]), drop_none({"label": label, "url": need(url, "url")}))
        if expected_revision is None:
            expected_revision = self._row_revision(found.get("saved_links", []), link_id)
        return self.client.send("PATCH", "/api/v1/company-links/%d" % link_id, drop_none({"label": label, "url": url, "expected_revision": expected_revision}))

    @staticmethod
    def _row_revision(rows: list, record_id: int) -> int:
        for row in rows:
            if row.get("id") == record_id:
                return row["revision"]
        raise BridgeError("record %d not found" % record_id)

    @tool(
        "write",
        "Create a contact (company and name) or edit one (id plus company, used to find its revision).",
        routes=("/api/v1/contacts", "/api/v1/contacts/"),
    )
    def contact_save(self, company: str, id: int | None = None, name: str | None = None, title: str | None = None, email: str | None = None, linkedin_url: str | None = None, expected_revision: int | None = None) -> dict:
        if id is None:
            return self.client.send("POST", "/api/v1/contacts", drop_none({"company": company, "name": need(name, "name"), "title": title, "email": email, "linkedin_url": linkedin_url}))
        if expected_revision is None:
            expected_revision = self._row_revision(self._company(company).get("contacts", []), id)
        return self.client.send("PATCH", "/api/v1/contacts/%d" % id, drop_none({"name": name, "title": title, "email": email, "linkedin_url": linkedin_url, "expected_revision": expected_revision}))

    @tool(
        "write",
        "Create or edit an inbox rule, switch one on or off, or reorder all rules. kinds: archive_title, tag_title, boost_company. Pass order (every rule id) alone to reorder.",
        routes=("/api/v1/rules", "/api/v1/rules/", "/api/v1/rules/order", "/api/v1/rule-actions/"),
    )
    def rule_save(self, id: int | None = None, name: str | None = None, kind: str | None = None, pattern: str | None = None, value: str | None = None, enabled: bool | None = None, order: list[int] | None = None, expected_revision: int | None = None) -> dict:
        listing = self.client.get("/api/v1/rules")
        if order is not None:
            revision = listing["order_revision"] if expected_revision is None else expected_revision
            return self.client.send("PUT", "/api/v1/rules/order", {"ids": order, "expected_revision": revision})
        fields = drop_none({"name": name, "kind": kind, "pattern": pattern, "value": value})
        if id is None:
            return self.client.send("POST", "/api/v1/rules", fields)
        rule = next((row for row in listing["rules"] if row["id"] == id), None)
        if rule is None:
            raise BridgeError("rule %d not found" % id)
        revision = rule["revision"] if expected_revision is None else expected_revision
        out: dict = {}
        if fields:
            out = self.client.send("PATCH", "/api/v1/rules/%d" % id, {**fields, "expected_revision": revision})
        if enabled is not None:
            # toggle route ignores revisions
            if not fields and revision != rule["revision"]:
                raise Conflict(409, "This rule changed in another tab.", {"rule": rule})
            try:
                out = {**out, **self.client.send("PUT", "/api/v1/rules/%d" % id, {"enabled": enabled})}
            except (ApiError, httpx.TransportError) as error:
                if not out:
                    raise
                raise BridgeError("the fields were saved but the enabled change failed (%s); call again with enabled only" % error) from error
        if not out:
            raise BridgeError("give fields to change or enabled")
        return out

    @tool(
        "write",
        "Create or edit a saved view, or reorder all views. Pass order (every view id) alone to reorder. A name clash comes back as a conflict.",
        routes=("/api/v1/saved-views", "/api/v1/saved-views/", "/api/v1/saved-views/order"),
    )
    def view_save(self, id: int | None = None, name: str | None = None, filters: dict | None = None, sort: Literal["score", "newest", "company"] | None = None, order: list[int] | None = None, expected_revision: int | None = None) -> dict:
        fields = drop_none({"name": name, "filters": filters, "sort": sort})
        if order is None and id is None:
            return self.client.send("POST", "/api/v1/saved-views", fields)
        listing = self.client.get("/api/v1/saved-views")
        if order is not None:
            revision = listing["order_revision"] if expected_revision is None else expected_revision
            return self.client.send("PUT", "/api/v1/saved-views/order", {"ids": order, "expected_revision": revision})
        if expected_revision is None:
            expected_revision = self._row_revision(listing["saved_views"], id)
        return self.client.send("PATCH", "/api/v1/saved-views/%d" % id, {**fields, "expected_revision": expected_revision})

    @tool(
        "write",
        "Save one quick-fill detail for a job's company: an answer override (kind override, name is the template id) or a company account property (kind account). The current value is sent as expected unless you pass one.",
        routes=("/api/v1/profile/context", "/api/v1/profile/copy"),
    )
    def profile_context_set(self, job: str, company: str, kind: Literal["override", "account"], name: str, value: str | bool | None, expected: str | bool | None = None) -> dict:
        field = {"kind": kind, "name": name, "value": value}
        if expected is not None:
            field["expected"] = expected
        else:
            profile = self.client.get("/api/v1/profile", job=job, company=company)["profile"]
            if kind == "override":
                field["expected"] = (profile.get("answer_overrides") or {}).get(name, "")
            else:
                field["expected"] = (profile.get("company_account") or {}).get(name, None if name == "account_exists" else "")
        return self.client.send("PUT", "/api/v1/profile/context", {"dedupe_key": job, "company": company, "field": field})
