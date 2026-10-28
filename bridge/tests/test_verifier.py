import asyncio
import hashlib
import hmac
import time

import pytest

from jobseer_bridge.auth import JwtVerifier, KeySet
from jwks_stub import AUDIENCE, ISSUER, SCOPE, SUBJECT, JwksStub, forge, make_key, make_token, public_jwk, public_pem


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(scope="module")
def key():
    return make_key()


@pytest.fixture
def stub(key):
    server = JwksStub()
    server.keys = [public_jwk(key, "k1")]
    yield server
    server.stop()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def verify(stub, clock):
    verifier = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, subject=SUBJECT, scope=SCOPE, keys=KeySet(stub.url, clock=clock, timeout=2.0))
    return lambda token: asyncio.run(verifier.verify_token(token))


def test_valid_token_is_accepted(verify, key):
    access = verify(make_token(key))
    assert access.subject == SUBJECT
    assert access.client_id == AUDIENCE
    assert SCOPE in access.scopes
    assert access.expires_at > time.time()
    assert access.claims["iss"] == ISSUER


def test_azp_becomes_the_client_id(verify, key):
    assert verify(make_token(key, azp="other-client")).client_id == "other-client"


def test_audience_list_containing_ours_is_accepted(verify, key):
    assert verify(make_token(key, aud=["x", AUDIENCE]))


def test_scp_list_claim_counts_as_scope(verify, key):
    assert verify(make_token(key, scope=None, scp=[SCOPE]))


def test_expired_token_is_refused(verify, key):
    assert verify(make_token(key, exp=int(time.time()) - 120)) is None


def test_small_clock_skew_is_tolerated(verify, key):
    assert verify(make_token(key, exp=int(time.time()) - 10))
    assert verify(make_token(key, nbf=int(time.time()) + 10, iat=None))


def test_not_yet_valid_token_is_refused(verify, key):
    assert verify(make_token(key, nbf=int(time.time()) + 300, iat=None)) is None


@pytest.mark.parametrize("claim,value", [("iss", ISSUER.rstrip("/")), ("iss", "https://other.example.test/"), ("aud", "someone-else"), ("aud", [AUDIENCE + "x"]), ("sub", "intruder"), ("scope", "openid profile")])
def test_wrong_claims_are_refused(verify, key, claim, value):
    assert verify(make_token(key, **{claim: value})) is None


@pytest.mark.parametrize("claim", ["exp", "iss", "aud", "sub"])
def test_missing_required_claims_are_refused(verify, key, claim):
    assert verify(make_token(key, **{claim: None})) is None


def test_bad_signature_is_refused(verify, key):
    token = make_token(make_key(), kid="k1")
    assert verify(token) is None
    good = make_token(key)
    head, body, sig = good.split(".")
    assert verify(".".join([head, body, sig[:-4] + "AAAA"])) is None


def test_alg_none_is_refused(verify):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "scope": SCOPE, "exp": now + 600}
    assert verify(forge({"alg": "none", "kid": "k1", "typ": "JWT"}, claims)) is None


def test_hs256_with_the_public_key_is_refused(verify, key):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "scope": SCOPE, "exp": now + 600}
    secret = public_pem(key)
    forged = forge({"alg": "HS256", "kid": "k1", "typ": "JWT"}, claims, sign=lambda body: hmac.new(secret, body, hashlib.sha256).digest())
    assert verify(forged) is None


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c", "....", "x" * 20000, "e30.e30.e30"])
def test_malformed_tokens_are_refused(verify, token):
    assert verify(token) is None


def test_missing_kid_is_refused(verify, key):
    import jwt

    now = int(time.time())
    token = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "scope": SCOPE, "exp": now + 60}, key, algorithm="RS256")
    assert verify(token) is None


def test_unknown_kid_refetches_once_per_gap_then_rotation_works(stub, verify, key, clock):
    assert verify(make_token(key))
    assert stub.hits == 1
    rotated = make_key()
    stub.keys = [public_jwk(key, "k1"), public_jwk(rotated, "k2")]
    token = make_token(rotated, kid="k2")
    assert verify(token) is None
    assert stub.hits == 1
    clock.now += 31
    assert verify(token)
    assert stub.hits == 2
    assert verify(make_token(make_key(), kid="k9")) is None
    assert stub.hits == 2


def test_keys_are_cached_within_the_ttl(stub, verify, key, clock):
    for _ in range(3):
        assert verify(make_token(key))
    assert stub.hits == 1
    clock.now += 301
    assert verify(make_token(key))
    assert stub.hits == 2


def test_unreachable_key_set_fails_closed(stub, verify, key, clock):
    stub.down = True
    assert verify(make_token(key)) is None
    clock.now += 10
    stub.down = False
    assert verify(make_token(key))


def test_expired_cache_with_key_set_down_does_not_reuse_stale_keys(stub, verify, key, clock):
    assert verify(make_token(key))
    stub.down = True
    clock.now += 400
    assert verify(make_token(key)) is None


def test_connection_refused_fails_closed(key, clock):
    dead = JwksStub()
    url = dead.url
    dead.stop()
    verifier = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, subject=SUBJECT, scope=SCOPE, keys=KeySet(url, clock=clock, timeout=1.0))
    assert asyncio.run(verifier.verify_token(make_token(key))) is None


def test_rejections_never_log_the_token(verify, key, caplog):
    token = make_token(key, sub="intruder")
    with caplog.at_level("DEBUG"):
        assert verify(token) is None
        assert verify(token[:-3] + "zzz") is None
    assert token not in caplog.text and token.split(".")[1] not in caplog.text
