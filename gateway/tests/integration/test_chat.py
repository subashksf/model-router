"""Integration tests for POST /v1/chat/completions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


SIMPLE_BODY = {
    "model": "auto",
    "messages": [{"role": "user", "content": "What is the capital of France?"}],
}

COMPLEX_BODY = {
    "model": "auto",
    "messages": [{"role": "user", "content": "Implement a distributed rate limiter"}],
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestChatCompletionHappyPath:
    async def test_simple_query_returns_200(self, client):
        resp = await client.post("/v1/chat/completions", json=SIMPLE_BODY)
        assert resp.status_code == 200

    async def test_response_shape_is_openai_compatible(self, client):
        resp = await client.post("/v1/chat/completions", json=SIMPLE_BODY)
        data = resp.json()
        assert "id" in data
        assert "choices" in data
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["message"]["content"] == "hello"

    async def test_response_model_reflects_routed_model(self, client):
        """The response model field is what the router chose, not what the client requested."""
        resp = await client.post("/v1/chat/completions", json=SIMPLE_BODY)
        data = resp.json()
        # Client sent model="auto"; response should have the provider's model id
        assert data["model"] == "mock-cheap"

    async def test_extra_openai_fields_are_accepted(self, client):
        body = {**SIMPLE_BODY, "temperature": 0.7, "max_tokens": 256, "top_p": 0.9}
        resp = await client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200

    async def test_system_message_is_accepted(self, client):
        body = {
            "model": "auto",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ],
        }
        resp = await client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Routing via headers
# ---------------------------------------------------------------------------

class TestRoutingHeaders:
    async def test_feature_tag_override_changes_routing(self, tmp_path, monkeypatch, mock_chat_response):
        """A feature_tag override in policy should route complex → simple tier."""
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
        (tmp_path / "default_policy.yaml").write_text(yaml.dump(policy_with_override))
        monkeypatch.setenv("POLICIES_DIR", str(tmp_path))
        monkeypatch.setenv("DEFAULT_POLICY", "default_policy.yaml")

        captured_model = {}

        async def _complete(request, model):
            captured_model["model"] = model
            return mock_chat_response

        mock_prov = MagicMock()
        mock_prov.complete = _complete

        with (
            patch("gateway.db.session.init_db", new=AsyncMock()),
            patch("gateway.api.v1.chat.get_provider", return_value=mock_prov),
            patch("gateway.api.v1.chat.emit", new=AsyncMock()),
        ):
            from gateway.main import app
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/v1/chat/completions",
                    json=COMPLEX_BODY,
                    headers={"X-Feature-Tag": "autocomplete"},
                )

        assert resp.status_code == 200
        assert captured_model["model"] == "mock-cheap"  # simple tier, not premium

    async def test_unknown_tenant_falls_back_to_default_policy(self, client):
        resp = await client.post(
            "/v1/chat/completions",
            json=SIMPLE_BODY,
            headers={"X-Tenant-Id": "tenant-that-does-not-exist"},
        )
        assert resp.status_code == 200

    async def test_missing_headers_still_works(self, client):
        resp = await client.post("/v1/chat/completions", json=SIMPLE_BODY)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestValidationErrors:
    async def test_missing_messages_returns_422(self, client):
        resp = await client.post("/v1/chat/completions", json={"model": "auto"})
        assert resp.status_code == 422

    async def test_empty_messages_list_returns_422(self, client):
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": []},
        )
        assert resp.status_code == 422

    async def test_invalid_role_returns_422(self, client):
        body = {
            "model": "auto",
            "messages": [{"role": "invalid_role", "content": "hi"}],
        }
        resp = await client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 422

    async def test_non_json_body_returns_422(self, client):
        resp = await client.post(
            "/v1/chat/completions",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------

class TestProviderErrors:
    async def test_provider_exception_returns_502(self, tmp_path, monkeypatch):
        (tmp_path / "default_policy.yaml").write_text(yaml.dump({
            "tiers": {"simple": {"provider": "mock", "model": "mock-cheap"},
                      "complex": {"provider": "mock", "model": "mock-premium"},
                      "sensitive": {"provider": "mock", "model": "mock-safe"}},
            "overrides": [],
        }))
        monkeypatch.setenv("POLICIES_DIR", str(tmp_path))
        monkeypatch.setenv("DEFAULT_POLICY", "default_policy.yaml")

        error_provider = MagicMock()
        error_provider.complete = AsyncMock(side_effect=Exception("API rate limit exceeded"))

        with (
            patch("gateway.db.session.init_db", new=AsyncMock()),
            patch("gateway.api.v1.chat.get_provider", return_value=error_provider),
            patch("gateway.api.v1.chat.emit", new=AsyncMock()),
        ):
            from gateway.main import app
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/v1/chat/completions", json=SIMPLE_BODY)

        assert resp.status_code == 502
        assert "API rate limit exceeded" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class TestStreaming:
    async def test_streaming_returns_event_stream_content_type(self, client):
        body = {**SIMPLE_BODY, "stream": True}
        resp = await client.post("/v1/chat/completions", json=body)
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    async def test_streaming_response_contains_done_sentinel(self, client):
        body = {**SIMPLE_BODY, "stream": True}
        resp = await client.post("/v1/chat/completions", json=body)
        assert b"[DONE]" in resp.content

    async def test_streaming_response_contains_content_chunk(self, client):
        body = {**SIMPLE_BODY, "stream": True}
        resp = await client.post("/v1/chat/completions", json=body)
        assert b"hello" in resp.content


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

class TestTelemetry:
    async def test_emit_is_called_after_successful_request(self, tmp_path, monkeypatch, mock_chat_response):
        (tmp_path / "default_policy.yaml").write_text(yaml.dump({
            "tiers": {
                "simple":    {"provider": "mock", "model": "mock-cheap"},
                "complex":   {"provider": "mock", "model": "mock-premium"},
                "sensitive": {"provider": "mock", "model": "mock-safe"},
            },
            "overrides": [],
        }))
        monkeypatch.setenv("POLICIES_DIR", str(tmp_path))
        monkeypatch.setenv("DEFAULT_POLICY", "default_policy.yaml")

        mock_prov = MagicMock()
        mock_prov.complete = AsyncMock(return_value=mock_chat_response)
        mock_emit = AsyncMock()

        with (
            patch("gateway.db.session.init_db", new=AsyncMock()),
            patch("gateway.api.v1.chat.get_provider", return_value=mock_prov),
            patch("gateway.api.v1.chat.emit", mock_emit),
        ):
            from gateway.main import app
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/v1/chat/completions",
                    json=SIMPLE_BODY,
                    headers={"X-Feature-Tag": "test-feature", "X-Tenant-Id": "test-tenant"},
                )

        assert resp.status_code == 200
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["feature_tag"] == "test-feature"
        assert call_kwargs["tenant_id"] == "test-tenant"
        assert call_kwargs["tokens_in"] == 10
        assert call_kwargs["tokens_out"] == 5

    async def test_telemetry_failure_does_not_fail_request(self, tmp_path, monkeypatch, mock_chat_response):
        """A DB error during telemetry write must not surface as a 500 to the client."""
        (tmp_path / "default_policy.yaml").write_text(yaml.dump({
            "tiers": {
                "simple":    {"provider": "mock", "model": "mock-cheap"},
                "complex":   {"provider": "mock", "model": "mock-premium"},
                "sensitive": {"provider": "mock", "model": "mock-safe"},
            },
            "overrides": [],
        }))
        monkeypatch.setenv("POLICIES_DIR", str(tmp_path))
        monkeypatch.setenv("DEFAULT_POLICY", "default_policy.yaml")

        mock_prov = MagicMock()
        mock_prov.complete = AsyncMock(return_value=mock_chat_response)

        # emit itself raises — simulates _write() failing inside the task
        async def _failing_emit(**kwargs):
            raise RuntimeError("DB is down")

        with (
            patch("gateway.db.session.init_db", new=AsyncMock()),
            patch("gateway.api.v1.chat.get_provider", return_value=mock_prov),
            patch("gateway.api.v1.chat.emit", side_effect=_failing_emit),
        ):
            from gateway.main import app
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/v1/chat/completions", json=SIMPLE_BODY)

        # The response must still be 200 even though telemetry failed
        assert resp.status_code == 200
