# API Specification — Model Router

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

See `specs/openapi.yaml` for the machine-readable schema. This document covers conventions, headers, versioning, and error handling that the OpenAPI file alone does not express.

---

## 1. Base URLs

| Environment | URL |
|-------------|-----|
| Local (Docker Compose) | `http://localhost:8000` |
| Self-hosted | Operator-configured; typically behind a reverse proxy |

---

## 2. Versioning

- Chat completions live under `/v1/` to match the OpenAI path.
- Internal endpoints (`/internal/`) are unversioned in MVP and may change without notice.
- When breaking changes are needed, a `/v2/` prefix will be introduced and `/v1/` maintained for one release cycle.

---

## 3. Authentication

**MVP:** No authentication enforced by the gateway itself.

Deploy behind a reverse proxy (nginx, Caddy, AWS ALB) that enforces mTLS or adds an `Authorization` header check. See `specs/SECURITY.md` for the recommended approach.

The `GATEWAY_API_KEY` environment variable is reserved for a future bearer-token middleware layer.

---

## 4. Request Headers

All headers are optional. If omitted, the gateway applies defaults.

| Header | Type | Description |
|--------|------|-------------|
| `X-Feature-Tag` | `string` | Labels this request with a product feature (e.g. `autocomplete`, `summarization`). Used for cost attribution. Max 64 chars, alphanumeric + hyphens. |
| `X-Tenant-Id` | `string` | Selects the tenant routing policy. Must match a filename in `policies/<tenant_id>.yaml`. If missing or file not found, `default_policy.yaml` is used. Max 64 chars, alphanumeric + hyphens. |
| `Content-Type` | `application/json` | Required. |
| `Authorization` | `Bearer <key>` | Passed through to the downstream provider. Not validated by the router in MVP. |

---

## 5. Endpoints

### 5.1 `POST /v1/chat/completions`

Creates a chat completion. OpenAI-compatible.

**Request body:** See `specs/openapi.yaml` → `ChatCompletionRequest`.

Key fields:
- `model` (string, required by schema but ignored by router — we select the model)
- `messages` (array of Message objects, required)
- `stream` (boolean, default `false`)
- `temperature`, `max_tokens` — passed through to the provider

**Response:** `ChatCompletionResponse` (see openapi.yaml).

**Status codes:**

| Code | Meaning |
|------|---------|
| 200 | Success |
| 422 | Request body failed Pydantic validation |
| 502 | Downstream provider returned an error |
| 500 | Internal routing or classification error |

**Streaming:** When `stream: true`, returns `Content-Type: text/event-stream` with OpenAI-format SSE chunks ending with `data: [DONE]`.

---

### 5.2 `GET /internal/stats`

Returns aggregated cost and routing data for the dashboard.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `window` | `"1h" \| "24h" \| "7d" \| "30d"` | `"24h"` | Time window for aggregation |
| `tenant` | string | (none) | Filter to a specific tenant ID |

**Response body:**

```json
{
  "window": "24h",
  "totalCostUsd": 1.2345,
  "baselineCostUsd": 3.4567,
  "savingsUsd": 2.2222,
  "savingsPct": 64.2,
  "byFeature": [
    {
      "featureTag": "autocomplete",
      "costUsd": 0.8,
      "baselineCostUsd": 2.1,
      "requestCount": 1234
    }
  ],
  "byModel": [
    {
      "model": "claude-haiku-4-5-20251001",
      "costUsd": 0.6,
      "requestCount": 900
    }
  ]
}
```

**Status codes:** `200` on success, `500` on DB error.

---

## 6. Error Envelope

All non-2xx responses return a JSON body:

```json
{
  "detail": "human-readable error message"
}
```

This matches FastAPI's default error format. Provider error messages are surfaced in `detail` for 502 responses.

---

## 7. The `model` Field Behavior

Clients typically send `"model": "gpt-4o"` or similar. The router:

1. Accepts any string in `model` (Pydantic allows it).
2. **Ignores it** — the routing engine selects the actual model based on classification + policy.
3. The `model` field in the **response** will contain the model that was actually used (e.g. `claude-haiku-4-5-20251001`), not the requested model.

Clients that hard-check the response `model` field should be aware of this.

---

## 8. Streaming Chunk Format

Streaming chunks follow the OpenAI SSE format:

```
data: {"choices":[{"delta":{"content":"Hello"},"index":0}]}

data: {"choices":[{"delta":{"content":" world"},"index":0}]}

data: [DONE]
```

Anthropic's native streaming format is translated to this format by the `AnthropicProvider` adapter. OpenAI's native format is forwarded as-is.

---

## 9. Header Passthrough Policy

Any headers not listed above are **not** forwarded to the downstream provider. The gateway constructs a fresh outbound request using the provider SDK. This prevents header injection attacks.

The exception: provider-specific headers that the SDK adds automatically (e.g. `anthropic-version`) are managed by the SDK, not by the gateway.
