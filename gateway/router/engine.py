"""Map a Classification to a concrete provider + model via the active policy."""

from __future__ import annotations

from dataclasses import dataclass

from gateway.classifier.heuristic import Classification
from gateway.router.policy import TierConfig, get_policy


@dataclass
class RoutingDecision:
    provider: str
    model: str
    tier: str

    @classmethod
    def from_tier_config(cls, tier: str, cfg: TierConfig) -> "RoutingDecision":
        return cls(provider=cfg.provider, model=cfg.model, tier=tier)


def route(
    classification: Classification,
    tenant_id: str | None = None,
    feature_tag: str | None = None,
) -> RoutingDecision:
    policy = get_policy(tenant_id)

    # Check overrides first (first match wins)
    for override in policy.overrides:
        match = override.match
        if feature_tag and match.get("feature_tag") == feature_tag:
            tier_name = override.tier
            break
    else:
        tier_name = classification.complexity

    # Fall back to "complex" if the tier is missing in the policy
    tier_cfg = policy.tiers.get(tier_name) or policy.tiers.get("complex")
    if tier_cfg is None:
        raise ValueError(f"No tier config found for '{tier_name}' in policy")

    return RoutingDecision.from_tier_config(tier_name, tier_cfg)
