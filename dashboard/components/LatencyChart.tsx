"use client";

import useSWR from "swr";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const TIER_ORDER = ["simple", "moderate", "complex", "sensitive"];

export function LatencyChart() {
  const { data, isLoading, error } = useSWR("/api/stats?window=24h", fetcher, {
    refreshInterval: 15_000,
  });

  const rows: Array<{
    tier: string;
    avgMs: number;
    p50Ms: number;
    p95Ms: number;
    p99Ms: number;
  }> = data?.byTierLatency ?? [];

  const chartData = TIER_ORDER.map((tier) => {
    const row = rows.find((r) => r.tier === tier);
    return {
      tier,
      avg:  row ? Math.round(Number(row.avgMs))  : 0,
      p50:  row ? Math.round(Number(row.p50Ms))  : 0,
      p95:  row ? Math.round(Number(row.p95Ms))  : 0,
      p99:  row ? Math.round(Number(row.p99Ms))  : 0,
    };
  });

  return (
    <div style={{ background: "#1e2130", borderRadius: 8, padding: "1.25rem", border: "1px solid #2d3148" }}>
      <h2 style={{ fontSize: "0.875rem", color: "#94a3b8", marginBottom: "1rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        Latency by Tier (ms) — 24 h
      </h2>

      {isLoading && <p style={{ color: "#94a3b8" }}>Loading…</p>}
      {error && <p style={{ color: "#f87171" }}>Error loading data.</p>}

      {!isLoading && !error && (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2d3148" />
            <XAxis dataKey="tier" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <YAxis
              tickFormatter={(v) => `${v}ms`}
              tick={{ fill: "#94a3b8", fontSize: 12 }}
            />
            <Tooltip
              contentStyle={{ background: "#0f1117", border: "1px solid #2d3148" }}
              formatter={(v: number, name: string) => [`${v} ms`, name]}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="avg" name="avg"  fill="#94a3b8" radius={[3, 3, 0, 0]} maxBarSize={18} />
            <Bar dataKey="p50" name="p50"  fill="#60a5fa" radius={[3, 3, 0, 0]} maxBarSize={18} />
            <Bar dataKey="p95" name="p95"  fill="#f59e0b" radius={[3, 3, 0, 0]} maxBarSize={18} />
            <Bar dataKey="p99" name="p99"  fill="#f87171" radius={[3, 3, 0, 0]} maxBarSize={18} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
