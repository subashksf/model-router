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

export function CostByFeatureChart() {
  const { data, isLoading, error } = useSWR("/api/stats?window=7d", fetcher, {
    refreshInterval: 300_000,
  });

  return (
    <div style={{ background: "#1e2130", borderRadius: 8, padding: "1.25rem", border: "1px solid #2d3148" }}>
      <h2 style={{ fontSize: "0.875rem", color: "#94a3b8", marginBottom: "1rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        Cost by Feature — 7 days
      </h2>

      {isLoading && <p style={{ color: "#94a3b8" }}>Loading…</p>}
      {error && <p style={{ color: "#f87171" }}>Error loading data.</p>}

      {data?.byFeature && (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={data.byFeature} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2d3148" />
            <XAxis dataKey="featureTag" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={(v) => `$${v}`} />
            <Tooltip
              contentStyle={{ background: "#0f1117", border: "1px solid #2d3148" }}
              formatter={(v: number) => [`$${v.toFixed(4)}`, ""]}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="costUsd" name="Actual" fill="#6366f1" radius={[4, 4, 0, 0]} />
            <Bar dataKey="baselineCostUsd" name="Baseline" fill="#334155" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
