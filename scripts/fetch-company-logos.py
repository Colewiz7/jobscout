#!/usr/bin/env python3
"""Vendor verified company favicons for the inbox and Companies views.

The browser only requests these same-origin files. Keep domains curated: an
ambiguous company name should show initials rather than another firm's mark.
Run this script when config/company-domains.json changes, then review the
generated icons before committing them.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import pathlib
import re
import struct
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOMAINS = ROOT / "config/company-domains.json"
OUTPUT = ROOT / "src/jobscout/static/company-logos"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def slug(company: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", company.casefold())).strip("-")


def fetch_icon(domain: str) -> tuple[bytes, str]:
    url = "https://www.google.com/s2/favicons?" + urllib.parse.urlencode(
        {"domain": domain, "sz": "64"}
    )
    request = urllib.request.Request(url, headers={"User-Agent": "JobSeer logo refresh"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(65_537)
        if len(data) > 65_536:
            raise ValueError("logo service returned an oversized image")
        if data.startswith(PNG_SIGNATURE):
            width, height = struct.unpack(">II", data[16:24])
            if not 16 <= width <= 256 or not 16 <= height <= 256:
                raise ValueError("unexpected logo dimensions")
            return data, "png"
        if data.startswith(b"\xff\xd8\xff") and response.headers.get_content_type() == "image/jpeg":
            return data, "jpg"
        raise ValueError("logo service did not return a PNG or JPEG")


def main() -> None:
    companies = json.loads(DOMAINS.read_text())
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        placeholder = hashlib.sha256(fetch_icon("jobseer-no-such-company.invalid")[0]).digest()
    except OSError:
        placeholder = None

    def one(item: tuple[str, str]) -> tuple[str, str | None, str | None]:
        company, domain = item
        try:
            data, extension = fetch_icon(domain)
            if placeholder and hashlib.sha256(data).digest() == placeholder:
                raise ValueError("only a generic placeholder was available")
            filename = f"{slug(company)}.{extension}"
            (OUTPUT / filename).write_bytes(data)
            return company, filename, None
        except (OSError, ValueError) as error:
            return company, None, str(error)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, companies.items()))
    manifest = {company: filename for company, filename, error in results if filename}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for company, _, error in results:
        if error:
            print(f"No icon for {company}: {error}")
    print(f"Saved {len(manifest)} company icons to {OUTPUT}")


if __name__ == "__main__":
    main()
