# Telemetry Specification

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Purpose

Telemetry is the foundation of the cost attribution story. Every request produces one event that records what happened, what it cost, and what it would have cost without the router. The `savings_usd` number derived from telemetry is the primary demo closer.

---

## 2. Event Schema

One row per request, written to the `usage_events` TimescaleDB hypertable.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `ts` | `TIMESTAMPTZ` | No | Request completion timestamp (UTC). Partition key. |
| `tenant_id` | `TEXT` | Yes | From `X-Tenant-Id` header. `NULL` if not provided. |
| `feature_tag` | `TEXT` | Yes | From `X-Feature-Tag` header. `NULL` if not provided. |
| `complexity` | `TEXT` | No | Classifier output: `simple \| moderate \| complex \| sensitive` |
| `tier` | `TEXT` | No | Routing tier actually used (may differ from complexity due to overrides) |
| `provider` | `TEXT` | No | Provider name: `anthropic \| openai` |
| `model` | `TEXT` | No | Full model ID: e.g. `claude-haiku-4-5-20251001` |
| `tokens_in` | `INTEGER` | No | Input token count from provider response (or heuristic estimate if unavailable) |
| `tokens_out` | `INTEGER` | No | Output token count from provider response. `0` for streaming in MVP. |
| `latency_ms` | `INTEGER` | No | End-to-end latency from request receipt to response sent, in milliseconds. |
| `cost_usd` | `NUMERIC(12,8)` | No | Computed cost for the actual model used. |
| `baseline_cost_usd` | `NUMERIC(12,8)` | No | Computed cost if the request had been sent to the baseline model. |

---

## 3. Cost Calculation

Cost is computed in-process at emit time using a static pricing table in `telemetry/collector.py`.

### Formula
```
cost_usd = (tokens_in * rate_in + tokens_out * rate_out) / 1000
```

Where `rate_in` and `rate_out` are USD per 1,000 tokens for the actual model used.

### Baseline cost
```
baseline_cost_usd = (tokens_in * baseline_rate_in + tokens_out * baseline_rate_out) / 1000
```

The baseline model is `gpt-4o` (hardcoded in MVP). This represents "what you'd pay if you sent everything to GPT-4o."

### Savings
```
savings_usd = baseline_cost_usd - cost_usd
savings_pct = (savings_usd / baseline_cost_usd) * 100  # if baseline > 0
```

### Pricing table (current)

| Model | Input ($/1k tokens) | Output ($/1k tokens) |
|-------|--------------------|--------------------|
| `claude-haiku-4-5-20251001` | $0.00025 | $0.00125 |
| `claude-sonnet-4-6` | $0.003 | $0.015 |
| `claude-opus-4-6` | $0.015 | $0.075 |
| `gpt-4o-mini` | $0.00015 | $0.0006 |
| `gpt-4o` (baseline) | $0.005 | $0.015 |

**Maintenance:** Update `_COST_TABLE` in `telemetry/collector.py` when provider pricing changes. A future version will move this to a DB table or config file.

Models not in the table default to `(0.0, 0.0)` — they will be recorded with zero cost and zero baseline. This is a silent failure; log a warning when a model is not found in the table.

---

## 4. Emit Behavior

### Fire-and-forget
Telemetry is emitted via `asyncio.create_task()` after the response is returned to the client. It is **never** in the critical path.

```python
# In chat.py, after provider.complete() returns:
await emit(...)   # schedules task, returns immediately
return response   # client gets response without waiting for DB write
```

### Failure handling
- If the DB write fails: log the exception at `ERROR` level and discard the event.
- No retry in MVP. Events are permanently lost on DB failure.
- A DB outage should never cause a 500 to the client.

### Streaming latency
For streaming requests, `latency_ms` is measured from request receipt to the moment the stream is fully consumed (i.e. the `[DONE]` chunk is sent). This measures total time-to-completion, not time-to-first-token.

---

## 5. Token Count Sources (priority order)

1. **Provider response usage fields** (most accurate) — use when available.
2. **Classifier token estimate** (`classification.token_count`) — used for `tokens_in` when provider doesn't return input tokens (e.g. streaming in MVP).
3. **Zero** — `tokens_out` defaults to `0` for streaming in MVP.

---

## 6. Continuous Aggregate: `hourly_cost_by_feature`

Pre-aggregated view for dashboard queries. Refreshed every hour.

| Column | Description |
|--------|-------------|
| `bucket` | 1-hour time bucket |
| `tenant_id` | Tenant |
| `feature_tag` | Feature |
| `model` | Model used |
| `tokens_in` | Sum of input tokens |
| `tokens_out` | Sum of output tokens |
| `cost_usd` | Sum of cost |
| `baseline_cost_usd` | Sum of baseline cost |
| `request_count` | Number of requests |
| `avg_latency_ms` | Average latency |

Dashboard queries should target this view (or its `time_bucket` rollups) rather than the raw `usage_events` table to stay within the p99 < 100 ms query budget.

---

## 7. Data Retention

MVP default: no automatic retention policy (keep all data indefinitely).

Recommended production policy:
- Raw `usage_events`: 90 days
- `hourly_cost_by_feature` aggregate: 1 year

Implement via TimescaleDB data retention policy:
```sql
SELECT add_retention_policy('usage_events', INTERVAL '90 days');
```

---

## 8. Privacy Considerations

The `usage_events` table stores:
- Tenant ID and feature tags (low sensitivity — these are tags, not content)
- Token counts (no content)
- Cost and model metadata (no content)

**The telemetry system never stores message content.** This is a deliberate design decision — cost attribution does not require content storage.

If a future classifier needs to log signals for accuracy analysis, log only the `signals` list (e.g. `["complex_keyword", "long_context"]`), not the raw text.
