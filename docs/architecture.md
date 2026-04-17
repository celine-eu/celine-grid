# Architecture

## Overview

`celine-grid` is a FastAPI Backend-for-Frontend. It has no domain logic of its own — it orchestrates two external concerns:

1. **Read path** — proxy grid resilience data (wind/heat risk, substations, filters, summary) from the Digital Twin to the frontend, enforcing DSO network-ownership at the request boundary.
2. **Write path** — persist per-user alert rules and notification settings in PostgreSQL, then dispatch nudging events to the nudging-tool when the grid-resilience-flow pipeline completes.

```
Frontend ──► celine-grid BFF ──► Digital Twin API  (read)
                │
                ├──► PostgreSQL    (alert rules, notification settings)
                └──► nudging-tool  (alert dispatch)

MQTT broker ──► pipeline_listener ──► alert_dispatcher ──► nudging-tool
```

## Request lifecycle

Every protected request passes through two security layers before reaching a route handler:

1. **`PolicyMiddleware`** (`security/middleware.py`) — rejects requests with no recognisable token with `401` before any route handler is invoked. Public paths (`/health`, `/api/docs`, `/api/redoc`, `/api/openapi.json`) bypass this check.
2. **FastAPI dependency** (`api/deps.py`) — decodes the JWT via `JwtUser.from_token`, then delegates to `GridAccessPolicy` to perform an OPA evaluation for the specific action.

## Authentication

Tokens are accepted from two sources (checked in order):

- `x-auth-request-access-token` header — set by OAuth2 Proxy in production
- `Authorization: Bearer <token>` header — used for direct API calls / service accounts

The header name is configurable via `JWT_HEADER_NAME`.

## Authorization — OPA policy

`policies/grid.rego` (`package celine.grid.access`) defines three actions:

| Action | Who may proceed |
|---|---|
| `read` | DSO users whose org alias matches the requested `network_id`; service accounts with `grid.read` or `grid.admin` scope |
| `alerts.read` | Any authenticated DSO user (ownership enforced at DB query level by `user_id = sub`) |
| `alerts.write` | DSO users with `grid.alerts.write` or `grid.admin` scope |

The `GridAccessPolicy` class (`security/policy.py`) loads the Rego bundle once at import time and evaluates decisions per request. When the policy engine is unavailable (e.g. local dev without OPA), the policy falls back to permissive — `allow=True`.

DSO network identity comes from the Keycloak organisation claim on the JWT. The first organisation with `type=dso` becomes the user's `network_id`; `resolve_dso_network()` raises HTTP 403 if no such organisation is present.

## Grid data proxy

All grid endpoints live under `/api/grid/{network_id}/`. The `network_id` path parameter is validated against the user's DSO org alias (or scope for service accounts) before the request is forwarded to the Digital Twin via `celine.sdk.dt.DTClient`. DTApiError responses are mapped to appropriate HTTP status codes.

Available data surfaces:

- **Wind** — `wind/map`, `wind/bosco`, `wind/alert-distribution`, `wind/trend`
- **Heat** — `heat/map`, `heat/alert-distribution`, `heat/trend`
- **Substations** — `substations/map`
- **Metadata** — `filters`, `summary`

Each DT call is authenticated using client-credentials OIDC flow (`OidcClientCredentialsProvider`).

## Alert rules

Alert rules are stored in the `alert_rules` PostgreSQL table. Each rule belongs to a `user_id` (JWT `sub`) and a `network_id`, and carries:

- `risk_types` — JSON array of `"wind"` / `"heat"` (or both)
- `threshold` — `WARNING` or `ALERT`
- `active` — whether the rule participates in dispatch evaluation
- `recipients` — optional override email list

Rule ownership is enforced at the SQL query level (`WHERE user_id = :sub`), not only at the OPA layer.

## Pipeline listener and alert dispatch

`services/pipeline_listener.py` subscribes to `celine/pipelines/runs/+` on startup. When a `PipelineRunEvent` with `status=completed` and `flow=grid-resilience-flow` arrives, it calls `dispatch_grid_alerts()`.

The dispatcher (`services/alert_dispatcher.py`):

1. Fetches current `wind_alert_distribution` and `heat_alert_distribution` from the DT for the event's `namespace` (= `network_id`).
2. Loads all active `AlertRule` rows for that `network_id`.
3. For each rule, checks whether the distribution contains events at or above the rule's threshold (`WARNING` floor = `{WARNING, ALERT}`; `ALERT` floor = `{ALERT}`).
4. For each triggered rule, emits a `grid_alert` `DigitalTwinEvent` to the nudging-tool via `NudgingAdminClient`.

If the MQTT broker is unavailable at startup, the listener logs a warning and the rest of the service continues to operate normally.

## Database

PostgreSQL (async via SQLAlchemy + asyncpg). Two tables:

| Table | Purpose |
|---|---|
| `alert_rules` | Per-user grid alert rules |
| `notification_settings` | Per-user global notification preferences (email recipients, webhook URL) |

Migrations are managed by Alembic in the `alembic/` directory. The `docker-compose.yml` `migrate` service runs `alembic upgrade head` before the API container starts.

## Service dependencies

| Dependency | Role | Required |
|---|---|---|
| PostgreSQL | Alert rules and notification settings storage | Yes |
| Digital Twin API (`digital-twin`) | Grid risk data source | Yes (grid endpoints return 503 if unconfigured) |
| nudging-tool | Alert delivery | Yes (dispatch silently degrades on send failures) |
| MQTT broker | Pipeline completion events | No (startup warning; alert dispatch is inactive) |
| OPA / `celine.sdk.policies` | Fine-grained access control | No (permissive fallback) |

## Key design decisions

**DSO org alias as network_id** — the Keycloak organisation alias is used directly as the `network_id` for DT queries. No mapping table is needed; org management in Keycloak is the single source of truth.

**Permissive OPA fallback** — the policy engine falls back to allow-all when unavailable so development environments without a running OPA instance stay functional. Production deployments always have the policies directory present in the container.

**Pipeline listener vs polling** — alert dispatch is event-driven (MQTT) rather than scheduled. This avoids unnecessary DT queries and ensures alerts fire promptly after each pipeline run without coupling the BFF to a scheduler.

**Thin BFF pattern** — celine-grid performs no risk calculations. All domain logic lives in the Digital Twin. The BFF's job is routing, authentication, authorization, and persistence of user preferences.
