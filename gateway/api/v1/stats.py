"""GET /internal/stats — aggregated cost data consumed by the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import text

from gateway.db.session import get_session

router = APIRouter(tags=["internal"])

_WINDOWS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}


@router.get("/internal/stats")
async def stats(
    window: str = Query(default="24h"),
    tenant: str | None = Query(default=None),
):
    hours = _WINDOWS.get(window, 24)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    tenant_filter = "AND tenant_id = :tenant" if tenant else ""

    async with get_session() as session:
        # Overall totals
        totals = (
            await session.execute(
                text(
                    f"""
                    SELECT
                        COALESCE(SUM(cost_usd), 0)          AS total_cost_usd,
                        COALESCE(SUM(baseline_cost_usd), 0) AS baseline_cost_usd
                    FROM usage_events
                    WHERE ts >= :since {tenant_filter}
                    """
                ),
                {"since": since, "tenant": tenant},
            )
        ).mappings().one()

        # By feature
        by_feature_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT
                        COALESCE(feature_tag, 'untagged') AS feature_tag,
                        SUM(cost_usd)                     AS cost_usd,
                        SUM(baseline_cost_usd)            AS baseline_cost_usd,
                        COUNT(*)                          AS request_count
                    FROM usage_events
                    WHERE ts >= :since {tenant_filter}
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                ),
                {"since": since, "tenant": tenant},
            )
        ).mappings().all()

        # By model
        by_model_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT
                        model,
                        SUM(cost_usd)  AS cost_usd,
                        COUNT(*)       AS request_count
                    FROM usage_events
                    WHERE ts >= :since {tenant_filter}
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                ),
                {"since": since, "tenant": tenant},
            )
        ).mappings().all()

    total = float(totals["total_cost_usd"])
    baseline = float(totals["baseline_cost_usd"])
    savings = baseline - total

    return {
        "window": window,
        "totalCostUsd": total,
        "baselineCostUsd": baseline,
        "savingsUsd": savings,
        "savingsPct": (savings / baseline * 100) if baseline else 0,
        "byFeature": [dict(r) for r in by_feature_rows],
        "byModel": [dict(r) for r in by_model_rows],
    }
