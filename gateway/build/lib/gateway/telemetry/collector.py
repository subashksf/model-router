"""
Async, non-blocking telemetry emitter.

Writes a row to the `usage_events` table after each request.
Cost per token is a simple lookup table — update as provider pricing changes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from gateway.classifier.heuristic import Classification
from gateway.db.session import get_session
from gateway.router.engine import RoutingDecision

log = logging.getLogger(__name__)

# Cost in USD per 1 000 tokens (input, output)
# Update these as pricing changes; later move to DB or config.
_COST_TABLE: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5-20251001":  (0.00025, 0.00125),
    "claude-sonnet-4-6":          (0.003,   0.015),
    "claude-opus-4-6":            (0.015,   0.075),
    # OpenAI
    "gpt-4o-mini":                (0.00015, 0.0006),
    "gpt-4o":                     (0.005,   0.015),
}

_BASELINE_MODEL = "gpt-4o"  # used to compute "savings vs. baseline"


def _cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = _COST_TABLE.get(model, (0.0, 0.0))
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000


async def _write(
    *,
    tenant_id: str | None,
    feature_tag: str | None,
    classification: Classification,
    routing: RoutingDecision,
    tokens_in: int,
    tokens_out: int,
    latency_s: float,
) -> None:
    cost = _cost_usd(routing.model, tokens_in, tokens_out)
    baseline_cost = _cost_usd(_BASELINE_MODEL, tokens_in, tokens_out)

    async with get_session() as session:
        await session.execute(
            """
            INSERT INTO usage_events (
                ts, tenant_id, feature_tag,
                complexity, tier, provider, model,
                tokens_in, tokens_out, latency_ms,
                cost_usd, baseline_cost_usd
            ) VALUES (
                :ts, :tenant_id, :feature_tag,
                :complexity, :tier, :provider, :model,
                :tokens_in, :tokens_out, :latency_ms,
                :cost_usd, :baseline_cost_usd
            )
            """,
            {
                "ts": datetime.now(timezone.utc),
                "tenant_id": tenant_id,
                "feature_tag": feature_tag,
                "complexity": classification.complexity,
                "tier": routing.tier,
                "provider": routing.provider,
                "model": routing.model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": int(latency_s * 1000),
                "cost_usd": cost,
                "baseline_cost_usd": baseline_cost,
            },
        )
        await session.commit()


async def emit(
    *,
    tenant_id: str | None,
    feature_tag: str | None,
    classification: Classification,
    routing: RoutingDecision,
    tokens_in: int,
    tokens_out: int,
    latency_s: float,
) -> None:
    """Fire-and-forget: schedule the DB write without blocking the response."""
    asyncio.create_task(
        _write(
            tenant_id=tenant_id,
            feature_tag=feature_tag,
            classification=classification,
            routing=routing,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_s=latency_s,
        )
    )
