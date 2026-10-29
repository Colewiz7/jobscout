from .client import BridgeError
from .common import drop_none, need
from .profile_diff import apply, diff, editable
from .registry import tool


class ProfileTools:
    def _quick_fill(self) -> None:
        if not self.client.features().get("quick_fill"):
            raise BridgeError("quick-fill is off on this server")

    # shared so edits diff alike
    def _profile_change(self, name: str, args: dict, build, confirm_id: str | None) -> dict:
        self._quick_fill()
        profile = self.client.get("/api/v1/profile")["profile"]
        new = build(profile)
        changes = diff(editable(profile), new)
        if not changes:
            return {"changed": False, "revision": profile["revision"]}
        if confirm_id is None:
            return {
                "needs_confirmation": True,
                "confirm_id": self.confirms.issue(name, args, {"revision": profile["revision"]}, owner=self.caller()),
                "expires_in": int(self.confirms.ttl),
                "revision": profile["revision"],
                "diff": changes,
                "next": "call %s again with the same arguments plus confirm_id" % name,
            }
        seen = self.confirms.redeem(confirm_id, name, args, owner=self.caller())
        if seen["revision"] != profile["revision"]:
            raise BridgeError("the profile changed after the preview (revision %s, now %s); ask for a new preview" % (seen["revision"], profile["revision"]))
        # server locks on the previewed revision
        saved = self.client.send("PUT", "/api/v1/profile", {"profile": {**new, "revision": seen["revision"]}})["profile"]
        return {"changed": True, "revision": saved["revision"], "diff": changes}

    @tool("confirm", "Change the quick-fill profile. set_fields maps existing field keys to new values. upsert adds or updates rows in fields, documents, answer_templates or stories (matched by key, id or name; partial rows keep the rest). remove lists keys or ids to delete per section. Deleting a template, document or story is permanent. Two steps: the first call returns a diff and a confirm_id; repeat it with the id to save. The save fails if the profile changed after the preview.", routes=("/api/v1/profile",))
    def profile_update(self, set_fields: dict[str, str] | None = None, upsert: dict[str, list[dict]] | None = None, remove: dict[str, list[str]] | None = None, confirm_id: str | None = None) -> dict:
        args = {"set_fields": set_fields, "upsert": upsert, "remove": remove}
        return self._profile_change("profile_update", args, lambda profile: apply(profile, set_fields, upsert, remove), confirm_id)

    def _one_row(self, name: str, section: str, args: dict, row: dict, ident: str, remove: bool, confirm_id: str | None) -> dict:
        key = need(args.get(ident), ident) if remove else None
        upsert = None if remove else {section: [row]}
        gone = {section: [key]} if remove else None
        return self._profile_change(name, args, lambda profile: apply(profile, None, upsert, gone), confirm_id)

    @tool("confirm", "Set the value of one quick-fill profile field by key (see profile_get for the keys; an empty value clears it). The server keeps a fixed set of field keys. Two steps with confirm_id; the preview is a diff and the save fails if the profile moved after it.", routes=("/api/v1/profile",))
    def profile_field_set(self, key: str, value: str, confirm_id: str | None = None) -> dict:
        args = {"key": key, "value": value}
        return self._profile_change("profile_field_set", args, lambda profile: apply(profile, {key: value}, None, None), confirm_id)

    @tool("confirm", "Add, edit or remove one interview story. Edits match by id and keep the parts you leave out; a new story needs a title. Removing is permanent. Two steps with confirm_id and a diff preview.", routes=("/api/v1/profile",))
    def profile_story_save(self, id: str | None = None, title: str | None = None, situation: str | None = None, task: str | None = None, action: str | None = None, result: str | None = None, reflection: str | None = None, competencies: list[str] | None = None, remove: bool = False, confirm_id: str | None = None) -> dict:
        args = {"id": id, "title": title, "situation": situation, "task": task, "action": action, "result": result, "reflection": reflection, "competencies": competencies, "remove": remove}
        row = drop_none({key: value for key, value in args.items() if key != "remove"})
        if not remove and not (id or title):
            raise BridgeError("give an id or a title")
        return self._one_row("profile_story_save", "stories", args, row, "id", remove, confirm_id)

    @tool("confirm", "Add, edit or remove one answer template. Edits match by id; a new template needs a name. Removing is permanent. Two steps with confirm_id and a diff preview.", routes=("/api/v1/profile",))
    def profile_template_save(self, id: str | None = None, name: str | None = None, body: str | None = None, remove: bool = False, confirm_id: str | None = None) -> dict:
        args = {"id": id, "name": name, "body": body, "remove": remove}
        row = drop_none({"id": id, "name": name, "body": body})
        if not remove and not (id or name):
            raise BridgeError("give an id or a name")
        return self._one_row("profile_template_save", "answer_templates", args, row, "id", remove, confirm_id)

    @tool("confirm", "Add, edit or remove one resume document entry, or make it the default resume with default=true. Edits match by id; a new document needs a name. Removing is permanent. Two steps with confirm_id and a diff preview.", routes=("/api/v1/profile",))
    def profile_document_save(self, id: str | None = None, name: str | None = None, date: str | None = None, url: str | None = None, default: bool | None = None, remove: bool = False, confirm_id: str | None = None) -> dict:
        args = {"id": id, "name": name, "date": date, "url": url, "default": default, "remove": remove}
        row = drop_none({"id": id, "name": name, "date": date, "url": url, "default": default})
        if not remove and not (id or name):
            raise BridgeError("give an id or a name")

        def build(profile):
            if remove:
                return apply(profile, None, None, {"documents": [need(id, "id")]})
            new = apply(profile, None, {"documents": [row]}, None)
            if default:
                # only one default at a time
                target = next(item for item in new["documents"] if (str(item.get("id")) == str(id) if id else item.get("name") == name))
                for item in new["documents"]:
                    item["default"] = item is target
            return new

        return self._profile_change("profile_document_save", args, build, confirm_id)
