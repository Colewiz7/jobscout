import asyncio
import logging
import time

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

log = logging.getLogger("jobseer_bridge.auth")

ALGORITHM = "RS256"
SKEW = 30
MAX_TOKEN = 8192
MAX_JWKS = 262144


class JwksError(Exception):
    pass


class KeySet:
    def __init__(self, url: str, *, timeout: float = 5.0, ttl: float = 300.0, retry_gap: float = 30.0, backoff: float = 5.0, clock=time.monotonic, transport=None):
        self.url = url
        self.timeout = timeout
        self.ttl = ttl
        self.retry_gap = retry_gap
        self.backoff = backoff
        self.clock = clock
        self.transport = transport
        self._keys: dict | None = None
        self._loaded = 0.0
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    async def key(self, kid: str):
        async with self._lock:
            now = self.clock()
            fresh = self._keys is not None and now - self._loaded < self.ttl
            # cap refetch from forged kids
            if not fresh or (kid not in self._keys and now - self._loaded >= self.retry_gap):
                await self._load(now)
            return self._keys.get(kid)

    async def _load(self, now: float) -> None:
        if now < self._blocked_until:
            raise JwksError("key set unavailable")
        problem = None
        keys = {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport, follow_redirects=False) as http:
                reply = await http.get(self.url)
            if reply.status_code != 200 or len(reply.content) > MAX_JWKS:
                problem = "key set returned an unusable response"
            else:
                keys = parse_keys(reply.json())
        except (httpx.HTTPError, ValueError, TypeError):
            problem = "key set could not be fetched"
        if problem:
            # fail closed on outage
            self._keys = None
            self._blocked_until = now + self.backoff
            raise JwksError(problem)
        self._keys = keys
        self._loaded = now


def parse_keys(document) -> dict:
    keys = {}
    for entry in document.get("keys", []):
        if not isinstance(entry, dict) or entry.get("kty") != "RSA":
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or entry.get("use", "sig") != "sig":
            continue
        try:
            keys[kid] = jwt.PyJWK(entry).key
        except (jwt.PyJWTError, ValueError, TypeError):
            continue
    return keys


def token_scopes(claims: dict) -> list[str]:
    found = claims.get("scope")
    scopes = found.split() if isinstance(found, str) else []
    extra = claims.get("scp")
    if isinstance(extra, list):
        scopes += [item for item in extra if isinstance(item, str)]
    return scopes


class JwtVerifier:
    def __init__(self, *, issuer: str, audience: str, subject: str, scope: str, keys: KeySet, skew: int = SKEW):
        self.issuer = issuer
        self.audience = audience
        self.subject = subject
        self.scope = scope
        self.keys = keys
        self.skew = skew

    # fail closed on doubt
    async def verify_token(self, token: str) -> AccessToken | None:
        reason = None
        access = None
        try:
            access = await self._check(token)
        except JwksError:
            reason = "key set unavailable"
        except jwt.PyJWTError as error:
            reason = type(error).__name__
        except (ValueError, TypeError, KeyError):
            reason = "malformed token"
        if access is None:
            log.warning("bearer token rejected: %s", reason or "policy")
        return access

    async def _check(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or len(token) > MAX_TOKEN or token.count(".") != 2:
            return None
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str):
            return None
        key = await self.keys.key(kid)
        if key is None:
            return None
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=self.audience,
            issuer=self.issuer,
            leeway=self.skew,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        scopes = token_scopes(claims)
        if self.scope not in scopes or claims["sub"] != self.subject:
            return None
        client = claims.get("azp")
        return AccessToken(
            token=token,
            client_id=client if isinstance(client, str) else self.audience,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            subject=claims["sub"],
            claims={"iss": claims["iss"], "aud": claims["aud"]},
        )
