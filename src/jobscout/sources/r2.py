"""Just enough S3 to drain one bucket.

Four operations against one prefix does not justify boto3 and its transitive
weight in an image that is otherwise four dependencies. httpx is already here,
and SigV4 is short enough to read in one sitting.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import re
import urllib.parse

import httpx

log = logging.getLogger(__name__)

_ALGORITHM = "AWS4-HMAC-SHA256"
_REGION = "auto"          # R2 has no regions but the signature needs a value
_SERVICE = "s3"
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()

_KEY = re.compile(r"<Key>([^<]+)</Key>")
_TRUNCATED = re.compile(r"<IsTruncated>\s*true\s*</IsTruncated>", re.I)
_CONTINUATION = re.compile(r"<NextContinuationToken>([^<]+)</NextContinuationToken>")


class R2Error(RuntimeError):
    pass


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


class Bucket:
    """One bucket, addressed by path, signed per request."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str,
                 client: httpx.Client | None = None):
        self._host = urllib.parse.urlparse(endpoint).netloc or endpoint
        self._bucket = bucket
        self._access = access_key
        self._secret = secret_key
        self._client = client or httpx.Client(timeout=30.0)

    @classmethod
    def from_env(cls, client: httpx.Client | None = None) -> "Bucket | None":
        """None when unconfigured, so the source is simply skipped."""
        endpoint = os.environ.get("R2_ENDPOINT", "")
        bucket = os.environ.get("R2_BUCKET", "")
        access = os.environ.get("R2_ACCESS_KEY_ID", "")
        secret = os.environ.get("R2_SECRET_ACCESS_KEY", "")
        if not all((endpoint, bucket, access, secret)):
            return None
        return cls(endpoint, bucket, access, secret, client)

    def _request(self, method: str, key: str = "", body: bytes = b"",
                 query: str = "") -> httpx.Response:
        path = "/" + self._bucket + ("/" + urllib.parse.quote(key) if key else "")
        now = datetime.datetime.now(datetime.timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest() if body else _EMPTY_SHA

        canonical_headers = (f"host:{self._host}\n"
                             f"x-amz-content-sha256:{payload_hash}\n"
                             f"x-amz-date:{amzdate}\n")
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical = (f"{method}\n{path}\n{query}\n{canonical_headers}\n"
                     f"{signed_headers}\n{payload_hash}")
        scope = f"{datestamp}/{_REGION}/{_SERVICE}/aws4_request"
        to_sign = (f"{_ALGORITHM}\n{amzdate}\n{scope}\n"
                   f"{hashlib.sha256(canonical.encode()).hexdigest()}")

        key_bytes = _sign(("AWS4" + self._secret).encode(), datestamp)
        for part in (_REGION, _SERVICE, "aws4_request"):
            key_bytes = _sign(key_bytes, part)
        signature = hmac.new(key_bytes, to_sign.encode(), hashlib.sha256).hexdigest()

        url = f"https://{self._host}{path}" + (f"?{query}" if query else "")
        return self._client.request(
            method, url, content=body or None,
            headers={
                "x-amz-date": amzdate,
                "x-amz-content-sha256": payload_hash,
                "Authorization": (f"{_ALGORITHM} Credential={self._access}/{scope}, "
                                  f"SignedHeaders={signed_headers}, Signature={signature}"),
            },
        )

    def keys(self, prefix: str) -> list[str]:
        """Every key under a prefix, following continuation tokens."""
        found: list[str] = []
        token = ""
        while True:
            query = "list-type=2&max-keys=200&prefix=" + urllib.parse.quote(prefix, safe="")
            if token:
                query += "&continuation-token=" + urllib.parse.quote(token, safe="")
            response = self._request("GET", query=query)
            if response.status_code != 200:
                raise R2Error(f"listing {prefix!r} returned {response.status_code}")
            body = response.text
            found.extend(_KEY.findall(body))
            if not _TRUNCATED.search(body):
                return found
            nxt = _CONTINUATION.search(body)
            if not nxt:
                return found
            token = nxt.group(1)

    def get(self, key: str) -> bytes:
        response = self._request("GET", key)
        if response.status_code != 200:
            raise R2Error(f"get {key!r} returned {response.status_code}")
        return response.content

    def put(self, key: str, body: bytes) -> None:
        response = self._request("PUT", key, body)
        if response.status_code not in (200, 201):
            raise R2Error(f"put {key!r} returned {response.status_code}")

    def delete(self, key: str) -> None:
        response = self._request("DELETE", key)
        if response.status_code not in (200, 202, 204):
            raise R2Error(f"delete {key!r} returned {response.status_code}")

    def move(self, key: str, new_key: str) -> None:
        """Copy then delete. R2 supports server-side copy, but a message small
        enough to be an email is not worth a second code path."""
        self.put(new_key, self.get(key))
        self.delete(key)
