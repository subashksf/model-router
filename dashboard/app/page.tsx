"use client";

import { SavingsWidget } from "@/components/SavingsWidget";
import { CostByFeatureChart } from "@/components/CostByFeatureChart";
import { ModelBreakdownChart } from "@/components/ModelBreakdownChart";

export default function DashboardPage() {
  return (
    <main style={{ padding: "2rem", maxWidth: 1200, margin: "0 auto" }}>
      <h1 style={{ marginBottom: "2rem", fontSize: "1.5rem" }}>Model Router — Cost Dashboard</h1>

      <SavingsWidget />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem", marginTop: "1.5rem" }}>
        <CostByFeatureChart />
        <ModelBreakdownChart />
      </div>
    </main>
  );
}
