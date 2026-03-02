# Model Router

OpenAI-compatible API gateway that **classifies** queries, **routes** them to the right model tier, and **attributes costs** per feature/tenant.

```
POST /v1/chat/completions   ← drop-in OpenAI replacement
GET  /internal/stats        ← cost aggregates consumed by the dashboard
```

## Repo layout

```
model-router/
├── gateway/                # FastAPI application
│   ├── main.py             # app entry point
│   ├── schemas.py          # OpenAI-compatible Pydantic models
│   ├── api/v1/
│   │   ├── chat.py         # POST /v1/chat/completions
│   │   └── stats.py        # GET /internal/stats
│   ├── classifier/
│   │   └── heuristic.py    # token count + regex classifier → simple/moderate/complex/sensitive
│   ├── router/
│   │   ├── policy.py       # loads YAML policy files per tenant
│   │   └── engine.py       # maps classification → provider + model
│   ├── providers/
│   │   ├── base.py         # abstract interface
│   │   ├── anthropic.py    # Anthropic adapter
│   │   ├── openai.py       # OpenAI adapter
│   │   └── registry.py     # provider factory
│   ├── telemetry/
│   │   └── collector.py    # async fire-and-forget usage logging
│   ├── db/
│   │   ├── session.py      # SQLAlchemy async session
│   │   └── migrations/
│   │       └── 001_initial.sql   # TimescaleDB schema + continuous aggregate
│   ├── pyproject.toml
│   └── Dockerfile
├── dashboard/              # Next.js cost dashboard
│   ├── app/
│   │   ├── page.tsx        # main dashboard (SavingsWidget + charts)
│   │   └── api/stats/      # BFF route → proxies to gateway
│   ├── components/
│   │   ├── SavingsWidget.tsx
│   │   ├── CostByFeatureChart.tsx
│   │   └── ModelBreakdownChart.tsx
│   └── Dockerfile
├── policies/
│   └── default_policy.yaml # tier → provider/model mapping; add <tenant_id>.yaml to override
├── docker-compose.yml
└── .env.example
```

## Quick start

```bash
cp .env.example .env          # fill in ANTHROPIC_API_KEY / OPENAI_API_KEY
docker compose up --build
```

- Gateway: http://localhost:8000
- Dashboard: http://localhost:3000
- Postgres: localhost:5432

## Using the gateway

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Feature-Tag: autocomplete" \
  -H "X-Tenant-Id: acme" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "What is 2 + 2?"}]
  }'
```

The router will classify this as `simple`, route it to Haiku, and log the cost vs. the GPT-4o baseline.

## Routing policy

Edit `policies/default_policy.yaml` to change which model is used per tier, or create `policies/<tenant_id>.yaml` for per-tenant overrides:

```yaml
tiers:
  simple:    { provider: anthropic, model: claude-haiku-4-5-20251001 }
  moderate:  { provider: anthropic, model: claude-sonnet-4-6 }
  complex:   { provider: anthropic, model: claude-sonnet-4-6 }
  sensitive: { provider: anthropic, model: claude-opus-4-6 }
overrides:
  - match: { feature_tag: legal-review }
    tier: sensitive
```

## Adding a provider

1. Create `gateway/providers/<name>.py` implementing `BaseProvider`
2. Register it in `gateway/providers/registry.py`
3. Reference it in a policy YAML
