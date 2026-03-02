# ADR-004: Use TimescaleDB for Cost Attribution Storage

**Status:** Accepted
**Date:** 2026-03-01
**Deciders:** Initial team

---

## Context

The telemetry store needs to handle:
- High-volume append-only writes (one row per LLM request)
- Time-range queries by tenant, feature tag, and model
- Pre-aggregated views for dashboard performance (hourly/daily rollups)
- Long-term retention (months of data) without query degradation

Candidate options:

| Option | Pros | Cons |
|--------|------|------|
| Plain Postgres | Already in stack, simple | No automatic partitioning; range queries degrade over millions of rows |
| TimescaleDB | Postgres extension — same tooling, driver, ORM; automatic partitioning + continuous aggregates | Slightly larger image |
| ClickHouse | Exceptional OLAP performance | Separate cluster, different SQL dialect, no SQLAlchemy async driver |
| SQLite | Zero ops | No concurrent writes; not production-grade |

---

## Decision

**Use TimescaleDB (Postgres extension) on the same Postgres instance already required for the stack.**

Specifically:
- `usage_events` is a hypertable partitioned by `ts` with 1-hour chunks.
- A continuous aggregate `hourly_cost_by_feature` pre-aggregates cost, token counts, and latency at 1-hour granularity.
- The continuous aggregate is refreshed on a 1-hour schedule with a 7-day backfill window.
- Dashboard queries hit the continuous aggregate, not the raw hypertable, for p99 < 100 ms at any data volume.

---

## Consequences

**Positive:**
- Single Postgres connection string, single container, single backup strategy.
- All existing SQLAlchemy, asyncpg, and Postgres tooling works unchanged.
- Continuous aggregates make dashboard queries fast without a separate OLAP store.
- Automatic chunk pruning makes retention management straightforward.
- TimescaleDB is open-source (Apache 2.0 for the community edition) with no licensing cost.

**Negative:**
- TimescaleDB adds ~200 MB to the Docker image over plain Postgres.
- `create_hypertable` and continuous aggregate DDL must be run after table creation — slightly more complex migration.
- If we outgrow TimescaleDB (unlikely at MVP scale), migrating to ClickHouse requires an ETL pipeline.

**Scale envelope:**
- TimescaleDB handles 100k–1M rows/day comfortably on modest hardware (4 vCPU, 8 GB RAM).
- At 10M+ rows/day, evaluate ClickHouse or Timescale's cloud offering.
