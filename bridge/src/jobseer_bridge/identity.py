from mcp.server.auth.middleware.auth_context import get_access_token


# stdio has no caller identity
def current_identity() -> tuple[str, str] | None:
    token = get_access_token()
    if token is None:
        return None
    return (token.subject or "", token.client_id)
