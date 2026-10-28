import socket

from .audit import AuditLog
from .bridge import Bridge
from .config import Config
from .ratelimit import RateLimiter

INSTRUCTIONS = "JobSeer inbox and tracker actions. Every result names the target server it ran against."


def build(config: Config, client=None, confirms=None):
    from mcp.server.fastmcp import FastMCP

    bridge = Bridge(config, client, confirms)
    server = FastMCP("jobseer", instructions=INSTRUCTIONS, log_level="WARNING")
    bridge.register(server)
    return server, bridge


def build_remote(config: Config, settings, client=None, confirms=None, keys=None):
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import PlainTextResponse

    from .auth import JwtVerifier, KeySet

    verifier = JwtVerifier(issuer=settings.issuer, audience=settings.audience, subject=settings.subject, scope=settings.scope, keys=keys or KeySet(settings.jwks_url))
    limit = RateLimiter(settings.rate_limit, settings.rate_window) if settings.rate_limit else None
    audit = AuditLog(settings.audit_log, config.scrub) if settings.audit_log else None
    bridge = Bridge(config, client, confirms, selection=settings.tools, limiter=limit, remote=True, audit=audit, resolver=socket.getaddrinfo)
    server = FastMCP(
        "jobseer",
        instructions=INSTRUCTIONS,
        log_level="WARNING",
        host=settings.host,
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=settings.max_body,
        token_verifier=verifier,
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.public_url, required_scopes=[settings.scope], validate_token_resource=False),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=list(settings.allowed_hosts), allowed_origins=list(settings.allowed_origins)),
    )

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        return PlainTextResponse("ok")

    bridge.register(server)
    return server, bridge
