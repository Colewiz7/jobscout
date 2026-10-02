"""Refresh company icons from verified public sites, never guessed ATS branding.

The daily job stores small raster icons in Postgres so a new company can gain
an icon without rebuilding the dashboard. An unverifiable match stays initials.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import logging
import pathlib
import re
import socket
import struct
import urllib.parse

import httpx
from bs4 import BeautifulSoup

from . import db

log = logging.getLogger(__name__)
ROOT = pathlib.Path(__file__).resolve().parents[2]
DOMAIN_PATH = ROOT / "config/company-domains.json"
STATIC_MANIFEST = pathlib.Path(__file__).with_name("static") / "company-logos" / "manifest.json"
MAX_ICON = 65_536
MAX_HTML = 131_072
ATS_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}
BOARD_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
WORKDAY_HOST = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$")


def _public_host(host: str) -> bool:
    if not host or "." not in host or host.endswith(".local"):
        return False
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(addresses) and all(
        ipaddress.ip_address(item[4][0]).is_global for item in addresses
    )


def _read(client: httpx.Client, url: str, limit: int) -> bytes | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        return None
    if not _public_host(parsed.hostname or ""):
        return None
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code != 200:
                return None
            data = bytearray()
            for part in response.iter_bytes():
                data.extend(part)
                if len(data) > limit:
                    return None
            return bytes(data)
    except httpx.HTTPError:
        return None


def _site_html(client: httpx.Client, domain: str) -> str:
    # Many official sites only answer one of these hostnames. Redirects stay
    # disabled: a posting-controlled URL must never pivot into private egress.
    for host in (domain, f"www.{domain}"):
        body = _read(client, f"https://{host}/", MAX_HTML)
        if body:
            return body.decode("utf-8", "replace")
    return ""


def _brand_word(name: str) -> str:
    return next((part for part in db.company_key(name).split() if len(part) >= 4), "")


def _site_matches(name: str, domain: str, html: str) -> bool:
    brand = _brand_word(name)
    if not brand or brand not in domain.split(".")[0].replace("-", ""):
        return False
    soup = BeautifulSoup(html, "html.parser")
    site_name = soup.find("meta", attrs={"property": "og:site_name"})
    title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "", site_name.get("content", "") if site_name else ""))
    return brand in re.sub(r"[^a-z0-9]+", "", title.casefold())


def _icon_type(data: bytes) -> str | None:
    if len(data) > MAX_ICON:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return "image/png" if 16 <= width <= 512 and 16 <= height <= 512 else None
    if data.startswith(b"\x00\x00\x01\x00") and len(data) >= 22:
        return "image/x-icon"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _candidate_domains(name: str, urls: list[str]) -> list[str]:
    company = "".join(db.company_key(name).split())
    candidates: list[str] = []
    for url in urls:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https":
            continue
        if host in ATS_HOSTS:
            slug = parsed.path.strip("/").split("/", 1)[0].casefold()
            if BOARD_SLUG.fullmatch(slug) and slug.replace("-", "") == company:
                candidates.append(f"{slug}.com")
        elif WORKDAY_HOST.fullmatch(host):
            tenant = host.split(".", 1)[0]
            if tenant.replace("-", "") == company:
                candidates.append(f"{tenant}.com")
        elif host and not host.endswith(".myworkdayjobs.com"):
            labels = host.removeprefix("www.").split(".")
            if labels[0] in {"careers", "jobs", "apply"} and len(labels) > 2:
                labels = labels[1:]
            if len(labels) >= 2:
                candidates.append(".".join(labels))
    if company and len(company) <= 50:
        candidates.append(f"{company}.com")
    return list(dict.fromkeys(candidates))[:3]


def _favicon(client: httpx.Client, domain: str, placeholder_hash: bytes) -> tuple[bytes, str] | None:
    url = "https://www.google.com/s2/favicons?" + urllib.parse.urlencode({"domain": domain, "sz": "64"})
    data = _read(client, url, MAX_ICON)
    media_type = _icon_type(data or b"")
    if not media_type or hashlib.sha256(data).digest() == placeholder_hash:
        return None
    return data, media_type


def refresh(conn, *, client: httpx.Client | None = None, now: dt.datetime | None = None) -> dict[str, int]:
    """Refresh missing icons daily and successful icons monthly."""
    now = now or dt.datetime.now(dt.timezone.utc)
    domains = {db.company_key(name): domain for name, domain in json.loads(DOMAIN_PATH.read_text()).items()}
    static = {db.company_key(name) for name in json.loads(STATIC_MANIFEST.read_text())}
    with conn.cursor() as cur:
        cur.execute("select key, name from companies order by name")
        companies = cur.fetchall()
        cur.execute("select company, url from postings where url <> ''")
        urls: dict[str, list[str]] = {}
        for row in cur.fetchall():
            urls.setdefault(db.company_key(row["company"]), []).append(row["url"])
        cur.execute("select company_key, checked_at, logo_data from company_assets")
        checked = {row["company_key"]: row for row in cur.fetchall()}
    counts = {"checked": 0, "added": 0, "unverified": 0}
    own_client = client is None
    client = client or httpx.Client(timeout=5, follow_redirects=False, headers={"User-Agent": "JobSeer company icon refresh"})
    try:
        invalid = _read(client, "https://www.google.com/s2/favicons?domain=jobseer-no-such-company.invalid&sz=64", MAX_ICON)
        if not invalid or not _icon_type(invalid):
            log.warning("generic favicon reference unavailable; leaving unknown companies as initials")
            return counts
        placeholder_hash = hashlib.sha256(invalid).digest()
        for company in companies:
            key, name = company["key"], company["name"]
            if key in static:
                continue
            prior = checked.get(key)
            if prior and now - prior["checked_at"] < dt.timedelta(days=30 if prior["logo_data"] else 1):
                continue
            counts["checked"] += 1
            options = [domains[key]] if key in domains else _candidate_domains(name, urls.get(key, []))
            found = None
            for domain in options:
                if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,250}", domain):
                    continue
                site = _site_html(client, domain)
                if not site or not _site_matches(name, domain, site):
                    continue
                icon = _favicon(client, domain, placeholder_hash)
                if icon:
                    found = (domain, *icon, "curated" if key in domains else "verified_site")
                    break
            with conn.cursor() as cur:
                cur.execute(
                    """insert into company_assets
                       (company_key, company_name, domain, logo_data, media_type, source, checked_at)
                       values (%s, %s, %s, %s, %s, %s, %s)
                       on conflict (company_key) do update set
                         company_name = excluded.company_name,
                         domain = coalesce(excluded.domain, company_assets.domain),
                         logo_data = coalesce(excluded.logo_data, company_assets.logo_data),
                         media_type = coalesce(excluded.media_type, company_assets.media_type),
                         source = coalesce(excluded.source, company_assets.source),
                         checked_at = excluded.checked_at""",
                    (key, name, found[0] if found else None, found[1] if found else None,
                     found[2] if found else None, found[3] if found else None, now),
                )
            conn.commit()
            if found:
                counts["added"] += 1
            else:
                counts["unverified"] += 1
                log.info("%s: no verified public favicon; using initials", name)
    finally:
        if own_client:
            client.close()
    return counts
