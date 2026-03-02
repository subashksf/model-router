"""
Mock provider — returns fake responses instantly, no API key required.
Use this for local development and smoke-testing the routing logic.

Enable by setting any policy tier to:
  provider: mock
  model: mock-<anything>
"""

from __future__ import annotations

import time
import uuid
from typing import AsyncIterator

from gateway.providers.base import BaseProvider
from gateway.schemas import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Message,
    UsageInfo,
)


class MockProvider(BaseProvider):
    async def complete(
        self, request: ChatCompletionRequest, model: str
    ) -> ChatCompletionResponse:
        last_user_msg = next(
            (m.content for m in reversed(request.messages) if m.role == "user"),
            "Hello",
        )
        reply = f"[mock:{model}] You said: {last_user_msg!r}"

        tokens_in = sum(
            len(m.content) // 4 if isinstance(m.content, str) else 10
            for m in request.messages
        )
        tokens_out = len(reply) // 4

        return ChatCompletionResponse(
            id=f"mock-{uuid.uuid4().hex[:8]}",
            created=int(time.time()),
            model=model,
            choices=[
                ChatChoice(message=Message(role="assistant", content=reply))
            ],
            usage=UsageInfo(
                prompt_tokens=tokens_in,
                completion_tokens=tokens_out,
                total_tokens=tokens_in + tokens_out,
            ),
        )

    async def stream(
        self, request: ChatCompletionRequest, model: str
    ) -> AsyncIterator[bytes]:
        resp = await self.complete(request, model)
        content = resp.choices[0].message.content
        chunk_id = resp.id

        # Stream word by word so it feels realistic
        for word in content.split():
            chunk = (
                f'data: {{"id":"{chunk_id}","object":"chat.completion.chunk",'
                f'"choices":[{{"index":0,"delta":{{"content":{word + " "!r}}},'
                f'"finish_reason":null}}]}}\n\n'
            )
            yield chunk.encode()

        yield (
            f'data: {{"id":"{chunk_id}","object":"chat.completion.chunk",'
            f'"choices":[{{"index":0,"delta":{{}},"finish_reason":"stop"}}]}}\n\n'
        ).encode()
        yield b"data: [DONE]\n\n"
