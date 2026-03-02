# ADR-002: Ship Heuristic Classifier Before Model-Based Classifier

**Status:** Accepted
**Date:** 2026-03-01
**Deciders:** Initial team

---

## Context

The classifier is the component that scores each request (simple / moderate / complex / sensitive). There are two main approaches:

**Option A — Heuristic classifier:**
Token count thresholds + regex keyword matching. Runs in-process with no network call. Latency: < 1 ms.

**Option B — Model-based classifier:**
Call a small, cheap model (Haiku, GPT-4o-mini, or a local Phi-3-mini) with a classification prompt. Latency: 300–1000 ms on API, 50–200 ms on local hardware.

**Option C — Hybrid:** Heuristic fast-path with model fallback for ambiguous cases.

The core tension: a model-based classifier is more accurate (especially for domain-specific signals) but adds latency and cost to every request — which partially undermines the cost-saving purpose of the router.

---

## Decision

**Ship Option A (heuristics) for MVP. Design the interface to make Option B or C a drop-in swap.**

Specifically:
- The `classify()` function has a stable async interface: `async def classify(messages: list[Message]) -> Classification`.
- The heuristic implementation lives in `classifier/heuristic.py`.
- A future model-based implementation would live in `classifier/model_based.py` and be selected via config.
- The `Classification` dataclass includes a `signals: list[str]` field that both implementations populate, enabling debugging and accuracy measurement regardless of which classifier is active.

The threshold for switching: when heuristic accuracy (measured via manual eval or shadow scoring) drops below 70% on real traffic, introduce the model-based or hybrid path.

---

## Consequences

**Positive:**
- Zero added latency or cost for classification in MVP.
- No chicken-and-egg problem: we can ship and gather real traffic data before deciding how to improve the classifier.
- The heuristic is fully transparent and auditable — easy to explain to customers why a specific request was routed a certain way.
- Local model path (Phi-3-mini, Llama 3.2 3B) eliminates classifier inference cost entirely once fine-tuned.

**Negative:**
- Heuristic accuracy will be lower than a model-based approach for domain-specific content.
- Keyword lists and token thresholds require manual tuning per domain.
- "Moderate" tier may be under-utilized since the heuristic boundaries are coarse.

**Future work:**
- Add a shadow-scoring mode: run both classifiers and log disagreements without changing routing behavior.
- Build an accuracy eval dataset from labeled real traffic.
- Fine-tune a local model on that dataset when disagreement rate exceeds the threshold.
