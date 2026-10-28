from typing import Literal

from .client import BridgeError, key_path
from .common import drop_none, need, slim
from .registry import tool

RECORD_PATHS = {
    "interview": "/api/v1/interviews/%d",
    "contact": "/api/v1/contacts/%d",
    "link": "/api/v1/company-links/%d",
    "rule": "/api/v1/rules/%d",
    "view": "/api/v1/saved-views/%d",
    "reminder": "/api/v1/reminders/%d",
}


# server fetches it, stay public
def check_capture_url(url: str) -> None:
    import ipaddress
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or "@" in parts.netloc:
        raise BridgeError("capture needs a plain https address")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        raise BridgeError("capture needs a public address, not %s" % host)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise BridgeError("capture needs a public address, not %s" % host)


class ConfirmTools:
    def _gate(self, name: str, args: dict, confirm_id: str | None, preview):
        if confirm_id is None:
            shown = preview()
            return {
                "needs_confirmation": True,
                "confirm_id": self.confirms.issue(name, args),
                "expires_in": int(self.confirms.ttl),
                "preview": shown,
                "next": "call %s again with the same arguments plus confirm_id" % name,
            }
        self.confirms.redeem(confirm_id, name, args)
        return None

    def _brief(self, keys: list[str]) -> dict:
        rows = {row["dedupe_key"]: row for row in self.client.jobs(True)}
        return {"jobs": [slim(rows[key]) for key in keys[:10] if key in rows], "unknown_keys": [key for key in keys if key not in rows][:10], "count": len(keys)}

    @tool("confirm", "Hide jobs from the inbox. Two steps: the first call returns a preview and a confirm_id, repeat it with the id to hide. The result has a short-lived undo_token for jobs_hide_undo.", routes=("/api/v1/hidden",))
    def jobs_hide(self, keys: list[str], confirm_id: str | None = None) -> dict:
        need(keys, "keys")
        held = self._gate("jobs_hide", {"keys": keys}, confirm_id, lambda: self._brief(keys))
        if held:
            return held
        return self.client.send("POST", "/api/v1/hidden", {"dedupe_keys": keys})

    @tool("write", "Undo a recent hide with its undo_token. The token lasts about a minute and works once.", routes=("/api/v1/hidden/undo",))
    def jobs_hide_undo(self, undo_token: str) -> dict:
        return self.client.send("POST", "/api/v1/hidden/undo", {"undo_token": undo_token})

    @tool("confirm", "Delete an interview, contact, company link, rule, saved view or follow-up by id. Two steps with confirm_id. Follow-ups cannot be restored.", routes=("/api/v1/reminders/", "/api/v1/interviews/", "/api/v1/contacts/", "/api/v1/company-links/", "/api/v1/rules/", "/api/v1/saved-views/"))
    def delete_record(self, kind: Literal["interview", "contact", "link", "rule", "view", "reminder"], id: int, confirm_id: str | None = None) -> dict:
        def preview():
            return {"delete": kind, "id": id, "record": self._find_record(kind, id), "restorable": kind != "reminder"}

        held = self._gate("delete_record", {"kind": kind, "id": id}, confirm_id, preview)
        if held:
            return held
        return self.client.send("DELETE", RECORD_PATHS[kind] % id)

    def _find_record(self, kind: str, record_id: int):
        if kind in {"interview", "reminder"}:
            data = self.client.get("/api/v1/tracker")
            rows = data.get("interviews", []) if kind == "interview" else data.get("reminders", []) + data.get("done_reminders", [])
        elif kind == "rule":
            rows = self.client.get("/api/v1/rules")["rules"]
        elif kind == "view":
            rows = self.client.get("/api/v1/saved-views")["saved_views"]
        else:
            return None
        return next((row for row in rows if row.get("id") == record_id), None)

    @tool("write", "Bring back a deleted interview, contact, company link, rule or saved view.", routes=("/restore",))
    def restore_record(self, kind: Literal["interview", "contact", "link", "rule", "view"], id: int) -> dict:
        return self.client.send("POST", (RECORD_PATHS[kind] % id) + "/restore")

    @tool("confirm", "Merge one company into another: contacts, links and notes move over. Two steps with confirm_id. The result has a merge_id for company_merge_undo.", routes=("/api/v1/companies/", "/merge"))
    def company_merge(self, source: str, target: str, confirm_id: str | None = None) -> dict:
        def preview():
            src, dst = self._company(source), self._company(target)
            return {"merge": src["name"], "into": dst["name"], "moves": {"postings": len(src.get("postings", [])), "contacts": len(src.get("contacts", [])), "links": len(src.get("saved_links", []))}}

        held = self._gate("company_merge", {"source": source, "target": target}, confirm_id, preview)
        if held:
            return held
        src, dst = self._company(source), self._company(target)
        return self.client.send("POST", "/api/v1/companies/%s/merge" % key_path(src["key"]), {"target": dst["key"]})

    @tool("write", "Undo a company merge by merge_id. Refused with a conflict if either company changed since.", routes=("/api/v1/company-merges/",))
    def company_merge_undo(self, merge_id: int, expected_revision: int | None = None) -> dict:
        return self.client.send("POST", "/api/v1/company-merges/%d/undo" % merge_id, drop_none({"expected_revision": expected_revision}))

    @tool("confirm", "Hide a company, or bring it back with hidden=false. Hiding takes two steps with confirm_id.", routes=("/hide",))
    def company_hide(self, company: str, hidden: bool = True, confirm_id: str | None = None) -> dict:
        found = self._company(company)
        action = "hide" if hidden else "restore"
        if hidden:
            held = self._gate("company_hide", {"company": company, "hidden": hidden}, confirm_id, lambda: {"hide": found["name"], "postings": len(found.get("postings", []))})
            if held:
                return held
        return self.client.send("POST", "/api/v1/companies/%s/%s" % (key_path(found["key"]), action))

    @tool("confirm", "Import up to 100 matched connections as contacts. Two steps with confirm_id.", routes=("/api/v1/connections/import",))
    def connections_import(self, contacts: list[dict], confirm_id: str | None = None) -> dict:
        if not contacts or len(contacts) > 100:
            raise BridgeError("send between 1 and 100 connections")
        held = self._gate("connections_import", {"contacts": contacts}, confirm_id, lambda: {"count": len(contacts), "first": contacts[:5]})
        if held:
            return held
        return self.client.send("POST", "/api/v1/connections/import", {"contacts": contacts})

    @tool("confirm", "Add a posting by URL. The server fetches that address. Pass company and title for sites it cannot read itself. Two steps with confirm_id.", routes=("/api/v1/capture",), openWorldHint=True)
    def job_capture(self, url: str, company: str | None = None, title: str | None = None, location: str | None = None, terms: str | None = None, remote: bool | None = None, confirm_id: str | None = None) -> dict:
        body = drop_none({"url": url, "company": company, "title": title, "location": location, "terms": terms, "remote": remote})
        check_capture_url(url)
        held = self._gate("job_capture", body, confirm_id, lambda: {"server_will_fetch": url, "fields": body})
        if held:
            return held
        return self.client.send("POST", "/api/v1/capture", body)

    @tool("confirm", "Change a job's eligibility verdict override. override and restore bypass a do_not_apply verdict and take two steps with confirm_id; revoke turns the guard back on.", routes=("/eligibility/override",))
    def eligibility_override(self, key: str, action: Literal["override", "revoke", "restore"], note: str | None = None, ids: list[int] | None = None, confirm_id: str | None = None) -> dict:
        path = "/api/v1/jobs/%s/eligibility/override" % key_path(key)
        if action == "revoke":
            return self.client.send("DELETE", path, drop_none({"ids": ids}) or None)
        if action == "restore" and not ids:
            raise BridgeError("ids of the overrides to restore are required")

        def preview():
            return {"job": slim(self.client.job(key) or {"dedupe_key": key}), "eligibility": self.client.get("/api/v1/jobs/%s/eligibility" % key_path(key)).get("eligibility"), "action": action, "note": note}

        held = self._gate("eligibility_override", {"key": key, "action": action, "note": note, "ids": ids}, confirm_id, preview)
        if held:
            return held
        if action == "override":
            return self.client.send("POST", path, {"note": note or ""})
        return self.client.send("POST", path + "/restore", {"ids": ids})
