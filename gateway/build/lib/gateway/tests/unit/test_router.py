"""Unit tests for gateway.router.engine and gateway.router.policy."""

import pytest
import yaml

from gateway.classifier.heuristic import Classification
from gateway.router.engine import route
from gateway.router.policy import Policy, get_policy, load_policy


def make_classification(complexity: str, token_count: int = 50) -> Classification:
    return Classification(complexity=complexity, token_count=token_count)


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------

class TestPolicyLoading:
    def test_default_policy_loads(self, policy_dir):
        policy = get_policy(None)
        assert isinstance(policy, Policy)
        assert "simple" in policy.tiers

    def test_unknown_tenant_falls_back_to_default(self, policy_dir):
        policy = get_policy("tenant-that-does-not-exist")
        assert isinstance(policy, Policy)
        assert "simple" in policy.tiers

    def test_tenant_specific_policy_is_loaded(self, policy_dir):
        tenant_policy = {
            "tiers": {
                "simple":    {"provider": "openai", "model": "gpt-4o-mini"},
                "moderate":  {"provider": "openai", "model": "gpt-4o-mini"},
                "complex":   {"provider": "openai", "model": "gpt-4o"},
                "sensitive": {"provider": "openai", "model": "gpt-4o"},
            },
            "overrides": [],
        }
        (policy_dir / "acme.yaml").write_text(yaml.dump(tenant_policy))

        policy = get_policy("acme")
        assert policy.tiers["simple"].provider == "openai"
        assert policy.tiers["simple"].model == "gpt-4o-mini"

    def test_missing_policy_file_raises(self, policy_dir):
        with pytest.raises(FileNotFoundError):
            load_policy("nonexistent.yaml")

    def test_policy_is_cached(self, policy_dir):
        p1 = load_policy("default_policy.yaml")
        p2 = load_policy("default_policy.yaml")
        assert p1 is p2

    def test_malformed_yaml_raises(self, policy_dir, tmp_path):
        (policy_dir / "bad.yaml").write_text("tiers: [this: is: not: valid]")
        with pytest.raises(Exception):
            load_policy("bad.yaml")

    def test_policy_with_overrides_loads(self, policy_dir):
        policy_with_override = {
            "tiers": {
                "simple":    {"provider": "mock", "model": "mock-cheap"},
                "complex":   {"provider": "mock", "model": "mock-premium"},
                "sensitive": {"provider": "mock", "model": "mock-safe"},
            },
            "overrides": [
                {"match": {"feature_tag": "autocomplete"}, "tier": "simple"},
            ],
        }
        (policy_dir / "override_tenant.yaml").write_text(yaml.dump(policy_with_override))
        policy = get_policy("override_tenant")
        assert len(policy.overrides) == 1
        assert policy.overrides[0].tier == "simple"


# ---------------------------------------------------------------------------
# Routing decisions
# ---------------------------------------------------------------------------

class TestRoutingDecisions:
    def test_simple_routes_to_cheap_tier(self, policy_dir):
        decision = route(make_classification("simple"))
        assert decision.model == "mock-cheap"
        assert decision.provider == "mock"
        assert decision.tier == "simple"

    def test_moderate_routes_to_mid_tier(self, policy_dir):
        decision = route(make_classification("moderate"))
        assert decision.model == "mock-mid"
        assert decision.tier == "moderate"

    def test_complex_routes_to_premium_tier(self, policy_dir):
        decision = route(make_classification("complex"))
        assert decision.model == "mock-premium"
        assert decision.tier == "complex"

    def test_sensitive_routes_to_safe_tier(self, policy_dir):
        decision = route(make_classification("sensitive"))
        assert decision.model == "mock-safe"
        assert decision.tier == "sensitive"

    def test_response_model_differs_from_request_model(self, policy_dir):
        """The routed model is always from policy, never the client's requested model."""
        decision = route(make_classification("simple"))
        assert decision.model != "gpt-4o"  # client might request gpt-4o; we ignore it


