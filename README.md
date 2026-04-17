# CELINE Grid

Backend-for-Frontend (BFF) for the **Grid Resilience** UI (`celine-frontend/apps/grid`).

It acts as the secure API gateway between the grid dashboard and the CELINE platform, proxying risk data from the Digital Twin and managing per-operator alert rules and notification preferences.

## Features

- **Grid risk data proxy** — forwards wind and heat resilience data (maps, alert distributions, trends, substation layouts, filter metadata, summary) from the Digital Twin to the frontend, scoped to the operator's DSO network
- **Alert rules** — per-user CRUD for wind/heat risk alert rules, each carrying a threshold (`WARNING` or `ALERT`) and optional recipient override
- **Notification settings** — per-user email recipients and webhook URL for alert delivery
- **Automated alert dispatch** — listens on MQTT for `grid-resilience-flow` pipeline completions, evaluates active alert rules against live DT distributions, and sends nudging events via the nudging-tool
- **OPA-enforced access control** — fine-grained authorization via `policies/grid.rego`; DSO org membership drives network ownership; service accounts use OAuth2 scopes

## API

The service runs on port `8015`. Interactive docs are available at `/api/docs`.

| Group | Endpoints |
|---|---|
| **user** | `GET /api/me` |
| **grid** | `GET /api/grid/{network_id}/wind/map`, `/wind/bosco`, `/wind/alert-distribution`, `/wind/trend` |
| | `GET /api/grid/{network_id}/heat/map`, `/heat/alert-distribution`, `/heat/trend` |
| | `GET /api/grid/{network_id}/substations/map`, `/filters`, `/summary` |
| **alerts** | `GET/POST /api/alert-rules`, `PATCH/DELETE /api/alert-rules/{id}` |
| | `GET/PUT /api/notification-settings` |
| **ops** | `GET /health` |

## Local development

### Prerequisites

- Python 3.12+, `uv`
- PostgreSQL at `localhost:15432` (credentials `postgres:securepassword123`)
- MQTT broker (optional — the listener degrades gracefully if unavailable at startup)

### Setup

```bash
uv sync
docker compose run --rm db-create    # create the 'grid' database
task alembic:upgrade                 # apply migrations
task run                             # hot-reload on :8015
```

For debugpy attach on port `48015`:

```bash
task debug
```

### Docker Compose

```bash
docker compose up
```

This runs `db-create`, `migrate`, and the `api` container in order.

## Configuration

All settings are read from environment variables or a `.env` file. The table below lists the most relevant variables.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/grid` | PostgreSQL DSN |
| `DIGITAL_TWIN_API_URL` | `http://host.docker.internal:8002` | Digital Twin base URL |
| `NUDGING_API_URL` | `http://host.docker.internal:8016` | nudging-tool base URL |
| `CELINE_OIDC_CLIENT_SECRET` | `svc-grid` | OIDC client secret |
| `CORS_ORIGINS` | `["http://localhost:3006"]` | Allowed CORS origins |
| `CELINE_POLICIES_DIR` | `./policies` | Directory containing `.rego` policy files |
| `GRID_PIPELINE_FLOW` | `grid-resilience-flow` | Prefect flow name to listen for |
| `MQTT__HOST` | `localhost` | MQTT broker host |
| `JWT_HEADER_NAME` | `x-auth-request-access-token` | Header carrying the bearer token (set by OAuth2 Proxy) |

## Database migrations

```bash
task alembic:upgrade               # apply all pending migrations
task alembic:create -- "my change" # autogenerate a new migration
```

## Release

```bash
task release   # bumps version via semantic-release, pushes commit + tag
```

The CI pipeline in `.github/workflows/release.yml` builds and publishes the Docker image on each version tag.
