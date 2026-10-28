import pytest

from jobseer_bridge import bridge as module
from jobseer_bridge.client import BridgeError
from jobseer_bridge.confirm import Confirms
from jobseer_bridge.server import build_remote
from jobseer_bridge.config import Config
from jwks_stub import JwksStub, make_key, make_token, public_jwk
from remote_harness import Running, session_run, settings_for

ARGS = {"keys": ["a"]}


def test_other_owner_is_refused_and_cannot_burn_the_id():
    confirms = Confirms()
    cid = confirms.issue("jobs_hide", ARGS, owner=("alice", "c1"))
    with pytest.raises(BridgeError):
        confirms.redeem(cid, "jobs_hide", ARGS, owner=("bob", "c1"))
    with pytest.raises(BridgeError):
        confirms.redeem(cid, "jobs_hide", ARGS, owner=("alice", "c2"))
    with pytest.raises(BridgeError):
        confirms.redeem(cid, "jobs_hide", ARGS, owner=None)
    confirms.redeem(cid, "jobs_hide", ARGS, owner=("alice", "c1"))


def test_same_owner_redeems_once():
    confirms = Confirms()
    cid = confirms.issue("jobs_hide", ARGS, owner=("alice", "c1"))
    confirms.redeem(cid, "jobs_hide", ARGS, owner=("alice", "c1"))
    with pytest.raises(BridgeError):
        confirms.redeem(cid, "jobs_hide", ARGS, owner=("alice", "c1"))


def test_unowned_ids_still_work_for_stdio():
    confirms = Confirms()
    cid = confirms.issue("jobs_hide", ARGS)
    confirms.redeem(cid, "jobs_hide", ARGS)


def test_expired_owned_id_is_refused():
    now = [0.0]
    confirms = Confirms(ttl=10, clock=lambda: now[0])
    cid = confirms.issue("jobs_hide", ARGS, owner=("alice", "c1"))
    now[0] = 11
    with pytest.raises(BridgeError):
        confirms.redeem(cid, "jobs_hide", ARGS, owner=("alice", "c1"))


def test_bridge_refuses_an_id_issued_to_another_subject(app, monkeypatch):
    _, bridge = app
    bridge.remote = True
    who = [("alice", "c1")]
    monkeypatch.setattr(module, "current_identity", lambda: who[0])
    keys = [bridge.client.jobs(True)[0]["dedupe_key"]]
    preview = bridge.jobs_hide(keys)
    cid = preview["confirm_id"]
    who[0] = ("bob", "c1")
    with pytest.raises(BridgeError):
        bridge.jobs_hide(keys, confirm_id=cid)
    who[0] = ("alice", "c1")
    assert bridge.jobs_hide(keys, confirm_id=cid) is not None


@pytest.fixture(scope="module")
def key():
    return make_key()


def test_confirm_round_trip_over_http(dashboard, client, key):
    stub = JwksStub()
    stub.keys = [public_jwk(key, "k1")]
    config = Config(url=dashboard.base, authentik_user="cole")
    server, _ = build_remote(config, settings_for(stub.url, JOBSEER_TOOLS="all"), client)
    running = Running(server)
    first = client.jobs(True)[0]["dedupe_key"]

    async def work(session):
        import json

        preview = await session.call_tool("jobs_hide", {"keys": [first]})
        shown = json.loads(preview.content[0].text)["data"]
        done = await session.call_tool("jobs_hide", {"keys": [first], "confirm_id": shown["confirm_id"]})
        again = await session.call_tool("jobs_hide", {"keys": [first], "confirm_id": shown["confirm_id"]})
        return shown, done, again

    try:
        shown, done, again = session_run(running, make_token(key), work)
    finally:
        running.stop()
        stub.stop()
    assert shown["needs_confirmation"] is True
    assert not done.isError
    assert again.isError and "confirm_id" in again.content[0].text