# ---------------------------------------------------------------------------
# Feature-tag overrides
# ---------------------------------------------------------------------------

class TestFeatureTagOverrides:
    def _policy_with_override(self, policy_dir, feature_tag: str, target_tier: str):
        policy = {
            "tiers": {
                "simple":    {"provider": "mock", "model": "mock-cheap"},
                "moderate":  {"provider": "mock", "model": "mock-mid"},
                "complex":   {"provider": "mock", "model": "mock-premium"},
                "sensitive": {"provider": "mock", "model": "mock-safe"},
            },
            "overrides": [
                {"match": {"feature_tag": feature_tag}, "tier": target_tier},
            ],
        }
        (policy_dir / "default_policy.yaml").write_text(yaml.dump(policy))
        load_policy.cache_clear()

    def test_feature_tag_overrides_complex_to_simple(self, policy_dir):
        self._policy_with_override(policy_dir, "autocomplete", "simple")
        decision = route(make_classification("complex"), feature_tag="autocomplete")
        assert decision.tier == "simple"
        assert decision.model == "mock-cheap"

    def test_feature_tag_overrides_simple_to_sensitive(self, policy_dir):
        self._policy_with_override(policy_dir, "legal-review", "sensitive")
        decision = route(make_classification("simple"), feature_tag="legal-review")
        assert decision.tier == "sensitive"
        assert decision.model == "mock-safe"

    def test_unmatched_feature_tag_uses_classification(self, policy_dir):
        self._policy_with_override(policy_dir, "autocomplete", "simple")
        decision = route(make_classification("complex"), feature_tag="summarization")
        assert decision.tier == "complex"

    def test_no_feature_tag_uses_classification(self, policy_dir):
        self._policy_with_override(policy_dir, "autocomplete", "simple")
        decision = route(make_classification("complex"), feature_tag=None)
        assert decision.tier == "complex"

    def test_first_override_wins(self, policy_dir):
        policy = {
            "tiers": {
                "simple":    {"provider": "mock", "model": "mock-cheap"},
                "complex":   {"provider": "mock", "model": "mock-premium"},
                "sensitive": {"provider": "mock", "model": "mock-safe"},
            },
            "overrides": [
                {"match": {"feature_tag": "docs"}, "tier": "simple"},
                {"match": {"feature_tag": "docs"}, "tier": "sensitive"},  # never reached
            ],
        }
        (policy_dir / "default_policy.yaml").write_text(yaml.dump(policy))
        load_policy.cache_clear()

        decision = route(make_classification("complex"), feature_tag="docs")
        assert decision.tier == "simple"


# ---------------------------------------------------------------------------
# Fallback and error cases
# ---------------------------------------------------------------------------

class TestFallbackBehavior:
    def test_missing_tier_falls_back_to_complex(self, policy_dir):
        """If a tier is missing from policy, falls back to complex."""
        policy_no_simple = {
            "tiers": {
                # "simple" is intentionally missing
                "complex":   {"provider": "mock", "model": "mock-premium"},
                "sensitive": {"provider": "mock", "model": "mock-safe"},
            },
            "overrides": [],
        }
        (policy_dir / "default_policy.yaml").write_text(yaml.dump(policy_no_simple))
        load_policy.cache_clear()

        decision = route(make_classification("simple"))
        assert decision.tier == "complex"

    def test_missing_complex_and_requested_tier_raises(self, policy_dir):
        """If complex fallback also missing, raises ValueError."""
        policy_minimal = {
            "tiers": {
                "sensitive": {"provider": "mock", "model": "mock-safe"},
            },
            "overrides": [],
        }
        (policy_dir / "default_policy.yaml").write_text(yaml.dump(policy_minimal))
        load_policy.cache_clear()

        with pytest.raises(ValueError, match="No tier config found"):
            route(make_classification("simple"))
