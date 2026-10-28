import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:8080"
TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    url: str = DEFAULT_URL
    readonly: bool = False
    cache_ttl: float = 10.0
    timeout: float = 15.0
    authentik_user: str | None = None
    authentik_app: str = "jobseer"
    authentik_outpost: str = "local"

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        url = (env.get("JOBSEER_URL") or DEFAULT_URL).strip().rstrip("/")
        return cls(url=url, readonly=(env.get("JOBSEER_READONLY") or "").strip().lower() in TRUE, cache_ttl=float(env.get("JOBSEER_CACHE_TTL") or cls.cache_ttl), authentik_user=env.get("JOBSEER_AUTHENTIK_USER") or None, authentik_app=env.get("JOBSEER_AUTHENTIK_APP") or cls.authentik_app, authentik_outpost=env.get("JOBSEER_AUTHENTIK_OUTPOST") or cls.authentik_outpost)

    # urls end up in results
    def __post_init__(self):
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("JOBSEER_URL must be an http(s) address")
        if "@" in parts.netloc or "?" in self.url or "#" in self.url:
            raise ValueError("JOBSEER_URL must not contain credentials, a query or a fragment")

    @property
    def origin(self) -> str:
        parts = urlsplit(self.url)
        return "%s://%s" % (parts.scheme, parts.netloc)

    # demo servers skip auth headers
    def auth_headers(self) -> dict[str, str]:
        if not self.authentik_user:
            return {}
        return {
            "X-authentik-username": self.authentik_user,
            "X-authentik-meta-app": self.authentik_app,
            "X-authentik-meta-outpost": self.authentik_outpost,
        }
