"""
Classifier evaluation — tests classify() directly, no gateway or HTTP required.

Measures:
  - Exact 4-class accuracy  (simple / moderate / complex / sensitive)
  - Per-tier precision, recall, F1, support
  - Confusion matrix  (rows = actual, cols = predicted)
  - Inference latency (mean, p50, p95, p99)

Adding a new classifier (XGBoost, embedding, etc.):
  1. Write an async function: async def _my_clf(messages) -> str
  2. Add it to CLASSIFIERS at the bottom of this file
  3. Run:  python experiments/eval_classifier.py --all

Usage:
  python experiments/eval_classifier.py
  python experiments/eval_classifier.py --classifier heuristic
  python experiments/eval_classifier.py --all
  python experiments/eval_classifier.py --dataset experiments/datasets/prompts.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

# ── Make gateway importable when run from project root ────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from gateway.classifier.heuristic import classify as _heuristic_classify  # noqa: E402
from gateway.classifier.model import classify as _model_classify  # noqa: E402
from gateway.schemas import Message  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
TIERS = ["simple", "moderate", "complex", "sensitive"]

ClassifierFn = Callable[[list[Message]], Awaitable[str]]


# ── Classifier adapters ───────────────────────────────────────────────────────
# Each adapter wraps a classifier so it returns a plain tier string.
# Add new classifiers here as you build them.

async def _heuristic(messages: list[Message]) -> str:
    result = await _heuristic_classify(messages)
    return result.complexity


async def _learned(messages: list[Message]) -> str:
    result = await _model_classify(messages)
    return result.complexity

# ── Registry ──────────────────────────────────────────────────────────────────
CLASSIFIERS: dict[str, ClassifierFn] = {
    "heuristic": _heuristic,
    "learned":   _learned,
    # "embedding": _embedding,  # add when implemented
}


# ── Core evaluation ───────────────────────────────────────────────────────────

async def evaluate(
    classifier: ClassifierFn,
    prompts: list[dict],
) -> tuple[list[str], list[str], list[float]]:
    """Run classifier on every prompt.
    Returns (y_true, y_pred, latencies_ms).
    """
    y_true, y_pred, latencies = [], [], []
    for prompt in prompts:
        messages = [Message(role="user", content=prompt["text"])]
        t0 = time.perf_counter()
        predicted = await classifier(messages)
        latency_ms = (time.perf_counter() - t0) * 1_000
        y_true.append(prompt["tier"])
        y_pred.append(predicted)
        latencies.append(latency_ms)
    return y_true, y_pred, latencies


# ── Metrics ───────────────────────────────────────────────────────────────────

def confusion_matrix(y_true: list[str], y_pred: list[str]) -> list[list[int]]:
    n = len(TIERS)
    cm: list[list[int]] = [[0] * n for _ in range(n)]
    for t, p in zip(y_true, y_pred):
        if t in TIERS and p in TIERS:
            cm[TIERS.index(t)][TIERS.index(p)] += 1
    return cm


def per_tier_metrics(cm: list[list[int]]) -> dict[str, dict[str, float]]:
    n = len(TIERS)
    result: dict[str, dict[str, float]] = {}
    for i, tier in enumerate(TIERS):
        tp = cm[i][i]
        fp = sum(cm[j][i] for j in range(n)) - tp
        fn = sum(cm[i][j] for j in range(n)) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0 else 0.0
        )
        result[tier] = {
            "precision": round(precision, 4),
            "recall":    round(recall, 4),
            "f1":        round(f1, 4),
            "support":   sum(cm[i]),
        }
    return result


def macro_avg(per_tier: dict[str, dict[str, float]]) -> dict[str, float]:
    keys = ["precision", "recall", "f1"]
    return {k: round(sum(per_tier[t][k] for t in TIERS) / len(TIERS), 4) for k in keys}


def latency_stats(latencies: list[float]) -> dict[str, float]:
    s = sorted(latencies)
    n = len(s)
    return {
        "mean_ms": round(sum(s) / n, 4),
        "p50_ms":  round(s[max(0, int(n * 0.50) - 1)], 4),
        "p95_ms":  round(s[max(0, int(n * 0.95) - 1)], 4),
        "p99_ms":  round(s[max(0, int(n * 0.99) - 1)], 4),
    }


# ── Display ───────────────────────────────────────────────────────────────────
_GREEN = "\033[32m"
_RESET = "\033[0m"
_BOLD  = "\033[1m"


def _fmt_cell(val: int, is_diag: bool, width: int = 10) -> str:
    s = str(val).rjust(width)
    return f"{_GREEN}{s}{_RESET}" if is_diag else s


def print_confusion_matrix(cm: list[list[int]]) -> None:
    col_w = 10
    label_w = 12
    print(f"\n  {'':>{label_w}}" + "".join(f"{t:>{col_w}}" for t in TIERS))
    print(f"  {'':->{label_w}}" + "-" * (col_w * len(TIERS)))
    for i, tier in enumerate(TIERS):
        row = f"  {tier:<{label_w}}" + "".join(
            _fmt_cell(cm[i][j], i == j, col_w) for j in range(len(TIERS))
        )
        print(row)


def print_report(
    classifier_name: str,
    y_true: list[str],
    y_pred: list[str],
    latencies: list[float],
) -> dict:
    cm      = confusion_matrix(y_true, y_pred)
    pt      = per_tier_metrics(cm)
    macro   = macro_avg(pt)
    lat     = latency_stats(latencies)
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    accuracy = correct / len(y_true)

    print(f"\n{_BOLD}{'='*62}{_RESET}")
    print(f"  Classifier : {_BOLD}{classifier_name}{_RESET}")
    print(f"  Prompts    : {len(y_true)}")
    print(f"  Accuracy   : {_BOLD}{accuracy:.1%}{_RESET}  ({correct}/{len(y_true)} exact 4-class match)")
    print(f"{'='*62}")

    print("\n  Confusion Matrix  (rows = actual, cols = predicted)")
    print("  Diagonal (green) = correct predictions")
    print_confusion_matrix(cm)

    print(f"\n  {'Tier':<12} {'Precision':>10} {'Recall':>8} {'F1':>8} {'Support':>8}")
    print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
    for tier in TIERS:
        m = pt[tier]
        print(
            f"  {tier:<12} {m['precision']:>10.3f} {m['recall']:>8.3f}"
            f" {m['f1']:>8.3f} {m['support']:>8}"
        )
    print(
        f"  {'macro avg':<12} {macro['precision']:>10.3f}"
        f" {macro['recall']:>8.3f} {macro['f1']:>8.3f}"
    )

    print(
        f"\n  Latency  mean={lat['mean_ms']:.3f} ms  "
        f"p50={lat['p50_ms']:.3f} ms  "
        f"p95={lat['p95_ms']:.3f} ms  "
        f"p99={lat['p99_ms']:.3f} ms"
    )

    # Per-prompt breakdown for wrong predictions
    wrong = [
        {"true": t, "pred": p, "text": prompts_ref[i]["text"][:80]}
        for i, (t, p) in enumerate(zip(y_true, y_pred))
        if t != p
    ]
    if wrong:
        print(f"\n  Misclassified ({len(wrong)}):")
        for w in wrong:
            print(f"    true={w['true']:<10} pred={w['pred']:<10} '{w['text']}'")

    return {
        "classifier":       classifier_name,
        "n_prompts":        len(y_true),
        "accuracy":         round(accuracy, 4),
        "confusion_matrix": cm,
        "per_tier":         pt,
        "macro_avg":        macro,
        "latency":          lat,
        "misclassified":    wrong,
        "predictions": [
            {"true": t, "pred": p, "correct": t == p, "latency_ms": round(l, 4)}
            for t, p, l in zip(y_true, y_pred, latencies)
        ],
    }


def print_comparison_table(all_results: list[dict]) -> None:
    print(f"\n{_BOLD}{'='*62}")
    print("  CLASSIFIER COMPARISON")
    print(f"{'='*62}{_RESET}")
    print(f"  {'Classifier':<18} {'Accuracy':>10} {'Macro-F1':>10} {'mean (ms)':>10} {'p99 (ms)':>10}")
    print(f"  {'-'*18} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    best_acc = max(r["accuracy"] for r in all_results)
    for r in all_results:
        acc_str = f"{r['accuracy']:.1%}"
        if r["accuracy"] == best_acc:
            acc_str = f"{_GREEN}{acc_str}{_RESET}"
        print(
            f"  {r['classifier']:<18} {acc_str:>10} "
            f"{r['macro_avg']['f1']:>10.3f} "
            f"{r['latency']['mean_ms']:>10.3f} "
            f"{r['latency']['p99_ms']:>10.3f}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

# Module-level ref so print_report can annotate misclassifications with text
prompts_ref: list[dict] = []


async def main(args: argparse.Namespace) -> None:
    global prompts_ref

    dataset_path = Path(args.dataset)
    with open(dataset_path) as f:
        dataset = json.load(f)
    prompts_ref = dataset["prompts"]

    print(f"\n{_BOLD}Classifier Evaluation{_RESET}")
    print(f"  Dataset : {dataset_path}  ({len(prompts_ref)} prompts)")
    print(f"  Tiers   : {TIERS}")

    names = list(CLASSIFIERS.keys()) if args.all else [args.classifier]
    for name in names:
        if name not in CLASSIFIERS:
            print(f"\nUnknown classifier '{name}'. Available: {list(CLASSIFIERS.keys())}")
            sys.exit(1)

    all_results = []
    for name in names:
        y_true, y_pred, latencies = await evaluate(CLASSIFIERS[name], prompts_ref)
        result = print_report(name, y_true, y_pred, latencies)
        all_results.append(result)

    if len(all_results) > 1:
        print_comparison_table(all_results)

    # Save JSON
    output_path = Path(
        args.output
        or f"experiments/results/classifier_eval_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id":   datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "dataset":  str(dataset_path),
        "results":  all_results,
    }
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n  Full results → {output_path}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate classifiers directly — no gateway required."
    )
    parser.add_argument(
        "--classifier",
        default="heuristic",
        help="Classifier to evaluate (default: heuristic). Available: " + ", ".join(CLASSIFIERS.keys()),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Evaluate all registered classifiers and show comparison table",
    )
    parser.add_argument(
        "--dataset",
        default="experiments/datasets/prompts.json",
        help="Path to labeled prompt dataset (default: experiments/datasets/prompts.json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: experiments/results/classifier_eval_<timestamp>.json)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
