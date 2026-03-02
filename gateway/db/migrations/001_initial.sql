-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Core usage events table
CREATE TABLE IF NOT EXISTS usage_events (
    ts               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tenant_id        TEXT,
    feature_tag      TEXT,
    complexity       TEXT        NOT NULL,  -- simple | moderate | complex | sensitive
    tier             TEXT        NOT NULL,
    provider         TEXT        NOT NULL,
    model            TEXT        NOT NULL,
    tokens_in        INTEGER     NOT NULL DEFAULT 0,
    tokens_out       INTEGER     NOT NULL DEFAULT 0,
    latency_ms       INTEGER     NOT NULL DEFAULT 0,
    cost_usd         NUMERIC(12, 8) NOT NULL DEFAULT 0,
    baseline_cost_usd NUMERIC(12, 8) NOT NULL DEFAULT 0
);

-- Convert to hypertable for efficient time-series queries
SELECT create_hypertable('usage_events', 'ts', if_not_exists => TRUE);

-- Indexes for common dashboard queries
CREATE INDEX IF NOT EXISTS idx_usage_tenant_ts    ON usage_events (tenant_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_feature_ts   ON usage_events (feature_tag, ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_model_ts     ON usage_events (model, ts DESC);

-- Continuous aggregate: hourly cost per feature
CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_cost_by_feature
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts) AS bucket,
    tenant_id,
    feature_tag,
    model,
    SUM(tokens_in)            AS tokens_in,
    SUM(tokens_out)           AS tokens_out,
    SUM(cost_usd)             AS cost_usd,
    SUM(baseline_cost_usd)    AS baseline_cost_usd,
    COUNT(*)                  AS request_count,
    AVG(latency_ms)           AS avg_latency_ms
FROM usage_events
GROUP BY bucket, tenant_id, feature_tag, model
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'hourly_cost_by_feature',
    start_offset => INTERVAL '7 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
