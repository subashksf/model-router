# Product Requirements Document — Model Router

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Problem Statement

Teams building LLM-powered features default to the most capable (and expensive) model for every request because routing logic is hard, and the cost of getting it wrong (bad output) feels higher than the cost of over-spending. The result is:

- 60–80% of requests that could be handled by a cheap model (Haiku, GPT-4o-mini) are sent to premium models (Sonnet, GPT-4o).
- Engineering teams have no visibility into which features drive LLM spend.
- When a CTO asks "how much are we spending on AI?", the answer is a single opaque number.

**Model Router** sits between the client and any LLM provider, silently routing each request to the right model tier. The client changes one environment variable (`OPENAI_BASE_URL`). Everything else — feature tags, cost breakdowns, routing policy — is handled server-side.

---

## 2. Target Users

| Persona | Problem | What they need |
|---------|---------|----------------|
| **Platform / infra engineer** | Wants to cut LLM costs without touching product code | A drop-in proxy with a single config file |
| **CTO / VP Eng** | Needs to justify LLM spend to the board | A dashboard showing cost per feature and savings vs. baseline |
| **ML / AI engineer** | Wants to tune routing accuracy for their domain | An extensible classifier and policy overrides |
| **Enterprise buyer** | Has compliance requirements (PII, VPC) | Hard routing rules and self-hosted deployment |

---

## 3. Goals

### Primary Goals (MVP)
- **G1** — Drop-in OpenAI-compatible endpoint: no client-side code changes required beyond swapping the base URL.
- **G2** — Route at least 3 complexity tiers (simple / complex / sensitive) using heuristics with no external model call.
- **G3** — Log cost per request with provider, model, tenant, and feature tag attribution.
- **G4** — Show a "savings vs. baseline" number on a dashboard that updates within 5 minutes.

### Secondary Goals (post-MVP)
- **G5** — Per-tenant YAML routing policy with hot-reload.
- **G6** — Replace heuristic classifier with a fine-tuned local model (Phi-3-mini or Llama 3.2 3B) to eliminate classifier inference cost.
- **G7** — Managed cloud offering (multi-tenant SaaS) alongside the self-hosted path.
- **G8** — Streaming support with per-chunk telemetry.

### Non-Goals
- We do not cache or deduplicate LLM responses.
- We do not modify prompts or inject system instructions.
- We are not a rate limiter or API gateway in the traditional sense (no auth enforcement on the proxy itself in MVP).
- We do not support non-chat APIs (embeddings, images, audio) in MVP.

---

## 4. User Stories

### Core path
- **US-01** — As a platform engineer, I can point my existing OpenAI SDK calls at the router and receive valid responses, so that I don't need to change product code.
- **US-02** — As a platform engineer, I can add an `X-Feature-Tag: autocomplete` header to my requests so that costs are attributed to the right product feature.
- **US-03** — As a platform engineer, I can add an `X-Tenant-Id` header so that enterprise customers get their own routing policy.

### Routing
- **US-04** — As an AI engineer, I can edit a YAML file to change which model handles "complex" queries without redeploying the gateway.
- **US-05** — As an AI engineer, I can pin a specific feature tag (e.g. `legal-review`) to the `sensitive` tier regardless of the classifier output, so that compliance requirements are always met.

### Observability
- **US-06** — As a CTO, I can open a dashboard and see the total LLM spend for the last 24 hours broken down by feature, so that I can identify cost drivers.
- **US-07** — As a CTO, I can see "actual spend vs. what we'd have paid sending everything to GPT-4o", so that the ROI of the router is immediately legible.
- **US-08** — As a platform engineer, I can query cost data via API so that I can embed it in internal tooling.

### Enterprise
- **US-09** — As an enterprise admin, I can configure a policy that routes any request matching a PII keyword to an on-premises model, so that sensitive data never leaves our VPC.

---

## 5. Success Metrics

| Metric | MVP target | Measurement |
|--------|-----------|-------------|
| Routing accuracy | ≥ 70% (requests routed to the "correct" tier by human eval) | Manual eval on 100-request sample |
| Added latency (p50) | < 20 ms classifier + routing overhead | `latency_ms` in `usage_events` vs. direct call |
| Added latency (p99) | < 100 ms | Same |
| Dashboard freshness | Data visible within 5 minutes of request | Compare `ts` to dashboard render time |
| Drop-in compatibility | 100% of OpenAI SDK methods used by clients work unchanged | Integration test suite |
| Cost savings (demo) | ≥ 40% reduction vs. all-GPT-4o baseline on a realistic workload | Telemetry comparison |

---

## 6. Constraints & Assumptions

- **Latency budget:** The classifier and router combined must add < 50 ms p99 to any request. A model-based classifier must run locally (not via API) to meet this budget.
- **Provider agnosticism:** The core router must not have Anthropic-specific or OpenAI-specific logic in the gateway layer — only in provider adapters.
- **Stateless gateway:** The FastAPI process is stateless; policy files are read-only mounts. This allows horizontal scaling without coordination.
- **Async telemetry:** Telemetry must never block the response path. A DB outage must degrade gracefully (lost events, not failed requests).
- **Self-hosted first:** The MVP is a Docker Compose stack. Cloud deployment is a later concern.

---

## 7. Out of Scope (MVP)

- Fine-grained RBAC on the dashboard
- Webhook / alerting on cost thresholds
- A/B testing between model tiers
- Support for function/tool call routing
- Multi-region deployment
- SDK (clients must use raw HTTP or existing OpenAI SDKs)
