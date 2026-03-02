"""Provider registry — maps provider name strings to provider instances."""

from __future__ import annotations

from functools import lru_cache

from gateway.providers.anthropic import AnthropicProvider
from gateway.providers.base import BaseProvider
from gateway.providers.openai import OpenAIProvider

_REGISTRY: dict[str, type[BaseProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


@lru_cache(maxsize=8)
def get_provider(name: str) -> BaseProvider:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown provider '{name}'. Available: {list(_REGISTRY)}")
    return cls()
