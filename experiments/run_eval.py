"""
Reproducible evaluation script for model-router.

Sends each prompt in the benchmark dataset through the router, records
routing decisions, computes theoretical costs, and compares against a
naive baseline (always routing to the most expensive tier).

Usage:
    python experiments/run_eval.py
    python experiments/run_eval.py --seed 42 --gateway-url http://localhost:8000
    python experiments/run_eval.py --output experiments/results/my_run.json

Outputs:
    - JSON results file (per-prompt metrics + aggregate summary)
    - Console summary table
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Theoretical cost table (USD per 1,000 tokens)
# Must match gateway/telemetry/collector.py _COST_TABLE
# ---------------------------------------------------------------------------
COST_TABLE: dict[str, tuple[float, float]] = {
    "mock-haiku":  (0.00025, 0.00125),   # maps to Haiku pricing
    "mock-sonnet": (0.003,   0.015),      # maps to Sonnet pricing
    "mock-opus":   (0.015,   0.075),      # maps to Opus pricing
    # Real models (if used)
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    "claude-sonnet-4-6":         (0.003,   0.015),
    "claude-opus-4-6":           (0.015,   0.075),
    "gpt-4o-mini":               (0.00015, 0.0006),
    "gpt-4o":                    (0.005,   0.015),
}

BASELINE_MODEL = "mock-opus"  # "worst case" — always routing to most expensive


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = COST_TABLE.get(model, (0.005, 0.015))  # default to gpt-4o rates
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000


# ---------------------------------------------------------------------------
# Router call
# ---------------------------------------------------------------------------

def call_router(
    gateway_url: str,
    prompt: str,
    feature_tag: str | None = None,
) -> dict:
    """Send one prompt to the gateway and return the raw response dict + latency."""
    body = json.dumps({
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    headers = {"Content-Type": "application/json"}
    if feature_tag:
        headers["X-Feature-Tag"] = feature_tag

    req = urllib.request.Request(
        f"{gateway_url}/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=30) as resp:
        latency_ms = (time.monotonic() - t0) * 1000
        data = json.loads(resp.read())

    return {"response": data, "latency_ms": latency_ms}


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def run_eval(
    gateway_url: str,
    dataset_path: Path,
    output_path: Path,
    seed: int,
    max_prompts: int | None,
) -> None:
    random.seed(seed)

    # Load dataset
    with open(dataset_path) as f:
        dataset = json.load(f)

    prompts = dataset["prompts"]
    if max_prompts:
        prompts = random.sample(prompts, min(max_prompts, len(prompts)))

    print(f"\nModel Router — Evaluation Run")
    print(f"  Gateway:  {gateway_url}")
    print(f"  Dataset:  {dataset_path} ({len(prompts)} prompts)")
    print(f"  Seed:     {seed}")
    print(f"  Output:   {output_path}")
    print("-" * 70)

    results = []
    errors = []

    for i, prompt in enumerate(prompts, 1):
        pid = prompt["id"]
        text = prompt["text"]
        expected_tier = prompt["tier"]

        try:
            result = call_router(gateway_url, text)
            response = result["response"]
            latency_ms = result["latency_ms"]

            routed_model = response.get("model", "unknown")
            usage = response.get("usage") or {}
            tokens_in = usage.get("prompt_tokens", max(1, len(text) // 4))
            tokens_out = usage.get("completion_tokens", 20)

            actual_cost = cost_usd(routed_model, tokens_in, tokens_out)
            baseline_cost = cost_usd(BASELINE_MODEL, tokens_in, tokens_out)

            # Infer routed tier from model name (works for mock and real models)
            if any(x in routed_model for x in ["haiku", "mini", "cheap"]):
                routed_tier = "simple"
            elif any(x in routed_model for x in ["opus", "safe", "4"]) and "sonnet" not in routed_model:
                routed_tier = "sensitive"
            elif any(x in routed_model for x in ["sonnet", "premium", "mid", "4o"]):
                routed_tier = "complex"
            else:
                routed_tier = "unknown"

            # Tier match: sensitive and complex both map to "non-simple" — count as correct
            # if the router didn't under-route (send complex/sensitive to simple)
            tier_correct = (
                expected_tier == "simple" and routed_tier == "simple"
            ) or (
                expected_tier != "simple" and routed_tier != "simple"
            ) or (
                expected_tier == "sensitive" and routed_tier == "sensitive"
            )

            record = {
                "id": pid,
                "text": text,
                "expected_tier": expected_tier,
                "routed_model": routed_model,
                "routed_tier": routed_tier,
                "tier_correct": tier_correct,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": round(latency_ms, 1),
                "cost_usd": round(actual_cost, 8),
                "baseline_cost_usd": round(baseline_cost, 8),
                "savings_usd": round(baseline_cost - actual_cost, 8),
            }
            results.append(record)

            status = "✓" if tier_correct else "✗"
            print(
                f"  [{i:02d}/{len(prompts)}] {status} {pid:4s} "
                f"expected={expected_tier:9s} routed={routed_model:20s} "
                f"latency={latency_ms:6.1f}ms"
            )

        except (urllib.error.URLError, Exception) as e:
            errors.append({"id": pid, "error": str(e)})
            print(f"  [{i:02d}/{len(prompts)}] ERR {pid}: {e}")

    # ---------------------------------------------------------------------------
    # Aggregate metrics
    # ---------------------------------------------------------------------------
    if not results:
        print("\nNo results collected. Is the gateway running?")
        sys.exit(1)

    total = len(results)
    correct = sum(1 for r in results if r["tier_correct"])
    accuracy = correct / total

    total_cost = sum(r["cost_usd"] for r in results)
    total_baseline = sum(r["baseline_cost_usd"] for r in results)
    total_savings = total_baseline - total_cost
    savings_pct = (total_savings / total_baseline * 100) if total_baseline > 0 else 0

    avg_latency = sum(r["latency_ms"] for r in results) / total
    p99_latency = sorted(r["latency_ms"] for r in results)[int(total * 0.99)]

    by_tier: dict[str, dict] = {}
    for tier in ["simple", "moderate", "complex", "sensitive"]:
        tier_results = [r for r in results if r["expected_tier"] == tier]
        if tier_results:
            tier_correct = sum(1 for r in tier_results if r["tier_correct"])
            by_tier[tier] = {
                "count": len(tier_results),
                "correct": tier_correct,
                "accuracy": tier_correct / len(tier_results),
                "avg_latency_ms": sum(r["latency_ms"] for r in tier_results) / len(tier_results),
                "total_cost_usd": sum(r["cost_usd"] for r in tier_results),
                "total_savings_usd": sum(r["savings_usd"] for r in tier_results),
            }

    summary = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "seed": seed,
        "gateway_url": gateway_url,
        "dataset": str(dataset_path),
        "total_prompts": total,
        "errors": len(errors),
        "routing_accuracy": round(accuracy, 4),
        "total_cost_usd": round(total_cost, 6),
        "total_baseline_cost_usd": round(total_baseline, 6),
        "total_savings_usd": round(total_savings, 6),
        "savings_pct": round(savings_pct, 2),
        "avg_latency_ms": round(avg_latency, 1),
        "p99_latency_ms": round(p99_latency, 1),
        "by_tier": by_tier,
    }

    # ---------------------------------------------------------------------------
    # Console summary
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"  Routing accuracy : {accuracy:.1%}  ({correct}/{total} correct)")
    print(f"  Total cost       : ${total_cost:.6f}")
    print(f"  Baseline cost    : ${total_baseline:.6f}  (all {BASELINE_MODEL})")
    print(f"  Savings          : ${total_savings:.6f}  ({savings_pct:.1f}%)")
    print(f"  Avg latency      : {avg_latency:.1f} ms")
    print(f"  p99 latency      : {p99_latency:.1f} ms")
    print()
    print(f"  {'Tier':<10} {'N':>4}  {'Accuracy':>8}  {'Avg Latency':>11}  {'Savings':>10}")
    print(f"  {'-'*10} {'-'*4}  {'-'*8}  {'-'*11}  {'-'*10}")
    for tier, stats in by_tier.items():
        print(
            f"  {tier:<10} {stats['count']:>4}  "
            f"{stats['accuracy']:>7.1%}  "
            f"{stats['avg_latency_ms']:>9.1f}ms  "
            f"${stats['total_savings_usd']:>9.6f}"
        )
    print("=" * 70)

    # ---------------------------------------------------------------------------
    # Write output
    # ---------------------------------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = {"summary": summary, "results": results, "errors": errors}
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nFull results written to: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Model Router evaluation script")
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:8000",
        help="Gateway base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--dataset",
        default="experiments/datasets/prompts.json",
        help="Path to benchmark dataset JSON",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: experiments/results/results_<timestamp>.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--max-prompts",
        type=int,
        default=None,
        help="Limit to N prompts sampled from the dataset (default: all)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    output = args.output or (
        f"experiments/results/results_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    )

    run_eval(
        gateway_url=args.gateway_url,
        dataset_path=Path(args.dataset),
        output_path=Path(output),
        seed=args.seed,
        max_prompts=args.max_prompts,
    )
