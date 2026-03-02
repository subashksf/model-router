"""Unit tests for gateway.classifier.heuristic."""

import pytest

from gateway.classifier.heuristic import (
    _CHARS_PER_TOKEN,
    _COMPLEX_THRESHOLD,
    _SIMPLE_THRESHOLD,
    classify,
)
from gateway.schemas import Message


def msg(content: str, role: str = "user") -> list[Message]:
    return [Message(role=role, content=content)]


# ---------------------------------------------------------------------------
# Helpers to build texts of a known token size
# ---------------------------------------------------------------------------

def chars_for_tokens(n: int) -> str:
    """Return a string that maps to exactly n tokens under the heuristic."""
    return "z" * (n * _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Basic tier routing by token count
# ---------------------------------------------------------------------------

class TestTokenCountTiers:
    async def test_short_text_is_simple(self):
        result = await classify(msg("What is the capital of France?"))
        assert result.complexity == "simple"

    async def test_simple_threshold_boundary_is_simple(self):
        # Exactly _SIMPLE_THRESHOLD tokens → simple
        result = await classify(msg(chars_for_tokens(_SIMPLE_THRESHOLD)))
        assert result.complexity == "simple"

    async def test_above_simple_threshold_is_moderate(self):
        # One token above the simple threshold → moderate
        result = await classify(msg(chars_for_tokens(_SIMPLE_THRESHOLD + 1)))
        assert result.complexity == "moderate"

    async def test_complex_threshold_boundary_is_complex(self):
        # Exactly _COMPLEX_THRESHOLD tokens → complex
        result = await classify(msg(chars_for_tokens(_COMPLEX_THRESHOLD)))
        assert result.complexity == "complex"
        assert "long_context" in result.signals

    async def test_long_text_is_complex(self):
        result = await classify(msg(chars_for_tokens(_COMPLEX_THRESHOLD + 100)))
        assert result.complexity == "complex"
        assert "long_context" in result.signals

    async def test_token_count_is_stored(self):
        text = "hello"
        result = await classify(msg(text))
        expected_tokens = max(1, len(text) // _CHARS_PER_TOKEN)
        assert result.token_count == expected_tokens


# ---------------------------------------------------------------------------
# Sensitive keyword detection
# ---------------------------------------------------------------------------

class TestSensitiveKeywords:
    @pytest.mark.parametrize("text", [
        "my SSN is 123-45-6789",
        "social security number required",
        "passport details",
        "enter your credit card",
        "this is HIPAA protected",
        "contains PHI data",
        "PII must not be stored",
        "consult an attorney",
        "I need legal advice",
        "involved in a lawsuit",
        "this is privileged information",
        "document is confidential",
        "do not share the password",
        "here is the secret key",
        "rotate the api key now",
    ])
    async def test_sensitive_keyword_overrides_tier(self, text):
        result = await classify(msg(text))
        assert result.complexity == "sensitive"
        assert "sensitive_keyword" in result.signals

    async def test_sensitive_overrides_long_context(self):
        """A very long text with a sensitive keyword must still be sensitive."""
        long_sensitive = chars_for_tokens(_COMPLEX_THRESHOLD + 500) + " confidential"
        result = await classify(msg(long_sensitive))
        assert result.complexity == "sensitive"

    async def test_sensitive_is_case_insensitive(self):
        result = await classify(msg("My PASSPORT details are here"))
        assert result.complexity == "sensitive"


# ---------------------------------------------------------------------------
# Complex keyword detection
# ---------------------------------------------------------------------------

class TestComplexKeywords:
    @pytest.mark.parametrize("text", [
        "Implement a distributed rate limiter",
        "Refactor this module to use async",
        "Architect a microservices system",
        "Explain the design pattern used here step by step",
        "What algorithm should I use for sorting?",
        "Optimize this SQL query",
        "Debug this failing function",
        "Compare these two approaches",
        "Analyze the time complexity",
        "Write a function to parse JSON",
        "Generate code for a binary tree",
    ])
    async def test_complex_keyword_upgrades_to_complex(self, text):
        result = await classify(msg(text))
        assert result.complexity == "complex"
        assert "complex_keyword" in result.signals

    async def test_complex_keyword_upgrades_short_text(self):
        """A short message with a complex keyword must be complex, not simple."""
        result = await classify(msg("Implement it"))
        assert result.complexity == "complex"


# ---------------------------------------------------------------------------
# Simple keyword detection
# ---------------------------------------------------------------------------

class TestSimpleKeywords:
    async def test_simple_keyword_downgrades_moderate_to_simple(self):
        """
        Moderate-length text (301–1499 tokens) with a simple keyword
        should be downgraded to simple.
        """
        # Build a moderate-length text starting with a simple keyword phrase
        padding = chars_for_tokens(_SIMPLE_THRESHOLD + 2)
        text = "What is " + padding
        result = await classify(msg(text))
        assert result.complexity == "simple"
        assert "simple_keyword" in result.signals

    async def test_simple_keyword_does_not_downgrade_complex(self):
        """A long text with a simple keyword prefix stays complex."""
        padding = chars_for_tokens(_COMPLEX_THRESHOLD + 10)
        text = "What is " + padding
        result = await classify(msg(text))
        assert result.complexity == "complex"

    async def test_complex_keyword_beats_simple_keyword(self):
        """When both keyword types appear, complex wins."""
        result = await classify(msg("What is the best algorithm to implement?"))
        assert result.complexity == "complex"


# ---------------------------------------------------------------------------
# Multi-modal content
# ---------------------------------------------------------------------------

class TestMultiModalContent:
    async def test_text_part_extracted_from_list_content(self):
        messages = [Message(role="user", content=[{"type": "text", "text": "hello"}])]
        result = await classify(messages)
        assert result.complexity == "simple"
        assert result.token_count >= 1

    async def test_non_text_parts_are_skipped(self):
        messages = [Message(role="user", content=[
            {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
            {"type": "text", "text": "Describe this image"},
        ])]
        result = await classify(messages)
        # Should not raise; text part contributes to classification
        assert result.complexity in {"simple", "moderate", "complex", "sensitive"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    async def test_empty_messages_list_does_not_raise(self):
        result = await classify([])
        assert result.complexity in {"simple", "moderate", "complex", "sensitive"}
        assert result.token_count >= 1

    async def test_system_role_contributes_to_classification(self):
        messages = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Hi"),
        ]
        result = await classify(messages)
        assert result.complexity == "simple"

    async def test_multiple_messages_are_concatenated(self):
        """Multiple short messages summing to > complex threshold → complex."""
        chunk = chars_for_tokens(_COMPLEX_THRESHOLD // 3 + 10)
        messages = [Message(role="user", content=chunk) for _ in range(3)]
        result = await classify(messages)
        assert result.complexity == "complex"

    async def test_signals_is_list(self):
        result = await classify(msg("hello"))
        assert isinstance(result.signals, list)

    async def test_score_property(self):
        result = await classify(msg("hello"))
        assert result.score in {0, 1, 2, 3}
