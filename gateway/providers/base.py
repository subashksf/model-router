"""Abstract provider interface — implement one class per LLM provider."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse


class BaseProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        request: ChatCompletionRequest,
        model: str,
    ) -> ChatCompletionResponse:
        """Non-streaming chat completion."""

    @abstractmethod
    async def stream(
        self,
        request: ChatCompletionRequest,
        model: str,
    ) -> AsyncIterator[bytes]:
        """Streaming chat completion — yields raw SSE bytes."""
