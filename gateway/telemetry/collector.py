"""
Async, non-blocking telemetry emitter.

Writes a row to the `usage_events` table after each request.
Cost per token is a simple lookup table — update as provider pricing changes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from gateway.classifier.heuristic import Classification
from gateway.db.session import get_session
from gateway.router.engine import RoutingDecision

log = logging.getLogger(__name__)

# Strong references to in-flight tasks — prevents GC from dropping them before they complete.
_pending: set[asyncio.Task] = set()

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
    # Mock models mirror the real Anthropic tier they stand in for
    "mock-haiku":                 (0.00025, 0.00125),   # mirrors claude-haiku  — 60x cheaper than Opus
    "mock-sonnet":                (0.003,   0.015),     # mirrors claude-sonnet —  5x cheaper than Opus
    "mock-opus":                  (0.015,   0.075),     # mirrors claude-opus   — break-even vs baseline
}

# Baseline = "what you'd pay if every request went to the top-tier model."
# The router's value proposition is everything cheaper than Opus that gets routed down.
_BASELINE_MODEL = "claude-opus-4-6"


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
    try:
        cost = _cost_usd(routing.model, tokens_in, tokens_out)
        baseline_cost = _cost_usd(_BASELINE_MODEL, tokens_in, tokens_out)

        async with get_session() as session:
            await session.execute(
                text("""
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
                """),
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
        log.info(
            "telemetry written: model=%s tokens_in=%d tokens_out=%d cost=%.8f baseline=%.8f",
            routing.model, tokens_in, tokens_out, cost, baseline_cost,
        )
    except Exception:
        log.exception("telemetry write failed — row dropped")


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
    task = asyncio.create_task(
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
    _pending.add(task)
    task.add_done_callback(_pending.discard)
