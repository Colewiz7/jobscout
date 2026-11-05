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
import xml.etree.ElementTree as ET

import httpx

log = logging.getLogger(__name__)

_ALGORITHM = "AWS4-HMAC-SHA256"
_REGION = "auto"          # R2 has no regions but the signature needs a value
_SERVICE = "s3"
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()



def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_listing(text: str) -> tuple[list[tuple[str, str]], bool, str]:
    # real parser decodes entities
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise R2Error("listing carried a DTD")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise R2Error("listing was not valid XML")
    entries: list[tuple[str, str]] = []
    truncated, token = False, ""
    for child in root:
        name = _local(child.tag)
        if name == "Contents":
            fields = {_local(item.tag): item.text or "" for item in child}
            if fields.get("Key"):
                entries.append((fields["Key"], fields.get("LastModified", "")))
        elif name == "IsTruncated":
            truncated = (child.text or "").strip().lower() == "true"
        elif name == "NextContinuationToken":
            token = (child.text or "").strip()
    return entries, truncated, token


class R2Error(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


class Bucket:
    """One bucket, addressed by path, signed per request."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str,
                 client: httpx.Client | None = None, get_timeout: float | None = None):
        self._host = urllib.parse.urlparse(endpoint).netloc or endpoint
        self._bucket = bucket
        self._access = access_key
        self._secret = secret_key
        self._client = client or httpx.Client(timeout=30.0)
        # short reads, stuck keys cheap
        self._get_timeout = get_timeout

    @classmethod
    def from_env(cls, client: httpx.Client | None = None, get_timeout: float | None = None) -> "Bucket | None":
        """None when unconfigured, so the source is simply skipped."""
        endpoint = os.environ.get("R2_ENDPOINT", "")
        bucket = os.environ.get("R2_BUCKET", "")
        access = os.environ.get("R2_ACCESS_KEY_ID", "")
        secret = os.environ.get("R2_SECRET_ACCESS_KEY", "")
        if not all((endpoint, bucket, access, secret)):
            return None
        return cls(endpoint, bucket, access, secret, client, get_timeout)

    def _request(self, method: str, key: str = "", body: bytes = b"",
                 query: str = "", timeout: float | None = None) -> httpx.Response:
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
            timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            headers={
                "x-amz-date": amzdate,
                "x-amz-content-sha256": payload_hash,
                "Authorization": (f"{_ALGORITHM} Credential={self._access}/{scope}, "
                                  f"SignedHeaders={signed_headers}, Signature={signature}"),
            },
        )

    def keys(self, prefix: str) -> list[str]:
        return [key for key, _ in self.entries(prefix)]

    def entries(self, prefix: str) -> list[tuple[str, str]]:
        """Every (key, last modified) under a prefix, following continuation tokens."""
        found: list[tuple[str, str]] = []
        token = ""
        used: set[str] = set()
        while True:
            params = {"list-type": "2", "max-keys": "200", "prefix": prefix}
            if token:
                params["continuation-token"] = token
            # sorted, as signing requires
            query = "&".join("%s=%s" % (name, urllib.parse.quote(value, safe="")) for name, value in sorted(params.items()))
            response = self._request("GET", query=query)
            if response.status_code != 200:
                raise R2Error(f"listing {prefix!r} returned {response.status_code}")
            page, truncated, token = _parse_listing(response.text)
            found.extend(page)
            if not truncated:
                return found
            # a partial list is not complete
            if not token:
                raise R2Error(f"listing {prefix!r} was truncated with no continuation token")
            if token in used:
                raise R2Error(f"listing {prefix!r} repeated a continuation token")
            used.add(token)

    def get(self, key: str) -> bytes:
        response = self._request("GET", key, timeout=self._get_timeout)
        if response.status_code != 200:
            raise R2Error(f"get {key!r} returned {response.status_code}", response.status_code)
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
