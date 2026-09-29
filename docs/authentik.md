# Authentik boundary

Production serves JobSeer only through the existing Authentik forward-auth
route. The application treats `X-authentik-username` as proof that the request
passed through that route and rejects every `/api/v1/*` request without it.
The Service must remain cluster-private, and the ingress or proxy must discard
any client-supplied `X-authentik-*` headers before Authentik adds its own.

State-changing requests require all three checks:

1. an Authentik username header;
2. a same-origin `Origin` when the browser supplies one;
3. the random token from `/api/v1/session` in both the Strict, HttpOnly cookie
   and `X-CSRF-Token` header.

Before enabling a structured profile route, verify from outside the cluster:

```bash
# No Authentik session: redirect at the ingress or 401 at the application.
curl -i https://jobs.example.invalid/api/v1/session

# Authenticated browser session: 200, a CSRF cookie, and the current username.
# Perform this check in browser developer tools; do not paste a session cookie
# into shell history.
```

Also verify that a direct request from another namespace cannot reach the
dashboard Service. Until these checks pass in the GitOps deployment, the
application returns 404 for `/api/v1/profile` and renders no Quick-fill UI by
default. After every verification in the deployment runbook passes, set
`JOBSCOUT_QUICK_FILL_ENABLED=true` on the dashboard Deployment to enable both
as one security boundary.
