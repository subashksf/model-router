# ADR-001: Mirror the OpenAI Chat Completions API Schema

**Status:** Accepted
**Date:** 2026-03-01
**Deciders:** Initial team

---

## Context

The gateway needs an API contract. We have three options:

1. Design a custom schema (e.g. add `complexity_hint`, `tier` fields explicitly)
2. Mirror the OpenAI `POST /v1/chat/completions` schema exactly
3. Mirror OpenAI but extend it with optional router-specific fields

The product's primary value proposition is that customers can swap a single environment variable (`OPENAI_BASE_URL`) and immediately benefit from routing. Any schema divergence breaks this promise.

The OpenAI Chat Completions schema is the de facto industry standard. Every major SDK (Python, Node, Go, Rust) has a client for it. Anthropic's own SDK supports OpenAI-compatible mode. Nearly every LLM product being built today is written against this schema.

---

## Decision

**Mirror the OpenAI `POST /v1/chat/completions` schema exactly for request and response bodies.**

- The `model` field is accepted but ignored by the router (we pick the model).
- Any extra fields in the request body are passed through to the downstream provider unchanged (Pydantic `extra="allow"`).
- Router-specific concerns (tenant, feature tag) are expressed as **HTTP headers** (`X-Tenant-Id`, `X-Feature-Tag`), not as body fields.
- Response bodies conform to the OpenAI schema so existing response-parsing code works unchanged.

---

## Consequences

**Positive:**
- Zero client-side migration cost — change one URL, get value immediately.
- All OpenAI SDK clients work out of the box without modification.
- The gateway can be positioned as a drop-in replacement in documentation and sales.
- Streaming (SSE) also follows OpenAI format, so streaming clients work too.

**Negative:**
- We cannot add router-specific fields to the request body without breaking schema purity.
- The `model` field in the request becomes a no-op, which may confuse clients that pass specific model names. We should document this clearly.
- We are implicitly dependent on OpenAI's schema versioning. If they make breaking changes, we follow or document the divergence.

**Neutral:**
- Headers for router config (`X-Feature-Tag`, `X-Tenant-Id`) are non-standard but invisible to OpenAI SDK clients that don't set them — safe to ignore if not present.
