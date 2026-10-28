import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import is_loopback

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_SCOPE = "jobseer:mcp"
DEFAULT_BODY = 262144
PATH = "/mcp"
DEFAULT_RATE = 60
DEFAULT_WINDOW = 60

REQUIRED = {
    "JOBSEER_AUTH_ISSUER": "issuer",
    "JOBSEER_AUTH_JWKS_URL": "jwks_url",
    "JOBSEER_AUTH_AUDIENCE": "audience",
    "JOBSEER_AUTH_SUBJECT": "subject",
    "JOBSEER_PUBLIC_URL": "public_url",
    "JOBSEER_ALLOWED_HOSTS": "allowed_hosts",
    "JOBSEER_ALLOWED_ORIGINS": "allowed_origins",
}


LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
NAME = re.compile(r"^%s(?:\.%s)*$" % (LABEL, LABEL))
V6 = re.compile(r"^\[([0-9a-f:.]+)\](?::([0-9]{1,5}))?$")
PORT = re.compile(r"^(.+):([0-9]{1,5})$")


# patterns defeat rebinding protection
def exact_host(name: str, entry: str) -> str:
    entry = entry.lower()
    wrapped = V6.match(entry)
    if wrapped:
        try:
            ipaddress.IPv6Address(wrapped.group(1))
        except ValueError:
            raise ValueError("%s has an invalid entry" % name) from None
        port = wrapped.group(2)
    else:
        found = PORT.match(entry)
        host, port = (found.group(1), found.group(2)) if found else (entry, None)
        if not NAME.match(host):
            raise ValueError("%s entries must be an exact host or host:port, no wildcards or schemes" % name)
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError("%s has an invalid port" % name)
    return entry


def exact_origin(name: str, entry: str, loopback_bind: bool) -> str:
    entry = entry.lower()
    if "*" in entry:
        raise ValueError("%s must not contain wildcards" % name)
    parts = urlsplit(entry)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("%s entries must look like https://host[:port]" % name)
    if "@" in parts.netloc or parts.path or parts.query or parts.fragment:
        raise ValueError("%s entries must be a bare origin without a path, query or credentials" % name)
    exact_host(name, parts.netloc)
    if parts.scheme == "http" and not (loopback_bind and is_loopback(parts.hostname)):
        raise ValueError("%s allows http only for a loopback origin on a loopback bind" % name)
    return entry


def strict_list(name: str, value: str) -> list[str]:
    pieces = [part.strip() for part in value.split(",")]
    if not all(pieces):
        raise ValueError("%s has an empty entry" % name)
    return pieces


def words(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


# bearer tokens need transport security
def check_address(name: str, value: str, path: str | None = None) -> None:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("%s must be an http(s) address" % name)
    if parts.scheme != "https" and not is_loopback(parts.hostname):
        raise ValueError("%s must use https unless it is a loopback address" % name)
    if "@" in parts.netloc or parts.query or parts.fragment:
        raise ValueError("%s must not contain credentials, a query or a fragment" % name)
    if path is not None and parts.path != path:
        raise ValueError("%s must end in %s" % (name, path))


def parse_tools(value: str | None):
    text = (value or "").strip().lower()
    if text in {"", "read"}:
        return "read"
    if text == "all":
        return "all"
    return frozenset(words(value))


def whole(env, name: str, default: int, low: int, high: int) -> int:
    raw = (env.get(name) or "").strip()
    try:
        number = int(raw) if raw else default
    except ValueError:
        raise ValueError("%s must be a whole number" % name) from None
    if not low <= number <= high:
        raise ValueError("%s must be between %d and %d" % (name, low, high))
    return number


@dataclass(frozen=True)
class RemoteSettings:
    issuer: str
    jwks_url: str
    audience: str
    subject: str
    public_url: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    scope: str = DEFAULT_SCOPE
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_body: int = DEFAULT_BODY
    tools: object = "read"
    rate_limit: int = DEFAULT_RATE
    rate_window: int = DEFAULT_WINDOW

    @classmethod
    def from_env(cls, env=None) -> "RemoteSettings":
        env = os.environ if env is None else env
        found = {attr: (env.get(name) or "").strip() for name, attr in REQUIRED.items()}
        missing = [name for name, attr in REQUIRED.items() if not found[attr]]
        if missing:
            raise ValueError("serve needs these set: %s" % ", ".join(missing))
        check_address("JOBSEER_AUTH_ISSUER", found["issuer"])
        check_address("JOBSEER_AUTH_JWKS_URL", found["jwks_url"])
        check_address("JOBSEER_PUBLIC_URL", found["public_url"], PATH)
        host = (env.get("JOBSEER_BIND_HOST") or "").strip() or DEFAULT_HOST
        hosts = tuple(exact_host("JOBSEER_ALLOWED_HOSTS", item) for item in strict_list("JOBSEER_ALLOWED_HOSTS", found.pop("allowed_hosts")))
        origins = tuple(exact_origin("JOBSEER_ALLOWED_ORIGINS", item, is_loopback(host)) for item in strict_list("JOBSEER_ALLOWED_ORIGINS", found.pop("allowed_origins")))
        return cls(
            **found,
            allowed_hosts=hosts,
            allowed_origins=origins,
            scope=(env.get("JOBSEER_AUTH_SCOPE") or "").strip() or DEFAULT_SCOPE,
            host=host,
            port=whole(env, "JOBSEER_BIND_PORT", DEFAULT_PORT, 1, 65535),
            max_body=whole(env, "JOBSEER_MAX_BODY", DEFAULT_BODY, 1024, 16777216),
            tools=parse_tools(env.get("JOBSEER_TOOLS")),
            rate_limit=whole(env, "JOBSEER_RATE_LIMIT", DEFAULT_RATE, 0, 100000),
            rate_window=whole(env, "JOBSEER_RATE_WINDOW", DEFAULT_WINDOW, 1, 86400),
        )
