/**
 * /api/stats — aggregates cost data from the gateway DB.
 *
 * Query params:
 *   window  = 1h | 24h | 7d | 30d  (default: 24h)
 *   tenant  = <tenant_id>           (optional)
 *
 * Returns:
 *   { totalCostUsd, baselineCostUsd, savingsUsd, savingsPct,
 *     byFeature: [{featureTag, costUsd, baselineCostUsd}],
 *     byModel:   [{model, costUsd, requestCount}] }
 */

import { NextRequest, NextResponse } from "next/server";

// GATEWAY_INTERNAL_URL is for server-to-server calls inside Docker (e.g. http://gateway:8000).
// Falls back to NEXT_PUBLIC_GATEWAY_URL for local dev outside Docker.
const GATEWAY_URL =
  process.env.GATEWAY_INTERNAL_URL ??
  process.env.NEXT_PUBLIC_GATEWAY_URL ??
  "http://localhost:8000";

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl;
  const window = searchParams.get("window") ?? "24h";
  const tenant = searchParams.get("tenant") ?? "";

  const url = new URL("/internal/stats", GATEWAY_URL);
  url.searchParams.set("window", window);
  if (tenant) url.searchParams.set("tenant", tenant);

  const resp = await fetch(url.toString(), { next: { revalidate: 60 } });
  if (!resp.ok) {
    return NextResponse.json({ error: "Gateway error" }, { status: resp.status });
  }
  const data = await resp.json();
  return NextResponse.json(data);
}
