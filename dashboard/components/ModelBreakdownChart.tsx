"use client";

import useSWR from "swr";
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const COLORS = ["#6366f1", "#22d3ee", "#4ade80", "#f59e0b", "#f87171"];

export function ModelBreakdownChart() {
  const { data, isLoading, error } = useSWR("/api/stats?window=7d", fetcher, {
    refreshInterval: 15_000,
  });

  return (
    <div style={{ background: "#1e2130", borderRadius: 8, padding: "1.25rem", border: "1px solid #2d3148" }}>
      <h2 style={{ fontSize: "0.875rem", color: "#94a3b8", marginBottom: "1rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        Spend by Model — 7 days
      </h2>

      {isLoading && <p style={{ color: "#94a3b8" }}>Loading…</p>}
      {error && <p style={{ color: "#f87171" }}>Error loading data.</p>}

      {data?.byModel && (
        <ResponsiveContainer width="100%" height={280}>
          <PieChart>
            <Pie
              data={data.byModel}
              dataKey="costUsd"
              nameKey="model"
              cx="50%"
              cy="50%"
              outerRadius={100}
              label={({ model, percent }) => `${model} ${(percent * 100).toFixed(0)}%`}
              labelLine={false}
            >
              {data.byModel.map((_: unknown, i: number) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{ background: "#0f1117", border: "1px solid #2d3148" }}
              formatter={(v: number) => [`$${v.toFixed(4)}`, "cost"]}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
