"""Anthropic provider adapter."""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import anthropic

from gateway.providers.base import BaseProvider
from gateway.schemas import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Message,
    UsageInfo,
)


class AnthropicProvider(BaseProvider):
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic()

    def _build_messages(self, messages: list[Message]) -> tuple[str | None, list[dict]]:
        """Split system prompt from user/assistant turns."""
        system: str | None = None
        turns = []
        for m in messages:
            if m.role == "system":
                system = m.content if isinstance(m.content, str) else str(m.content)
            else:
                turns.append({"role": m.role, "content": m.content})
        return system, turns

    async def complete(
        self, request: ChatCompletionRequest, model: str
    ) -> ChatCompletionResponse:
        system, turns = self._build_messages(request.messages)

        kwargs: dict = dict(
            model=model,
            messages=turns,
            max_tokens=request.max_tokens or 1024,
        )
        if system:
            kwargs["system"] = system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        resp = await self._client.messages.create(**kwargs)

        return ChatCompletionResponse(
            id=resp.id,
            created=int(time.time()),
            model=model,
            choices=[
                ChatChoice(
                    message=Message(role="assistant", content=resp.content[0].text),
                    finish_reason=resp.stop_reason or "stop",
                )
            ],
            usage=UsageInfo(
                prompt_tokens=resp.usage.input_tokens,
                completion_tokens=resp.usage.output_tokens,
                total_tokens=resp.usage.input_tokens + resp.usage.output_tokens,
            ),
        )

    async def stream(
        self, request: ChatCompletionRequest, model: str
    ) -> AsyncIterator[bytes]:
        system, turns = self._build_messages(request.messages)

        kwargs: dict = dict(
            model=model,
            messages=turns,
            max_tokens=request.max_tokens or 1024,
        )
        if system:
            kwargs["system"] = system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        async with self._client.messages.stream(**kwargs) as stream:
            async for text_chunk in stream.text_stream:
                payload = json.dumps({
                    "choices": [{"delta": {"content": text_chunk}, "index": 0, "finish_reason": None}]
                })
                yield f"data: {payload}\n\n".encode()
        yield b"data: [DONE]\n\n"
