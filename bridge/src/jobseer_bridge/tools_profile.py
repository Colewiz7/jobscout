from .client import BridgeError
from .profile_diff import apply, diff, editable
from .registry import tool


class ProfileTools:
    @tool(
        "confirm",
        "Change the quick-fill profile. set_fields maps existing field keys to new values. upsert adds or updates rows in fields, documents, answer_templates or stories (matched by key, id or name; partial rows keep the rest). remove lists keys or ids to delete per section. Deleting a template, document or story is permanent. Two steps: the first call returns a diff and a confirm_id; repeat it with the id to save. The save fails if the profile changed after the preview.",
        routes=("/api/v1/profile",),
    )
    def profile_update(self, set_fields: dict[str, str] | None = None, upsert: dict[str, list[dict]] | None = None, remove: dict[str, list[str]] | None = None, confirm_id: str | None = None) -> dict:
        if not self.client.features().get("quick_fill"):
            raise BridgeError("quick-fill is off on this server")
        args = {"set_fields": set_fields, "upsert": upsert, "remove": remove}
        profile = self.client.get("/api/v1/profile")["profile"]
        new = apply(profile, set_fields, upsert, remove)
        changes = diff(editable(profile), new)
        if not changes:
            return {"changed": False, "revision": profile["revision"]}
        if confirm_id is None:
            return {
                "needs_confirmation": True,
                "confirm_id": self.confirms.issue("profile_update", args, {"revision": profile["revision"]}),
                "expires_in": int(self.confirms.ttl),
                "revision": profile["revision"],
                "diff": changes,
                "next": "call profile_update again with the same arguments plus confirm_id",
            }
        seen = self.confirms.redeem(confirm_id, "profile_update", args)
        if seen["revision"] != profile["revision"]:
            raise BridgeError("the profile changed after the preview (revision %s, now %s); ask for a new preview" % (seen["revision"], profile["revision"]))
        saved = self.client.send("PUT", "/api/v1/profile", {"profile": new})["profile"]
        return {"changed": True, "revision": saved["revision"], "diff": changes}
