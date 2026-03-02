"""Shared Pydantic schemas — mirrors the OpenAI Chat Completions API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[Any]
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str = "auto"  # ignored by the router; we pick the model
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    # Pass-through any extra OpenAI params
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatChoice(BaseModel):
    index: int = 0
    message: Message
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: UsageInfo | None = None
