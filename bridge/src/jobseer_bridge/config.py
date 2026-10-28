import base64
import ipaddress
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import __version__

DEFAULT_URL = "http://127.0.0.1:8080"
DEFAULT_USER = "cole"
TRUE = {"1", "true", "yes", "on"}
TOKEN_CMD_TIMEOUT = 5.0
HIDDEN = "[redacted]"


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# failures raise outside except blocks
def run_token_cmd(command: str, timeout: float = TOKEN_CMD_TIMEOUT) -> str:
    problem = None
    token = ""
    argv = []
    try:
        argv = shlex.split(command)
    except ValueError:
        problem = "JOBSEER_TOKEN_CMD is not a valid command line"
    if problem is None and not argv:
        problem = "JOBSEER_TOKEN_CMD is empty"
    done = None
    if problem is None:
        try:
            done = subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            problem = "JOBSEER_TOKEN_CMD timed out after %gs" % timeout
        except OSError:
            problem = "JOBSEER_TOKEN_CMD could not be started"
    if done is not None:
        if done.returncode != 0:
            problem = "JOBSEER_TOKEN_CMD failed with exit code %d" % done.returncode
        else:
            try:
                token = done.stdout.decode("utf-8").strip()
            except UnicodeDecodeError:
                problem = "JOBSEER_TOKEN_CMD printed non-text output"
            else:
                if not token:
                    problem = "JOBSEER_TOKEN_CMD printed nothing"
    if problem:
        raise ValueError(problem)
    return token


@dataclass(frozen=True, repr=False)
class Config:
    url: str = DEFAULT_URL
    readonly: bool = False
    cache_ttl: float = 10.0
    timeout: float = 15.0
    authentik_user: str | None = None
    authentik_app: str = "jobseer"
    authentik_outpost: str = "local"
    user: str = DEFAULT_USER
    token: str | None = field(default=None, compare=False)

    @classmethod
    def from_env(cls, env=None, runner=run_token_cmd) -> "Config":
        env = os.environ if env is None else env
        url = (env.get("JOBSEER_URL") or DEFAULT_URL).strip().rstrip("/")
        token = (env.get("JOBSEER_TOKEN") or "").strip() or None
        command = (env.get("JOBSEER_TOKEN_CMD") or "").strip()
        # skip prompts when token given
        if token is None and command:
            token = runner(command)
        return cls(
            url=url,
            readonly=(env.get("JOBSEER_READONLY") or "").strip().lower() in TRUE,
            cache_ttl=float(env.get("JOBSEER_CACHE_TTL") or cls.cache_ttl),
            authentik_user=env.get("JOBSEER_AUTHENTIK_USER") or None,
            authentik_app=env.get("JOBSEER_AUTHENTIK_APP") or cls.authentik_app,
            authentik_outpost=env.get("JOBSEER_AUTHENTIK_OUTPOST") or cls.authentik_outpost,
            user=(env.get("JOBSEER_USER") or "").strip() or DEFAULT_USER,
            token=token,
        )

    # urls end up in results
    def __post_init__(self):
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("JOBSEER_URL must be an http(s) address")
        if "@" in parts.netloc or "?" in self.url or "#" in self.url:
            raise ValueError("JOBSEER_URL must not contain credentials, a query or a fragment")
        if self.token is None:
            return
        if not self.token.isprintable() or not self.token.isascii() or " " in self.token:
            raise ValueError("the token must be printable text without spaces")
        if not self.user.isprintable() or ":" in self.user:
            raise ValueError("JOBSEER_USER must be plain text without a colon")
        if parts.scheme != "https" and not is_loopback(parts.hostname):
            raise ValueError("a token is only sent over https, or http to a loopback address")

    def __repr__(self) -> str:
        return "Config(url=%r, readonly=%r, auth=%s)" % (self.url, self.readonly, self.auth_mode)

    __str__ = __repr__

    @property
    def origin(self) -> str:
        parts = urlsplit(self.url)
        return "%s://%s" % (parts.scheme, parts.netloc)

    @property
    def auth_mode(self) -> str:
        if self.token:
            return "basic as %s, token %s" % (self.user, HIDDEN)
        if self.authentik_user:
            return "authentik headers"
        return "none"

    def redact(self, text: str) -> str:
        if not self.token:
            return text
        for secret in (self.token, self.basic_value()):
            text = text.replace(secret, HIDDEN)
        return text

    # server text may echo secrets
    def scrub(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {self.scrub(key): self.scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(item) for item in value]
        return value

    def basic_value(self) -> str:
        return base64.b64encode(("%s:%s" % (self.user, self.token)).encode()).decode()

    # mixed credentials confuse the proxy
    def auth_headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": "Basic " + self.basic_value()}
        if not self.authentik_user:
            return {}
        return {
            "X-authentik-username": self.authentik_user,
            "X-authentik-meta-app": self.authentik_app,
            "X-authentik-meta-outpost": self.authentik_outpost,
        }

    def request_headers(self) -> dict[str, str]:
        return {"User-Agent": "jobseer-bridge/%s" % __version__, **self.auth_headers()}
