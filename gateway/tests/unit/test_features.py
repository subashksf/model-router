"""Unit tests for gateway.classifier.features."""

import pytest

from gateway.classifier.features import _CHARS_PER_TOKEN, estimate_tokens, extract_text
from gateway.schemas import Message


def msg(content: str, role: str = "user") -> list[Message]:
    return [Message(role=role, content=content)]


class TestExtractText:
    def test_single_string_message(self):
        text = extract_text(msg("hello world"))
        assert "hello world" in text

    def test_multiple_messages_are_joined(self):
        messages = [
            Message(role="user",      content="first"),
            Message(role="assistant", content="second"),
            Message(role="user",      content="third"),
        ]
        text = extract_text(messages)
        assert "first" in text
        assert "second" in text
        assert "third" in text

    def test_list_content_text_blocks_extracted(self):
        messages = [Message(role="user", content=[{"type": "text", "text": "hello"}])]
        text = extract_text(messages)
        assert "hello" in text

    def test_non_text_blocks_skipped(self):
        messages = [Message(role="user", content=[
            {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
            {"type": "text", "text": "describe this"},
        ])]
        text = extract_text(messages)
        assert "describe this" in text
        assert "image_url" not in text

    def test_empty_messages_returns_empty_string(self):
        text = extract_text([])
        assert text == ""

    def test_system_message_included(self):
        messages = [
            Message(role="system", content="You are helpful."),
            Message(role="user",   content="hi"),
        ]
        text = extract_text(messages)
        assert "helpful" in text
        assert "hi" in text

    def test_mixed_string_and_list_content(self):
        messages = [
            Message(role="user",      content="plain string"),
            Message(role="assistant", content=[{"type": "text", "text": "list block"}]),
        ]
        text = extract_text(messages)
        assert "plain string" in text
        assert "list block" in text


class TestEstimateTokens:
    def test_returns_at_least_one(self):
        assert estimate_tokens("") >= 1

    def test_short_text(self):
        text = "hi"
        assert estimate_tokens(text) >= 1

    def test_proportional_to_length(self):
        short = "a" * 4
        long  = "a" * 400
        assert estimate_tokens(long) > estimate_tokens(short)

    def test_chars_per_token_ratio(self):
        text = "x" * (10 * _CHARS_PER_TOKEN)
        assert estimate_tokens(text) == 10
