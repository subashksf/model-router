# Data Model Specification

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Overview

The data store has a single core table (`usage_events`) and one pre-aggregated view (`hourly_cost_by_feature`). The schema is intentionally minimal — the router does not store user data, conversation history, or model outputs.

Database: **TimescaleDB** (Postgres 16 + TimescaleDB extension).
Migration file: `gateway/db/migrations/001_initial.sql`.

---

## 2. `usage_events` — Core Table

### Definition

```sql
CREATE TABLE usage_events (
    ts                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    tenant_id         TEXT,
    feature_tag       TEXT,
    complexity        TEXT          NOT NULL,
    tier              TEXT          NOT NULL,
    provider          TEXT          NOT NULL,
    model             TEXT          NOT NULL,
    tokens_in         INTEGER       NOT NULL DEFAULT 0,
    tokens_out        INTEGER       NOT NULL DEFAULT 0,
    latency_ms        INTEGER       NOT NULL DEFAULT 0,
    cost_usd          NUMERIC(12,8) NOT NULL DEFAULT 0,
    baseline_cost_usd NUMERIC(12,8) NOT NULL DEFAULT 0
);

SELECT create_hypertable('usage_events', 'ts', if_not_exists => TRUE);
```

### Field semantics

**`ts`** — Request completion timestamp in UTC. This is the time the gateway returned a response (or the stream ended), not the time the request arrived. Used as the TimescaleDB partition key.

**`tenant_id`** — Opaque string from the `X-Tenant-Id` header. `NULL` means "no tenant identified." Queries that omit a `tenant_id` filter return all tenants.

**`feature_tag`** — Opaque string from the `X-Feature-Tag` header. `NULL` means untagged. Dashboard displays `NULL` as "untagged."

**`complexity`** — Classifier output. Constrained by application logic to `simple | moderate | complex | sensitive`. Not a DB-level constraint to avoid migration friction when adding tiers.

**`tier`** — Routing tier actually used. May differ from `complexity` when a feature-tag override is active. Always matches a key in the tenant's policy file.

**`provider`** — Provider name string. Currently `anthropic` or `openai`.

**`model`** — Full model identifier as passed to the provider API. Used as the key into `_COST_TABLE`.

**`tokens_in`** — Input token count. Source: provider response usage fields (preferred) or classifier estimate.

**`tokens_out`** — Output token count. Source: provider response usage fields. `0` for streaming in MVP.

**`latency_ms`** — Wall-clock milliseconds from request receipt to response complete.

**`cost_usd`** — Computed cost for the actual model used. See TELEMETRY.md §3 for formula.

**`baseline_cost_usd`** — Computed cost if the same request had been sent to `gpt-4o`. The difference is `savings_usd`.

### TimescaleDB configuration

- **Chunk interval:** 1 hour (default for high-write workloads; reduces chunk count per day)
- **Chunk size target:** ~25 MB per chunk
- No compression in MVP (enable with `add_compression_policy` once data exceeds 7 days)

---

## 3. Indexes

```sql
CREATE INDEX idx_usage_tenant_ts   ON usage_events (tenant_id, ts DESC);
CREATE INDEX idx_usage_feature_ts  ON usage_events (feature_tag, ts DESC);
CREATE INDEX idx_usage_model_ts    ON usage_events (model, ts DESC);
```

These cover the three most common dashboard filter patterns. The `ts DESC` ordering matches the `ORDER BY ts DESC` common in dashboard queries.

---

## 4. `hourly_cost_by_feature` — Continuous Aggregate

### Purpose
Pre-aggregates cost and request volume at 1-hour granularity. Dashboard queries targeting time ranges longer than 1 hour should use this view rather than the raw table.

### Definition

```sql
CREATE MATERIALIZED VIEW hourly_cost_by_feature
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts)    AS bucket,
    tenant_id,
    feature_tag,
    model,
    SUM(tokens_in)               AS tokens_in,
    SUM(tokens_out)              AS tokens_out,
    SUM(cost_usd)                AS cost_usd,
    SUM(baseline_cost_usd)       AS baseline_cost_usd,
    COUNT(*)                     AS request_count,
    AVG(latency_ms)              AS avg_latency_ms
FROM usage_events
GROUP BY bucket, tenant_id, feature_tag, model;
```

### Refresh policy
- `schedule_interval`: 1 hour
- `start_offset`: 7 days (backfill up to 7 days of history)
- `end_offset`: 1 hour (data within the last hour may be incomplete; exclude from aggregate)

### Staleness
Aggregate data lags raw events by up to 1 hour + the refresh schedule. The dashboard shows data "within 5 minutes" for the SavingsWidget (which queries the raw table for the last 24h window) and "up to 1 hour delayed" for the 7d/30d charts (which query the aggregate).

---

## 5. Standard Dashboard Queries

### Total cost and savings (last 24h)
```sql
SELECT
    COALESCE(SUM(cost_usd), 0)          AS total_cost_usd,
    COALESCE(SUM(baseline_cost_usd), 0) AS baseline_cost_usd
FROM usage_events
WHERE ts >= NOW() - INTERVAL '24 hours'
  AND tenant_id = $1;   -- omit for all-tenant view
```

### Cost by feature (last 7 days) — uses aggregate
```sql
SELECT
    COALESCE(feature_tag, 'untagged') AS feature_tag,
    SUM(cost_usd)                     AS cost_usd,
    SUM(baseline_cost_usd)            AS baseline_cost_usd,
    SUM(request_count)                AS request_count
FROM hourly_cost_by_feature
WHERE bucket >= NOW() - INTERVAL '7 days'
  AND tenant_id = $1
GROUP BY 1
ORDER BY 2 DESC;
```

### Cost by model (last 7 days) — uses aggregate
```sql
SELECT
    model,
    SUM(cost_usd)      AS cost_usd,
    SUM(request_count) AS request_count
FROM hourly_cost_by_feature
WHERE bucket >= NOW() - INTERVAL '7 days'
  AND tenant_id = $1
GROUP BY 1
ORDER BY 2 DESC;
```

### Routing accuracy (human-labeled sample required)
This query compares classifier output to a `labeled_complexity` column that doesn't exist yet. Placeholder for when shadow scoring is implemented:
```sql
-- Future: compare complexity vs. labeled_complexity on a sampled subset
```

---

## 6. Migration Strategy

**MVP (Docker Compose):** `001_initial.sql` is placed in `/docker-entrypoint-initdb.d/` inside the Postgres container. It runs once on first container start.

**Production (managed Postgres / RDS):** Use Alembic for migration management. Add `alembic` to `pyproject.toml` and generate migrations from the SQLAlchemy models when a DB-managed schema is desired.

**Schema evolution rules:**
- Adding nullable columns is always backwards-compatible.
- Adding new indexes requires a `CREATE INDEX CONCURRENTLY` to avoid locking.
- Changing column types or dropping columns requires a migration + coordinated deploy.
- Continuous aggregate schema changes require dropping and recreating the view.
