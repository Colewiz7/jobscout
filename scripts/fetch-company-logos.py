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
DIRECT_ICONS = {
    "devonenergy.com": "https://www.devonenergy.com/favicons/favicon-96x96.png",
    "tel.com": "https://www.tel.com/irta3a00000001ah-img/irta3a00000001at.png",
    "mwam.com": "https://www.mwam.com/favicon.ico",
}


def slug(company: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", company.casefold())).strip("-")


def fetch_icon(domain: str) -> tuple[bytes, str]:
    urls = [
        "https://www.google.com/s2/favicons?" + urllib.parse.urlencode({"domain": domain, "sz": "64"}),
        f"https://icons.duckduckgo.com/ip3/{domain}.ico",
    ]
    if domain in DIRECT_ICONS:
        urls.insert(0, DIRECT_ICONS[domain])
    last_error: Exception | None = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "JobSeer logo refresh"})
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read(65_537)
                if len(data) > 65_536:
                    raise ValueError("logo service returned an oversized image")
                if data.startswith(PNG_SIGNATURE):
                    width, height = struct.unpack(">II", data[16:24])
                    if not 16 <= width <= 512 or not 16 <= height <= 512:
                        raise ValueError("unexpected logo dimensions")
                    return data, "png"
                if data.startswith(b"\xff\xd8\xff") and response.headers.get_content_type() == "image/jpeg":
                    return data, "jpg"
                if data.startswith(b"\x00\x00\x01\x00") and len(data) >= 22:
                    return data, "ico"
                raise ValueError("logo service did not return a supported image")
        except (OSError, ValueError) as error:
            last_error = error
    raise ValueError(str(last_error or "no usable icon"))


def main() -> None:
    companies = json.loads(DOMAINS.read_text())
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUTPUT / "manifest.json"
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    try:
        placeholder = hashlib.sha256(fetch_icon("jobseer-no-such-company.invalid")[0]).digest()
    except (OSError, ValueError):
        placeholder = None

    def one(item: tuple[str, str]) -> tuple[str, str | None, str | None]:
        company, domain = item
        filename = existing.get(company)
        if filename and (OUTPUT / filename).is_file():
            return company, filename, None
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
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    for company, _, error in results:
        if error:
            print(f"No icon for {company}: {error}")
    print(f"Saved {len(manifest)} company icons to {OUTPUT}")


if __name__ == "__main__":
    main()
