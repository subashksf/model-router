# Deployment Specification

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Deployment Targets

| Target | Description | Status |
|--------|-------------|--------|
| Local (Docker Compose) | Single-machine dev/demo | MVP |
| Self-hosted (Docker Compose / Kubernetes) | Customer on-premises | MVP |
| Managed cloud (SaaS) | Anthropic-hosted multi-tenant | Post-MVP |

This spec covers the MVP Docker Compose deployment.

---

## 2. Prerequisites

- Docker Engine ≥ 24
- Docker Compose V2 (plugin, not standalone)
- `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`

---

## 3. Quick Start

```bash
git clone <repo>
cd model-router
cp .env.example .env
# Edit .env: add API keys, optionally change Postgres credentials

docker compose up --build
```

Services:
- Gateway: http://localhost:8000
- Dashboard: http://localhost:3000
- Postgres/TimescaleDB: localhost:5432

**First-time startup sequence:**
1. Postgres starts and runs `001_initial.sql` (creates tables, hypertable, continuous aggregate).
2. Gateway starts after Postgres health check passes.
3. Dashboard starts after gateway starts.

---

## 4. Environment Variables

All variables are read from `.env` at compose startup. See `.env.example` for the full list.

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key. Required if any policy tier uses `provider: anthropic`. |
| `OPENAI_API_KEY` | OpenAI API key. Required if any policy tier uses `provider: openai`. |
| `POSTGRES_PASSWORD` | Postgres password. Must match across `db` and `gateway` service configs. |

### Optional (with defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://router:secret@db:5432/model_router` | Full async connection string. Override for external Postgres. |
| `POSTGRES_DB` | `model_router` | Database name. |
| `POSTGRES_USER` | `router` | Database user. |
| `DEFAULT_POLICY` | `default_policy.yaml` | Filename (not path) of the default routing policy. |
| `POLICIES_DIR` | `/app/policies` | Directory where policy YAML files are mounted. |
| `GATEWAY_API_KEY` | (empty) | If set, enables bearer token auth on gateway routes (post-MVP). |
| `NEXT_PUBLIC_GATEWAY_URL` | `http://localhost:8000` | Gateway URL as seen from the dashboard container or browser. |

---

## 5. Service Architecture (Docker Compose)

```
┌─────────────────────────────────────────────────────┐
│  Docker Compose network: model-router_default        │
│                                                      │
│  ┌──────────┐    ┌──────────┐    ┌───────────────┐  │
│  │ gateway  │    │dashboard │    │      db        │  │
│  │ :8000    │◄───│ :3000    │    │ timescaledb   │  │
│  │          │    │          │    │ :5432         │  │
│  └────┬─────┘    └──────────┘    └───────────────┘  │
│       │                                  ▲           │
│       └──────────────────────────────────┘           │
└─────────────────────────────────────────────────────┘

Published ports (host → container):
  8000 → gateway:8000
  3000 → dashboard:3000
  5432 → db:5432 (omit in production)
```

---

## 6. Health Checks

### Gateway
FastAPI serves a health check at `GET /health` (to be added):
```python
@app.get("/health")
async def health():
    return {"status": "ok"}
```

Docker Compose healthcheck (to add to `docker-compose.yml`):
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 10s
  timeout: 5s
  retries: 3
```

### Database
Already configured in `docker-compose.yml` using `pg_isready`.

---

## 7. Policy File Management

Policy files are mounted into the gateway container as a read-only volume:
```yaml
volumes:
  - ./policies:/app/policies:ro
```

To add or modify a tenant policy:
1. Create or edit `policies/<tenant_id>.yaml`.
2. Restart the gateway container: `docker compose restart gateway`.
   - This is required because policies are cached in-process via `lru_cache`.

For zero-downtime policy updates (post-MVP): implement a `SIGHUP` handler that clears the `lru_cache`, or implement the `PolicyStore` protocol with a file watcher.

---

## 8. Database Migrations

**First run:** `001_initial.sql` runs automatically via `docker-entrypoint-initdb.d`. This only runs if the data volume is empty (first container start).

**Schema changes:** Add new migration files (`002_...sql`, `003_...sql`) to `gateway/db/migrations/`. Mount the entire directory:
```yaml
volumes:
  - ./gateway/db/migrations:/docker-entrypoint-initdb.d:ro
```

**Note:** `docker-entrypoint-initdb.d` only runs on a fresh volume. For an existing Postgres instance, run migrations manually:
```bash
docker compose exec db psql -U router -d model_router -f /docker-entrypoint-initdb.d/002_example.sql
```

For production: adopt Alembic for managed migration tracking.

---

## 9. Scaling the Gateway

The gateway is stateless and can be scaled horizontally. With Docker Compose:
```bash
docker compose up --scale gateway=3
```

Add a load balancer (nginx, Traefik) in front. All gateway replicas must have access to the same `policies/` directory (shared volume or synced config).

Connection pool sizing: each gateway instance maintains up to 5 DB connections (SQLAlchemy default). For N replicas: ensure Postgres `max_connections` ≥ `N * 5 + 10`.

---

## 10. Production Hardening Checklist

Before exposing to production traffic:

- [ ] Remove `5432` from published ports in `docker-compose.yml` (DB should not be internet-accessible)
- [ ] Set a strong `POSTGRES_PASSWORD` in `.env`
- [ ] Put the gateway behind a reverse proxy that enforces TLS and (optionally) auth
- [ ] Set `GATEWAY_API_KEY` once the auth middleware is implemented
- [ ] Configure log shipping (gateway logs to stdout — use Docker log driver)
- [ ] Configure Postgres backups (volume snapshot or `pg_dump` cron)
- [ ] Set TimescaleDB data retention policy (see DATA_MODEL.md §7)
- [ ] Pin Docker image versions (replace `latest` tags)
- [ ] Run `docker scout` or similar to check for image vulnerabilities
- [ ] Remove Postgres port from host in `docker-compose.yml` for production

---

## 11. Logs and Observability

**Gateway logs:** Structured JSON via Python `logging`. Written to stdout. Fields: timestamp, level, request_id (future), tenant_id, feature_tag, tier, model, latency_ms.

**Dashboard logs:** Next.js writes to stdout.

**Postgres logs:** TimescaleDB default logging.

**Metrics (post-MVP):** Export Prometheus metrics via `prometheus-fastapi-instrumentator`. Key metrics: `http_request_duration_seconds`, `routing_decisions_total` (by tier), `telemetry_write_errors_total`.
