"""
Shared feature extraction for the classifier layer.

Both the heuristic and learned classifiers use the same text extraction
so that token counts and input representations are consistent.
"""

from __future__ import annotations

from gateway.schemas import Message

_CHARS_PER_TOKEN = 4  # ~4 chars per token (GPT-style tokenisation approximation)


def extract_text(messages: list[Message]) -> str:
    """Concatenate all message content into a single string."""
    parts: list[str] = []
    for m in messages:
        if isinstance(m.content, str):
            parts.append(m.content)
        elif isinstance(m.content, list):
            for block in m.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return " ".join(parts)


def estimate_tokens(text: str) -> int:
    """Estimate token count from character count."""
    return max(1, len(text) // _CHARS_PER_TOKEN)
