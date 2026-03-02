"""Shared fixtures available to all tests."""

import pytest
import yaml

from gateway.providers.registry import get_provider
from gateway.router.policy import load_policy
from gateway.schemas import ChatChoice, ChatCompletionResponse, Message, UsageInfo

# ---------------------------------------------------------------------------
# Standard test policy — uses "mock" provider so no real API calls happen
# ---------------------------------------------------------------------------

MOCK_POLICY = {
    "tiers": {
        "simple":    {"provider": "mock", "model": "mock-cheap"},
        "moderate":  {"provider": "mock", "model": "mock-mid"},
        "complex":   {"provider": "mock", "model": "mock-premium"},
        "sensitive": {"provider": "mock", "model": "mock-safe"},
    },
    "overrides": [],
}


@pytest.fixture(autouse=True)
def clear_lru_caches():
    """Clear all lru_cache'd functions before every test to prevent cache bleed."""
    load_policy.cache_clear()
    get_provider.cache_clear()
    yield
    load_policy.cache_clear()
    get_provider.cache_clear()


@pytest.fixture
def policy_dir(tmp_path, monkeypatch):
    """
    Write a default_policy.yaml to a temp dir and point POLICIES_DIR at it.
    Returns the Path so tests can add extra tenant files.
    """
    (tmp_path / "default_policy.yaml").write_text(yaml.dump(MOCK_POLICY))
    monkeypatch.setenv("POLICIES_DIR", str(tmp_path))
    monkeypatch.setenv("DEFAULT_POLICY", "default_policy.yaml")
    return tmp_path


@pytest.fixture
def mock_chat_response():
    """A deterministic ChatCompletionResponse for use in provider mocks."""
    return ChatCompletionResponse(
        id="test-id",
        created=0,
        model="mock-cheap",
        choices=[ChatChoice(message=Message(role="assistant", content="hello"))],
        usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
