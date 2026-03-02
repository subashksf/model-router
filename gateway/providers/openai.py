"""OpenAI provider adapter."""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import openai

from gateway.providers.base import BaseProvider
from gateway.schemas import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Message,
    UsageInfo,
)


class OpenAIProvider(BaseProvider):
    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI()

    def _messages(self, messages: list[Message]) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in messages]

    async def complete(
        self, request: ChatCompletionRequest, model: str
    ) -> ChatCompletionResponse:
        resp = await self._client.chat.completions.create(
            model=model,
            messages=self._messages(request.messages),
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=False,
        )
        choice = resp.choices[0]
        return ChatCompletionResponse(
            id=resp.id,
            created=int(time.time()),
            model=model,
            choices=[
                ChatChoice(
                    message=Message(role="assistant", content=choice.message.content or ""),
                    finish_reason=choice.finish_reason or "stop",
                )
            ],
            usage=UsageInfo(
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                total_tokens=resp.usage.total_tokens,
            ) if resp.usage else None,
        )

    async def stream(
        self, request: ChatCompletionRequest, model: str
    ) -> AsyncIterator[bytes]:
        async with await self._client.chat.completions.create(
            model=model,
            messages=self._messages(request.messages),
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
        ) as stream:
            async for chunk in stream:
                yield (f"data: {chunk.model_dump_json()}\n\n").encode()
        yield b"data: [DONE]\n\n"
