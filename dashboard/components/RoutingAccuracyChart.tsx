"use client";

import useSWR from "swr";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  LabelList,
  ResponsiveContainer,
} from "recharts";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const TIER_COLORS: Record<string, string> = {
  simple:    "#4ade80",
  moderate:  "#60a5fa",
  complex:   "#f59e0b",
  sensitive: "#f87171",
};

const TIER_ORDER = ["simple", "moderate", "complex", "sensitive"];

export function RoutingAccuracyChart() {
  const { data, isLoading, error } = useSWR("/api/stats?window=24h", fetcher, {
    refreshInterval: 15_000,
  });

  const rows: Array<{ tier: string; accuracy: number; totalRequests: number }> =
    data?.byTierAccuracy ?? [];

  // Ensure consistent tier order even if some tiers have no data yet
  const chartData = TIER_ORDER.map((tier) => {
    const row = rows.find((r) => r.tier === tier);
    return {
      tier,
      accuracy: row ? Number(row.accuracy) : 0,
      totalRequests: row ? row.totalRequests : 0,
    };
  });

  return (
    <div style={{ background: "#1e2130", borderRadius: 8, padding: "1.25rem", border: "1px solid #2d3148" }}>
      <h2 style={{ fontSize: "0.875rem", color: "#94a3b8", marginBottom: "1rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        Routing Accuracy by Tier — 24 h
      </h2>

      {isLoading && <p style={{ color: "#94a3b8" }}>Loading…</p>}
      {error && <p style={{ color: "#f87171" }}>Error loading data.</p>}

      {!isLoading && !error && (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData} margin={{ top: 20, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2d3148" />
            <XAxis dataKey="tier" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <YAxis
              domain={[0, 100]}
              tickFormatter={(v) => `${v}%`}
              tick={{ fill: "#94a3b8", fontSize: 12 }}
            />
            <Tooltip
              contentStyle={{ background: "#0f1117", border: "1px solid #2d3148" }}
              formatter={(v: number, _name: string, props) => [
                `${v.toFixed(1)}%  (n=${props.payload.totalRequests})`,
                "Accuracy",
              ]}
            />
            <Bar dataKey="accuracy" radius={[4, 4, 0, 0]} maxBarSize={60}>
              {chartData.map((entry) => (
                <Cell key={entry.tier} fill={TIER_COLORS[entry.tier] ?? "#6366f1"} />
              ))}
              <LabelList
                dataKey="accuracy"
                position="top"
                formatter={(v: number) => (v > 0 ? `${v.toFixed(0)}%` : "")}
                style={{ fill: "#f1f5f9", fontSize: 11 }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
