# Security Specification

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Threat Model

The gateway sits between client applications and LLM providers. The primary threats are:

| Threat | Impact | Mitigation |
|--------|--------|------------|
| Unauthorized use of the gateway | Cost incurred on the operator's provider accounts | Bearer token auth (post-MVP); network-level controls for MVP |
| Tenant A reading Tenant B's telemetry | Data privacy violation | Tenant filter on all stats queries; no shared cache |
| PII in messages routed to an external provider | Compliance violation | `sensitive` tier routing; documented in policy |
| Prompt injection via headers or request body | Misrouting or log injection | Headers are extracted to variables, never interpolated; body is parsed via Pydantic |
| Provider API key exposure via logs | Full account compromise | Never log request bodies; scrub API keys from error messages |
| Stats endpoint exposing all tenant data | Privacy/competitive data leak | Network-level access control in MVP; auth middleware post-MVP |

---

## 2. Authentication — MVP

**The gateway has no built-in authentication in MVP.**

Acceptable mitigations for MVP deployments:

1. **Network isolation:** Deploy behind a VPC or private network; the gateway is not internet-accessible.
2. **Reverse proxy auth:** Put nginx/Caddy/ALB in front of the gateway and enforce `Authorization: Bearer <key>` at that layer.
3. **mTLS:** For enterprise self-hosted deployments, use mutual TLS between clients and the gateway.

The `GATEWAY_API_KEY` environment variable is reserved for a future bearer-token middleware. When set, a FastAPI dependency will check `Authorization: Bearer <key>` on all `/v1/` routes.

---

## 3. Authentication — Post-MVP

Implement a FastAPI dependency:

```python
async def require_auth(authorization: str = Header(...)):
    expected = os.environ.get("GATEWAY_API_KEY")
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid API key")
```

Apply to `/v1/chat/completions` and `/internal/stats` routes.

Future: per-tenant API keys stored in DB, enabling key rotation and audit logs.

---

## 4. Stats Endpoint Access Control

`GET /internal/stats` returns cost data for all tenants (or a filtered view). In MVP:

- Not authenticated.
- Should only be accessible from the dashboard container or internal network.
- **Do not expose this endpoint on a public internet-facing port.**

In Docker Compose, the gateway and dashboard are on the same Docker network. The dashboard's Next.js BFF route (`/api/stats`) proxies to the gateway internally. The gateway's port `8000` should not be published to the host in production.

---

## 5. PII and the `sensitive` Tier

The `sensitive` keyword list in `classifier/heuristic.py` is the first line of defense against PII routing to an inappropriate model.

### Guarantees (with default policy)
- Any request matching a sensitive keyword is routed to `claude-opus-4-6` (the most capable, most private Anthropic model).
- No request is routed to a third-party provider unless the policy explicitly configures one for the `sensitive` tier.

### Enterprise extension
Tenants with stricter requirements can:
1. Add domain-specific sensitive keywords to their policy YAML (future: `classifier.sensitive_keywords` field).
2. Point the `sensitive` tier at a self-hosted/on-premises model by adding a `local` provider adapter.
3. Set `overrides` to route entire feature tags to `sensitive` regardless of classifier output.

### What is NOT stored
The telemetry system stores **no message content**. Token counts, model names, and cost figures are stored. This is a hard architectural constraint — do not add `message_content` or similar fields to `usage_events`.

---

## 6. Provider API Key Handling

- API keys are stored in environment variables (`.env` file in development, secret management system in production).
- API keys are passed to provider SDKs via environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) — not hardcoded or passed through request headers.
- The client's `Authorization` header is **not** forwarded to the downstream provider. The gateway uses its own configured API keys.
- API keys must never appear in logs. Ensure exception handlers sanitize error messages from provider SDKs before logging.

---

## 7. Input Validation

- All request bodies are parsed and validated by Pydantic. Malformed JSON returns 422.
- `X-Feature-Tag` and `X-Tenant-Id` headers are validated to `[a-zA-Z0-9-_]{1,64}` before use in SQL queries (via parameterized queries — no string interpolation).
- SQL queries in `stats.py` use SQLAlchemy `text()` with named parameters (`:tenant`, `:since`) — never f-string interpolation into SQL.
- Policy YAML files are loaded from a controlled directory (`POLICIES_DIR`). The `tenant_id` value from the header is used as a filename directly. **Path traversal risk:** sanitize `tenant_id` to strip path separators before constructing the filename. Implementation: `tenant_id.replace("/", "").replace("..", "")`.

---

## 8. Logging Policy

**Log:** Request method, path, response status, latency, routing decision (provider, model, tier), tenant ID, feature tag.

**Do not log:** Message content, provider API keys, full request/response bodies.

**Log levels:**
- `INFO` — each request with routing decision
- `WARNING` — policy file not found (fallback used), model not in cost table
- `ERROR` — classifier exception, DB write failure, provider API error with sanitized message

---

## 9. Dependency Security

- Pin all Python dependencies in `pyproject.toml` with minimum versions.
- Run `pip audit` or `uv audit` in CI to detect known vulnerabilities in dependencies.
- The Docker base image is `python:3.12-slim` — minimal attack surface; no unnecessary system packages.
- Dashboard uses `node:22-alpine` — minimal Node.js image.
