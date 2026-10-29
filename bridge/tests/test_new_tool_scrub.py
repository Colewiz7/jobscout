import json

import pytest

from jobseer_bridge.bridge import Bridge
from jobseer_bridge.client import ApiError
from jobseer_bridge.config import Config

SECRET = "app-password-ZZ-9921"

CASES = [
    ("jobs_tag_undo", {"ids": [1], "undo": "add"}),
    ("job_edit_undo", {"edit_id": 1}),
    ("rule_action_undo", {"action_id": 1}),
    ("activity_dismiss", {"id": 1}),
    ("profile_copy_mark", {"job": "demo:1", "target": "email"}),
    ("job_description_paste", {"key": "demo:1", "text": "x" * 50}),
    ("apply_session_get", {"session_id": 1}),
    ("overview_get", {"key": "demo:1"}),
    ("overview_request", {"key": "demo:1"}),
    ("overview_dismiss", {"key": "demo:1"}),
    ("overview_regenerate", {"key": "demo:1"}),
    ("overview_regenerate_undo", {"key": "demo:1", "expected_revision": 1}),
    ("profile_field_set", {"key": "phone", "value": "1"}),
    ("profile_story_save", {"title": "T"}),
    ("profile_template_save", {"name": "N", "body": "b"}),
    ("profile_document_save", {"name": "D"}),
]


class Echo:
    def __init__(self, config, fail=False):
        self.config = config
        self.fail = fail

    def features(self):
        return {"quick_fill": True, "ai_overview": True}

    def _reply(self):
        if self.fail:
            raise ApiError(500, "upstream said %s" % SECRET)
        leak = "echo " + SECRET
        profile = {"revision": 1, "fields": [{"key": "phone", "label": "Phone", "value": leak}], "documents": [], "answer_templates": [], "stories": [], "note": leak}
        return {"note": leak, "job": {"title": leak}, "description": {"description_text": leak}, "profile": profile, "entry": {"x": leak}, "revision": 1, "session": {"id": 1, "note": leak}}

    def get(self, path, **params):
        return self._reply()

    def job(self, key):
        return {"dedupe_key": key, "title": "echo " + SECRET}

    def send(self, method, path, body=None):
        return self._reply()


@pytest.mark.parametrize("name, args", CASES)
def test_results_never_carry_the_password(name, args):
    config = Config(url="http://127.0.0.1:9", token=SECRET)
    bridge = Bridge(config, client=Echo(config))
    run = bridge.guard(getattr(bridge, name))
    text = run(**args)
    if '"needs_confirmation":true' in text:
        assert SECRET not in text
        text = run(confirm_id=json.loads(text)["data"]["confirm_id"], **args)
    assert SECRET not in text and config.basic_value() not in text


@pytest.mark.parametrize("name, args", CASES)
def test_errors_never_carry_the_password(name, args):
    from jobseer_bridge.client import BridgeError

    config = Config(url="http://127.0.0.1:9", token=SECRET)
    bridge = Bridge(config, client=Echo(config, fail=True))
    run = bridge.guard(getattr(bridge, name))
    with pytest.raises(BridgeError) as caught:
        text = run(**args)
        run(confirm_id=json.loads(text)["data"]["confirm_id"], **args)
    assert SECRET not in str(caught.value) and "[redacted]" in str(caught.value)
