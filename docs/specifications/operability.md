# Operability

Running, degrading, and the one thing that is currently wrong.

---

### REQ-0039 — the service is observable without an identity, and cheap to ask

`GET /health` answers `{"status": "ok"}` with no authentication, because a liveness probe
reaches the container before any proxy has attached anything to it.

`/api/docs`, `/api/redoc` and `/api/openapi.json` are also public. That publishes the full
route table to anyone who can reach the service — acceptable behind the cluster's ingress,
and a deliberate cost of keeping the schema available to the frontend.

`/api/ping` requires only a decodable token: no organisation, no scope. It is how a client
asks whether its token is still good.

The public set is matched by exact path, so `/api/docs/oauth2-redirect` is **not** public
and Swagger UI's interactive OAuth2 flow cannot complete here.

### REQ-0040 — an unavailable MQTT broker does not stop the service

Startup waits `MQTT_STARTUP_TIMEOUT_SECONDS` for the broker, then logs a warning and
carries on. The HTTP API is fully functional; alert dispatch is simply inactive, and
nothing at any endpoint says so.

The handler also refuses to dispatch before the Digital Twin and nudging clients exist,
rather than raising inside the broker's callback.

### REQ-0041 — an unavailable Digital Twin does not stop dispatch

The wind and heat distributions are fetched independently and each failure is swallowed, so
a heat rule still fires when the wind query is down.

**When both fail, dispatch sends nothing — the same observation as a genuinely calm day.**
A Digital Twin that is down therefore looks exactly like good weather, and nothing raises
an alarm about the absence of alerts.

### REQ-0042 — the client address is derived for logging only

`X-Forwarded-For` (first entry), then `X-Real-IP`, then the socket address, then
`"unknown"`.

All three headers are caller-controlled. This value is only ever logged and must not grow
into an authorisation input.

### REQ-0043 — no optional profile claim is load-bearing

Only `sub` and membership of a DSO organisation are required to boot the grid app. Every
other field on `MeUser` — `email`, `name`, `preferred_username`, `locale` — is decoration,
and a token carrying none of them still gets a `200`.

`email` was required while `JwtUser.email` is optional, so a DSO member whose token
carried no `email` claim got a `500` with an empty body from `/api/me` — the frontend's
first call, so the app did not load at all for them (#21).

**A conforming realm does supply it, and that is not enough.** `email` is its own OIDC
scope — not part of `profile` — so it is present only when the client requests the `email`
scope *and* the user has an address set. Two callers that reach this endpoint have neither
by construction: a client-credentials service account, which has no profile at all, and
any integration whose client was registered without the scope. The requirement is not that
the claim be absent in practice; it is that a missing optional claim must never be the
difference between a working application and a blank page.

### REQ-0044 — the reported language falls back from the token to the request

`/api/me` answers with the caller's preferred language as a lowercased primary subtag
(`it`, not `it-IT`), resolved in order:

1. the `locale` claim, which the standard OIDC `profile` scope supplies;
2. the highest-priority language in the request's `Accept-Language` header, ranked by
   `q` with ties keeping the order sent, `*` skipped and `q=0` dropped;
3. `null`.

No default language is invented. `en` would be indistinguishable to the frontend from a
caller who asked for English, and the frontend has its own chain — `localStorage`, then
this value, then `getLocaleFromNavigator()`, then `en` — which is the right place for a
default.

The subtag is reduced because `celine-frontend/apps/grid` matches the value against a
two-entry supported list, so anything region-qualified resolves to English there.
Reducing `pt-BR` to `pt` is a real loss of information and a deliberate one; a consumer
that needs the region changes this requirement.

Both sources are read defensively: a claim that is empty, blank, or not a string — Keycloak
maps multi-valued attributes to a list — falls through to the header rather than reaching
the response.

**Reading the claim from `.claims` is the load-bearing part.** `JwtUser` lifts a fixed list
of standard claims onto its own attributes and leaves everything else reachable only
through `.claims`; `locale` is not on that list. It was read with
`getattr(user, "locale", None)` and was therefore always `null`, whatever the realm sent
(#23). `name` on the line beside it was always correct, because `JwtUser` does model that
one — which is what made the mistake invisible in review, and why any field this service
exposes that the SDK does not model must come from `.claims`.
