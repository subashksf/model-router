"use client";

import useSWR from "swr";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

function fmt(n: number) {
  // Use more decimal places for micro-dollar amounts so values don't collapse to "$0.00"
  const decimals = Math.abs(n) < 0.005 ? 6 : 2;
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: decimals });
}

export function SavingsWidget() {
  const { data, error, isLoading } = useSWR("/api/stats?window=24h", fetcher, {
    refreshInterval: 10_000,
  });

  if (isLoading) return <p style={{ color: "#94a3b8" }}>Loading…</p>;
  if (error || !data) return <p style={{ color: "#f87171" }}>Failed to load stats.</p>;

  const pct = data.savingsPct?.toFixed(1) ?? "—";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: "1rem",
      }}
    >
      <StatCard label="Actual spend (24 h)" value={fmt(data.totalCostUsd)} />
      <StatCard label="Baseline (all Opus)" value={fmt(data.baselineCostUsd)} color="#94a3b8" />
      <StatCard label="Savings" value={`${fmt(data.savingsUsd)} (${pct}%)`} color="#4ade80" />
    </div>
  );
}

function StatCard({ label, value, color = "#f1f5f9" }: { label: string; value: string; color?: string }) {
  return (
    <div
      style={{
        background: "#1e2130",
        borderRadius: 8,
        padding: "1.25rem 1.5rem",
        border: "1px solid #2d3148",
      }}
    >
      <p style={{ fontSize: "0.75rem", color: "#94a3b8", marginBottom: "0.5rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </p>
      <p style={{ fontSize: "1.5rem", fontWeight: 700, color }}>{value}</p>
    </div>
  );
}
