"""POST /v1/chat/completions — OpenAI-compatible endpoint."""

import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from gateway.classifier.heuristic import classify
from gateway.providers.registry import get_provider
from gateway.router.engine import route
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse
from gateway.telemetry.collector import emit

router = APIRouter(tags=["chat"])


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    x_feature_tag: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
):
    started_at = time.monotonic()

    # 1. Classify
    classification = await classify(body.messages)

    # 2. Route → select provider + model
    routing = route(classification, tenant_id=x_tenant_id)

    # 3. Call provider
    provider = get_provider(routing.provider)
    try:
        if body.stream:
            async def stream_with_telemetry():
                tokens_out = 0
                async for chunk in provider.stream(body, routing.model):
                    tokens_out += 1  # rough; providers should emit usage events
                    yield chunk
                latency = time.monotonic() - started_at
                await emit(
                    tenant_id=x_tenant_id,
                    feature_tag=x_feature_tag,
                    classification=classification,
                    routing=routing,
                    tokens_in=classification.token_count,
                    tokens_out=tokens_out,
                    latency_s=latency,
                )

            return StreamingResponse(stream_with_telemetry(), media_type="text/event-stream")

        response: ChatCompletionResponse = await provider.complete(body, routing.model)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 4. Emit telemetry (non-blocking)
    latency = time.monotonic() - started_at
    await emit(
        tenant_id=x_tenant_id,
        feature_tag=x_feature_tag,
        classification=classification,
        routing=routing,
        tokens_in=response.usage.prompt_tokens if response.usage else classification.token_count,
        tokens_out=response.usage.completion_tokens if response.usage else 0,
        latency_s=latency,
    )

    return response
