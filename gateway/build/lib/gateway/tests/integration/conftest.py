"""Fixtures for integration tests — spins up the full FastAPI app with mocked I/O."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from gateway.schemas import ChatChoice, ChatCompletionResponse, Message, UsageInfo

# ---------------------------------------------------------------------------
# Standard mock provider response
# ---------------------------------------------------------------------------

MOCK_RESPONSE = ChatCompletionResponse(
    id="test-id",
    created=1700000000,
    model="mock-cheap",
    choices=[ChatChoice(message=Message(role="assistant", content="hello"))],
    usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
)

MOCK_POLICY = {
    "tiers": {
        "simple":    {"provider": "mock", "model": "mock-cheap"},
        "moderate":  {"provider": "mock", "model": "mock-mid"},
        "complex":   {"provider": "mock", "model": "mock-premium"},
        "sensitive": {"provider": "mock", "model": "mock-safe"},
    },
    "overrides": [],
}


@pytest.fixture
def mock_provider(mock_chat_response):
    """A mock BaseProvider that returns a deterministic response."""
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=mock_chat_response)

    async def _stream(*args, **kwargs):
        yield b'data: {"choices":[{"delta":{"content":"hello"},"index":0}]}\n\n'
        yield b"data: [DONE]\n\n"

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    return provider


@pytest.fixture
async def client(tmp_path, monkeypatch, mock_provider):
    """
    Full FastAPI test client with:
      - DB init mocked out (no real Postgres needed)
      - Provider registry patched to return mock_provider
      - Telemetry emit patched to a no-op
      - Policy dir pointed at a temp directory
    """
    # Set up policy directory
    (tmp_path / "default_policy.yaml").write_text(yaml.dump(MOCK_POLICY))
    monkeypatch.setenv("POLICIES_DIR", str(tmp_path))
    monkeypatch.setenv("DEFAULT_POLICY", "default_policy.yaml")

    with (
        patch("gateway.db.session.init_db", new=AsyncMock()),
        patch("gateway.api.v1.chat.get_provider", return_value=mock_provider),
        patch("gateway.api.v1.chat.emit", new=AsyncMock()),
    ):
        from gateway.main import app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
