# Architecture Specification — Model Router

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. System Overview

Model Router is a transparent proxy that sits between any LLM client and one or more LLM providers. It adds three capabilities without requiring client changes:

1. **Classification** — scores each request by complexity/sensitivity
2. **Routing** — maps that score to the cheapest appropriate model
3. **Attribution** — records cost, latency, and routing decisions per feature/tenant

```
Client App  (OpenAI SDK, curl, etc.)
    │
    │  POST /v1/chat/completions
    │  Headers: X-Feature-Tag, X-Tenant-Id
    ▼
┌─────────────────────────────────────────┐
│              API Gateway Layer           │
│         FastAPI  ·  port 8000           │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│           Query Classifier               │
│  heuristic: token count + regex          │
│  → simple | moderate | complex | sensitive│
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│           Routing Engine                 │
│  loads YAML policy for tenant            │
│  applies overrides (feature_tag)         │
│  → RoutingDecision(provider, model, tier)│
└──────┬───────────────────┬──────────────┘
       │                   │
       ▼                   ▼
┌────────────┐       ┌────────────┐
│  Cheap tier│       │Premium tier│    ← provider adapters
│  Haiku     │       │  Sonnet    │      (Anthropic, OpenAI)
│  GPT-4o-mini       │  GPT-4o    │
└────────────┘       └────────────┘
       │                   │
       └─────────┬─────────┘
                 │  response
                 ▼
┌─────────────────────────────────────────┐
│         Telemetry Collector              │
│  asyncio.create_task (non-blocking)      │
│  writes: model, tokens, latency, cost   │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│       TimescaleDB (Postgres)             │
│  usage_events hypertable                 │
│  hourly_cost_by_feature agg              │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│         Dashboard  (Next.js)             │
│  SavingsWidget · CostByFeature chart    │
│  ModelBreakdown chart                   │
└─────────────────────────────────────────┘
```

---

## 2. Component Responsibilities

### 2.1 API Gateway Layer (`gateway/api/v1/chat.py`)

- Accepts `POST /v1/chat/completions` with OpenAI-compatible request body.
- Extracts `X-Feature-Tag` and `X-Tenant-Id` headers.
- Orchestrates: classify → route → call provider → emit telemetry → return response.
- Handles both streaming (`text/event-stream`) and non-streaming responses.
- Maps provider errors to HTTP 502 with a safe error message.
- Must not mutate the request body before forwarding to the provider.

### 2.2 Query Classifier (`gateway/classifier/`)

- **Input:** `list[Message]` from the request body.
- **Output:** `Classification(complexity, token_count, signals)`.
- Currently implemented as a pure heuristic (no network call).
- Must be replaceable with a model-based classifier via the same interface.
- Must complete in < 5 ms p99.

### 2.3 Routing Engine (`gateway/router/`)

- **Input:** `Classification`, optional `tenant_id`, optional `feature_tag`.
- **Output:** `RoutingDecision(provider, model, tier)`.
- Loads policy from `policies/<tenant_id>.yaml` (falls back to `default_policy.yaml`).
- Policy files are cached in-process via `lru_cache`; no hot-reload in MVP (requires process restart).
- Override resolution order: feature_tag overrides → tier from classification.

### 2.4 Provider Adapters (`gateway/providers/`)

- One class per provider, implementing `BaseProvider`.
- Responsible for: request translation, API call, response normalization to `ChatCompletionResponse`.
- Encapsulates all provider-specific quirks (e.g. Anthropic's separate `system` field).
- Streaming adapters emit OpenAI-compatible SSE chunks regardless of provider.

### 2.5 Telemetry Collector (`gateway/telemetry/`)

- Called via `asyncio.create_task` after the response is returned — never in the critical path.
- Computes `cost_usd` and `baseline_cost_usd` in-process from a static pricing table.
- Writes a single row to `usage_events` per request.
- On DB error: logs the exception and drops the event. Does not retry in MVP.

### 2.6 Cost Attribution Store (`gateway/db/`)

- TimescaleDB hypertable partitioned by `ts` (hourly chunks).
- Continuous aggregate `hourly_cost_by_feature` pre-aggregates for dashboard queries.
- Migration applied via `docker-entrypoint-initdb.d` on first container start.

### 2.7 Stats API (`gateway/api/v1/stats.py`)

- `GET /internal/stats?window=24h&tenant=<id>`.
- Returns: total cost, baseline cost, savings, cost by feature, cost by model.
- Consumed by the Next.js dashboard BFF route.
- Not authenticated in MVP (see SECURITY.md).

### 2.8 Dashboard (`dashboard/`)

- Next.js 15 App Router, client components with SWR for polling.
- Fetches stats via `/api/stats` BFF route which proxies to the gateway.
- Three views: SavingsWidget (top KPIs), CostByFeatureChart, ModelBreakdownChart.
- Refreshes: SavingsWidget every 60s, charts every 5m.

---

## 3. Data Flow

### Non-streaming request (happy path)

```
1. Client → POST /v1/chat/completions
2. Gateway extracts headers (tenant_id, feature_tag)
3. Classifier.classify(messages) → Classification
4. Router.route(classification, tenant_id) → RoutingDecision
5. Provider.complete(request, model) → ChatCompletionResponse
6. asyncio.create_task(emit(...))   ← non-blocking
7. Gateway → 200 ChatCompletionResponse to client
8. [background] Telemetry._write() → INSERT usage_events
```

### Streaming request

Steps 1-4 identical. Step 5 returns an `AsyncIterator[bytes]`; the gateway wraps it in a `StreamingResponse`. Telemetry is emitted after the stream is exhausted.

---

## 4. Scalability Model

- **Gateway** is stateless; scale horizontally behind a load balancer.
- **Policy files** are read-only volume mounts; consistent across replicas.
- **DB connection pool** is per-process (SQLAlchemy async pool, default 5 connections).
- **TimescaleDB** handles write throughput via hypertable chunking; read throughput via continuous aggregates.
- Target: 500 req/s per gateway replica at p99 < 200 ms total latency.

---

## 5. Failure Modes

| Failure | Behavior | Recovery |
|---------|----------|----------|
| DB unavailable | Telemetry events dropped; requests succeed | Events permanently lost in MVP |
| Provider API error | Gateway returns HTTP 502 | Client retries independently |
| Policy file missing | Falls back to `default_policy.yaml` | Log warning; no crash |
| Classifier raises exception | Falls back to `complex` tier | Log error; request proceeds |
| Unknown provider in policy | Gateway raises 500 at routing time | Fix policy file |

---

## 6. External Dependencies

| Dependency | Purpose | Owned by |
|------------|---------|----------|
| Anthropic API | Premium and sensitive tier inference | Anthropic |
| OpenAI API | Alternative provider tiers | OpenAI |
| TimescaleDB | Cost time-series storage | Self-hosted |
| Next.js | Dashboard UI | Self-hosted |

---

## 7. Key Design Decisions

See `specs/adr/` for full rationale. Summary:

- **OpenAI-compatible schema** → zero migration cost for clients (ADR-001)
- **Heuristic classifier first** → sub-ms latency, no LLM call for routing (ADR-002)
- **YAML policy, not DB** → readable, version-controlled, no DB dependency for routing (ADR-003)
- **TimescaleDB** → continuous aggregates make dashboard queries fast without a separate OLAP store (ADR-004)
