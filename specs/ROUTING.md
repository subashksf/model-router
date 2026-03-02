# Routing Engine Specification

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Purpose

The routing engine maps a `Classification` result to a concrete `RoutingDecision` (provider + model + tier label) using a tenant-specific policy.

---

## 2. Interface Contract

```python
def route(
    classification: Classification,
    tenant_id: str | None = None,
    feature_tag: str | None = None,
) -> RoutingDecision
```

```python
@dataclass
class RoutingDecision:
    provider: str   # e.g. "anthropic", "openai"
    model: str      # e.g. "claude-haiku-4-5-20251001"
    tier: str       # e.g. "simple" — the tier actually used (after overrides)
```

---

## 3. Policy File Format

Each tenant has one YAML policy file at `policies/<tenant_id>.yaml`. If no tenant-specific file exists, `policies/default_policy.yaml` is used.

### Schema

```yaml
tiers:                         # required
  simple:                      # tier name must match Classification.complexity values
    provider: anthropic        # required: provider name (must exist in provider registry)
    model: claude-haiku-4-5-20251001  # required: model string passed to provider API
  moderate:
    provider: anthropic
    model: claude-sonnet-4-6
  complex:
    provider: anthropic
    model: claude-sonnet-4-6
  sensitive:
    provider: anthropic
    model: claude-opus-4-6

overrides:                     # optional, defaults to []
  - match:
      feature_tag: autocomplete
    tier: simple
  - match:
      feature_tag: legal-review
    tier: sensitive
```

### Validation rules
- All four tiers (`simple`, `moderate`, `complex`, `sensitive`) should be defined. A missing tier falls back to `complex`.
- `provider` must be a key in `gateway/providers/registry.py`.
- `model` must be a valid model string for that provider (not validated at load time; validated at runtime when the provider rejects it).
- Each override must have both `match` and `tier` fields.
- `match.feature_tag` is the only supported match key in MVP.

---

## 4. Resolution Algorithm

```
1. Load policy for tenant_id (or default_policy.yaml)
2. Evaluate overrides in order (first match wins):
   - If override.match.feature_tag == request.feature_tag → use override.tier
3. If no override matched → use classification.complexity as tier
4. Look up policy.tiers[tier] → TierConfig(provider, model)
5. If tier not found in policy.tiers → fall back to policy.tiers["complex"]
6. If "complex" not found → raise ValueError (misconfigured policy)
7. Return RoutingDecision(provider, model, tier)
```

### Example trace

Request: `feature_tag="autocomplete"`, `classification.complexity="complex"`

```
Policy has override: { match: { feature_tag: autocomplete }, tier: simple }
→ Override matches: use tier "simple"
→ Resolve policy.tiers["simple"] → { provider: anthropic, model: claude-haiku-4-5-20251001 }
→ RoutingDecision(provider="anthropic", model="claude-haiku-4-5-20251001", tier="simple")
```

Despite the classifier saying "complex", the feature tag override wins.

---

## 5. Policy Loading and Caching

- Policies are loaded from disk on first access and cached with `@lru_cache(maxsize=32)`.
- The cache key is the filename string.
- Cache is invalidated only on process restart in MVP.
- YAML parsing errors at load time raise `pydantic.ValidationError` and propagate as a 500 to the client.
- The `POLICIES_DIR` environment variable controls the directory path (default: `policies/`).

---

## 6. Adding a New Tier

Tiers are arbitrary string keys in the YAML — you can add tiers beyond the four defaults. The classifier must also be updated to emit the new tier name. Steps:

1. Add the new tier string to `Classification.complexity`'s `Literal` type.
2. Add the tier to `classifier/heuristic.py` scoring logic.
3. Add the tier to all policy YAML files that need it.
4. No routing engine code changes are needed.

---

## 7. Failure Behavior

| Failure | Behavior |
|---------|----------|
| Policy file not found | Fall back to `default_policy.yaml`; log warning |
| Policy file malformed YAML | Raise 500; log error with filename |
| Policy file has unknown provider | Raise 500 at routing time (caught at provider registry lookup) |
| Tier not in policy | Fall back to `complex` tier; log warning |
| `complex` tier also missing | Raise 500; this is a misconfigured policy |

---

## 8. Multi-Tenant Design

- Each tenant gets one policy file. The tenant ID is provided via the `X-Tenant-Id` header.
- Tenant IDs that are not found on disk silently use the default policy (no error exposed to the client).
- There is no mechanism in MVP for tenants to modify their own policy file via API — policy changes require an ops-side file update + process restart.

---

## 9. Future: Dynamic Policy Store

If runtime policy editing is required, introduce a `PolicyStore` protocol:

```python
class PolicyStore(Protocol):
    async def get(self, tenant_id: str | None) -> Policy: ...
```

Implementations: `FilePolicyStore` (current), `DbPolicyStore` (future).
The routing engine would call `await policy_store.get(tenant_id)` instead of the synchronous `get_policy()` call.
