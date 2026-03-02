# Provider Adapter Specification

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Purpose

Provider adapters encapsulate all provider-specific logic so the rest of the gateway never needs to know which provider it's talking to. Each adapter translates the internal request/response schema to the provider's native API and back.

---

## 2. Interface Contract

Every provider adapter must implement `BaseProvider`:

```python
class BaseProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        request: ChatCompletionRequest,
        model: str,
    ) -> ChatCompletionResponse:
        """Non-streaming completion. Must return a fully populated ChatCompletionResponse."""

    @abstractmethod
    async def stream(
        self,
        request: ChatCompletionRequest,
        model: str,
    ) -> AsyncIterator[bytes]:
        """
        Streaming completion. Must yield OpenAI-compatible SSE byte chunks.
        Must terminate with b"data: [DONE]\n\n".
        """
```

### `complete()` contract
- Must return `ChatCompletionResponse` with `id`, `created`, `model`, and at least one `choice`.
- `usage` field should be populated if the provider returns token counts. May be `None` if not available.
- Must raise a plain `Exception` (not an HTTP exception) on provider error. The gateway layer converts this to HTTP 502.
- Must not mutate the `request` object.

### `stream()` contract
- Must be an `AsyncIterator` (implement via `async def stream(...): ... yield ...` or `async for ... yield`).
- Each yielded chunk must be valid UTF-8 bytes.
- Chunk format must be OpenAI-compatible SSE regardless of the native provider format.
- Must yield `b"data: [DONE]\n\n"` as the final chunk.
- Must raise a plain `Exception` on error.

---

## 3. OpenAI-Compatible SSE Chunk Format

All streaming adapters must produce chunks in this format:

```
data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"<text>"},"finish_reason":null}]}\n\n
```

Final chunk (finish):
```
data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n
```

Terminator:
```
data: [DONE]\n\n
```

The `id` and other metadata fields should be populated where available. The dashboard and telemetry collector do not parse SSE chunks — they are forwarded directly to the client.

---

## 4. Provider Registry

Providers are registered in `gateway/providers/registry.py`:

```python
_REGISTRY: dict[str, type[BaseProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}
```

Provider instances are cached via `@lru_cache(maxsize=8)` keyed on provider name. Providers are instantiated once per process.

To add a new provider:
1. Create `gateway/providers/<name>.py` implementing `BaseProvider`.
2. Add an entry to `_REGISTRY`.
3. Add the provider name to `policies/*.yaml` as needed.

---

## 5. Anthropic Adapter Notes

**System prompt handling:** Anthropic's API requires the system prompt to be passed as a top-level `system` parameter, not inside the `messages` array. The adapter must:
- Extract any message with `role == "system"` from the messages list.
- Pass the extracted text as `system=<text>` to the Anthropic SDK.
- Pass remaining messages (user, assistant, tool) in the `messages` array.

**Token usage:** Anthropic returns `input_tokens` and `output_tokens`. Map to `prompt_tokens` and `completion_tokens` in `UsageInfo`.

**Streaming:** Anthropic's streaming format differs from OpenAI's. The adapter must translate each Anthropic `text_delta` event to an OpenAI-format SSE chunk.

**Max tokens:** If `request.max_tokens` is `None`, default to `1024`. The Anthropic API requires `max_tokens` to be set.

---

## 6. OpenAI Adapter Notes

**Request translation:** OpenAI's API accepts the OpenAI message format natively. Minimal translation required.

**System prompt:** Passed as a regular `{"role": "system", "content": "..."}` message — no special handling needed.

**Streaming:** OpenAI's native streaming format is already OpenAI-compatible. Forward chunks with minimal processing. Use the OpenAI SDK's async stream context manager.

**Usage:** Present in the final chunk only when `stream_options: {"include_usage": true}` is passed. In MVP, usage in streaming mode is not reliably available — set `tokens_out` to 0 in the telemetry emit for streaming requests.

---

## 7. Error Handling

Providers should let SDK exceptions propagate naturally. The gateway's `chat.py` catches all exceptions and wraps them in HTTP 502. Do not catch and swallow exceptions inside adapters.

Exceptions that indicate a retryable error (rate limit, 503) vs. a hard error (invalid API key, 400 bad request) are not distinguished in MVP — both result in 502. A future implementation may inspect the exception type and return 429 or 401 accordingly.

---

## 8. Adding a New Provider — Checklist

- [ ] Create `gateway/providers/<name>.py`
- [ ] Implement `complete()` returning `ChatCompletionResponse`
- [ ] Implement `stream()` yielding OpenAI-compatible SSE bytes + `[DONE]`
- [ ] Handle system prompt extraction if the provider requires it
- [ ] Populate `UsageInfo` from the provider's token count fields
- [ ] Add to `_REGISTRY` in `registry.py`
- [ ] Add to `_COST_TABLE` in `telemetry/collector.py` with current pricing
- [ ] Add integration test in `gateway/tests/test_providers.py`
- [ ] Update `policies/default_policy.yaml` with an example entry
- [ ] Document any provider-specific quirks in this spec
