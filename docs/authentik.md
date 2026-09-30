# Authentik boundary

Production routes `jobs.colewiz.dev` through Cloudflare Tunnel to the
Authentik embedded proxy outpost, then to the JobSeer ClusterIP. There is no
Traefik or public JobSeer Service. The JobSeer NetworkPolicy admits only the
outpost/server pods selected by both namespace and pod labels.

Every `/api/v1/*` request requires Authentik's username, application, and
outpost metadata headers. JobSeer also requires the configured `cole` user and
`jobseer` application. Authentik's proxy-provider upstream contract does not
provide a signed `X-authentik-jwt` in the deployed version, so this header
check must remain paired with the single-user Authentik binding and the
pod-level NetworkPolicy. A public request with a forged username but no login
must redirect to Authentik; it must not reach the dashboard.

State-changing requests additionally require a same-origin `Origin`, when
present, and the random `/api/v1/session` token in both a Strict, HttpOnly
cookie and the `X-CSRF-Token` header.

Production Quick-fill was enabled on 2026-09-30 after the five checks in the
GitOps `apps/jobscout/docs/deploy/verify.md` runbook passed, including the
single-user binding and second-user denial. The profile was initially empty;
the local `config/profile.seed.json` is not production data. The Super+J
snippet import streamed personal answers into the database without committing
them to git. Do not print profile responses or session cookies in logs.
