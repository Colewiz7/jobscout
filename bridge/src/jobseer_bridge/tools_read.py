from .client import ApiError, BridgeError, key_path
from .common import clip, slim
from .registry import tool

DESCRIPTION_CAP = 20_000


class ReadTools:
    @tool("read", "Show the target server, feature flags and whether writes are enabled.")
    def status(self) -> dict:
        session = self.client.session(refresh=True)
        return {
            "user": session.get("user"),
            "quick_fill": bool(session.get("features", {}).get("quick_fill")),
            "readonly": self.config.readonly,
        }

    @tool("read", "List inbox jobs. Filters run locally on a short-lived copy of the inbox; limit is capped at 100.")
    def inbox_list(self, text: str | None = None, company: str | None = None, status: str | None = None, tag: str | None = None, rule: str | None = None, limit: int = 25, offset: int = 0, include_closed: bool = False) -> dict:
        rows = self.client.jobs(include_closed)
        if text:
            needle = text.casefold()
            rows = [row for row in rows if needle in " ".join(str(row.get(key) or "") for key in ("company", "title", "location", "notes")).casefold()]
        if company:
            rows = [row for row in rows if company.casefold() in str(row.get("company") or "").casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if tag:
            rows = [row for row in rows if tag.casefold() in [str(item).casefold() for item in row.get("tags") or []]]
        if rule:
            rows = [row for row in rows if rule.casefold() in [str(item).casefold() for item in row.get("rule_tags") or []]]
        return {"total": len(rows), "offset": offset, "jobs": [slim(row) for row in clip(rows, limit, offset)]}

    @tool("read", "Show one job with its eligibility verdict. Set fetch to also load the posting text, which may make the server fetch it.", openWorldHint=True)
    def job_get(self, key: str, fetch: bool = False) -> dict:
        job = self.client.job(key)
        if job is None:
            raise BridgeError("job not found: %s" % key)
        out = {"job": job}
        if self.client.features().get("quick_fill"):
            try:
                out["eligibility"] = self.client.get("/api/v1/jobs/%s/eligibility" % key_path(key))["eligibility"]
            except ApiError as error:
                if error.status != 404:
                    raise
        if fetch:
            detail = self.client.get("/api/v1/jobs/%s/description" % key_path(key))["description"]
            text = detail.get("description_text") or ""
            if len(text) > DESCRIPTION_CAP:
                detail = {**detail, "description_text": text[:DESCRIPTION_CAP], "truncated": True}
            out["description"] = detail
        return out

    @tool("read", "Show the apply queue in order, with the current apply session and resume documents.")
    def queue_get(self) -> dict:
        queued = [row for row in self.client.jobs() if row.get("status") == "queued"]
        queued.sort(key=lambda row: (row.get("queue_position") is None, row.get("queue_position") or 0))
        session = self.client.get("/api/v1/queue/session")
        return {"queue": [slim(row) for row in queued], "session": session["session"], "documents": session["documents"]}

    @tool("read", "Show tracked applications, follow-ups and interviews. Optionally keep one status.")
    def tracker_get(self, status: str | None = None) -> dict:
        data = self.client.get("/api/v1/tracker")
        apps = data.get("applications", [])
        if status:
            apps = [row for row in apps if row.get("status") == status]
        return {"applications": apps, "reminders": data.get("reminders", []), "interviews": data.get("interviews", [])}

    @tool("read", "List companies with counts. Pass name to narrow the list.")
    def companies_list(self, name: str | None = None) -> dict:
        data = self.client.get("/api/v1/companies", name=name) if name else self.client.get("/api/v1/companies")
        if "company" in data:
            return {"companies": [self._brief_company(data["company"])]}
        return {"companies": data.get("companies", [])}

    @tool("read", "Show one company by key or name, with its postings, contacts and links.")
    def company_get(self, key: str) -> dict:
        data = self.client.get("/api/v1/companies", name=key)
        if "company" not in data:
            raise BridgeError("company not found: %s" % key)
        company = data["company"]
        return {**company, "postings": [slim(row) for row in company.get("postings", [])]}

    @staticmethod
    def _brief_company(company: dict) -> dict:
        return {**company, "postings": len(company.get("postings", []))}

    @tool("read", "List inbox rules and the order revision needed to reorder them.")
    def rules_list(self) -> dict:
        return self.client.get("/api/v1/rules")

    @tool("read", "List saved views and the order revision needed to reorder them.")
    def views_list(self) -> dict:
        return self.client.get("/api/v1/saved-views")

    @tool("read", "Show the quick-fill profile: fields, documents, answer templates, stories and the revision.")
    def profile_get(self, job: str | None = None, company: str | None = None) -> dict:
        if not self.client.features().get("quick_fill"):
            raise BridgeError("quick-fill is off on this server")
        return self.client.get("/api/v1/profile", job=job, company=company)["profile"]

    @tool("read", "Export interviews as an iCalendar text. Optionally one interview id.")
    def interviews_ics(self, id: int | None = None) -> dict:
        return {"ics": self.client.get_text("/api/v1/interviews.ics", id=id)}
