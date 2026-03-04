"""
LLM-as-labeler pipeline for training dataset generation.

Uses the Anthropic Python SDK directly (requires ANTHROPIC_API_KEY).
Design decisions: see experiments/DATASET_DECISIONS.md

Phases:
  generate  — produce N raw prompts via Claude Haiku, no tier labels
  label     — send each prompt through Claude Opus twice (T=0 and T=0.3);
               keep only where both runs agree AND min confidence >= 3
  validate  — run 60 anchor (human-labeled) prompts through labeler;
               abort pipeline if labeler agreement < ANCHOR_THRESHOLD (85%)
  merge     — combine filtered labeled prompts into a JSON compatible with
               experiments/datasets/prompts.json

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...

  # End-to-end:
  python experiments/generate_dataset.py run-all --n 1200

  # Individual phases:
  python experiments/generate_dataset.py generate \\
      --n 1200 --out experiments/datasets/raw_generated.jsonl

  python experiments/generate_dataset.py label \\
      --in experiments/datasets/raw_generated.jsonl \\
      --out experiments/datasets/labeled.jsonl

  python experiments/generate_dataset.py validate \\
      --anchor experiments/datasets/prompts.json \\
      --labeled experiments/datasets/labeled.jsonl

  python experiments/generate_dataset.py merge \\
      --labeled experiments/datasets/labeled.jsonl \\
      --out experiments/datasets/train.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("generate_dataset")

# ── Cost tracking ─────────────────────────────────────────────────────────────
# Rates in USD per 1K tokens (input, output) — keep in sync with gateway/telemetry/collector.py
_COST_TABLE: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    "claude-sonnet-4-6":         (0.003,   0.015),
    "claude-opus-4-6":           (0.015,   0.075),
}


class BudgetExceededError(RuntimeError):
    pass


class CostTracker:
    """Thread-safe (asyncio-safe) running cost accumulator with optional hard limit."""

    def __init__(self, max_cost: float = float("inf")) -> None:
        self.max_cost = max_cost
        self._total   = 0.0
        self._lock    = asyncio.Lock()

    async def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        rates = _COST_TABLE.get(model, (0.0, 0.0))
        cost  = (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000
        async with self._lock:
            self._total += cost
            total = self._total  # snapshot inside the lock
        log.debug("Cost so far: $%.4f (this call: $%.4f)", total, cost)
        if total > self.max_cost:
            raise BudgetExceededError(
                f"Budget exceeded: ${total:.4f} spent > ${self.max_cost:.2f} limit. "
                "Increase --max-cost or check for unexpected usage."
            )

    @property
    def total(self) -> float:
        return self._total

# ── Constants ─────────────────────────────────────────────────────────────────
TIERS = ["simple", "moderate", "complex", "sensitive"]

ANCHOR_THRESHOLD = 0.85   # min labeler–human agreement to proceed
MIN_CONFIDENCE   = 3      # min confidence score (1–5) for a label to be kept
CONCURRENCY      = 2      # max concurrent Anthropic API calls (keep low to respect rate limits)

GEN_MODEL   = "claude-haiku-4-5-20251001"
LABEL_MODEL = "claude-sonnet-4-6"   # Sonnet: 5x cheaper than Opus, much higher rate limits


# ── Domain configuration ───────────────────────────────────────────────────────
DOMAINS: list[dict] = [
    {
        "name": "general_knowledge",
        "description": "Factual questions, definitions, history, science, geography, culture.",
        "examples": [
            "simple: 'What is the capital of Brazil?'",
            "moderate: 'Explain the causes of the French Revolution.'",
            "complex: 'Compare the Roman and Han empires across military, economic, and cultural dimensions.'",
        ],
    },
    {
        "name": "software_engineering",
        "description": "Programming concepts, algorithms, data structures, system design, debugging.",
        "examples": [
            "simple: 'What does SOLID stand for?'",
            "moderate: 'Explain how a hash map handles collisions.'",
            "complex: 'Implement a thread-safe bounded queue in Python with producer-consumer semantics.'",
        ],
    },
    {
        "name": "data_science_ml",
        "description": "Statistics, machine learning, deep learning, data wrangling, model evaluation.",
        "examples": [
            "simple: 'What is the difference between supervised and unsupervised learning?'",
            "moderate: 'How does gradient boosting differ from random forests?'",
            "complex: 'Design an experiment to detect data leakage in a binary classification pipeline.'",
        ],
    },
    {
        "name": "devops_cloud",
        "description": "Infrastructure, CI/CD, containers, Kubernetes, observability, IaC.",
        "examples": [
            "simple: 'What is a Docker image vs a container?'",
            "moderate: 'How does a Kubernetes rolling update work?'",
            "complex: 'Architect a zero-downtime blue-green deployment pipeline for a multi-region service.'",
        ],
    },
    {
        "name": "web_development",
        "description": "Frontend, backend, APIs, authentication, browser internals, performance.",
        "examples": [
            "simple: 'What is the difference between GET and POST?'",
            "moderate: 'Explain how the browser critical rendering path works.'",
            "complex: 'Design a WebSocket-based collaborative whiteboard that handles 10k concurrent users.'",
        ],
    },
    {
        "name": "databases",
        "description": "SQL, NoSQL, transactions, indexing, query optimization, schema design.",
        "examples": [
            "simple: 'What is a primary key?'",
            "moderate: 'Explain the difference between OLTP and OLAP databases.'",
            "complex: 'Optimize a slow join query across three 100M-row tables and justify your index strategy.'",
        ],
    },
    {
        "name": "security_compliance",
        "description": "Auth, encryption, PII, secrets management, GDPR/HIPAA/PCI-DSS, incident response.",
        "examples": [
            "sensitive: 'Our production DB password is leaking in logs. How do we remediate?'",
            "sensitive: 'What PII fields are covered under GDPR Article 4?'",
            "complex: 'Design a secrets rotation strategy for a microservices fleet using Vault.'",
        ],
    },
    {
        "name": "business_legal",
        "description": "Business analysis, contracts, compliance, financial modeling, legal concepts.",
        "examples": [
            "simple: 'What is a KPI?'",
            "moderate: 'Explain the key clauses in a standard SaaS subscription agreement.'",
            "sensitive: 'Review this NDA and identify the highest-risk provisions for our company.'",
        ],
    },
]

TIER_DEFINITIONS = """\
Tier definitions:
  simple    — factual recall, single-concept lookup, translation, arithmetic.
              A one-line answer fully satisfies the request.
              Examples: "What is the capital of France?", "Define API."

  moderate  — requires explanation, comparison of 2-3 concepts, or multi-step
              reasoning that fits in ~1-3 paragraphs. No implementation needed.
              Examples: "Explain the CAP theorem.", "Compare TCP vs UDP."

  complex   — requires code generation, algorithm implementation, architecture
              design, deep multi-step analysis, or optimization of a system.
              A thorough answer requires significant length and expertise.
              Examples: "Implement a thread-safe LRU cache with O(1) ops.",
                        "Design a distributed rate limiter for 1M req/s."

  sensitive — contains PII (SSN, passport, credit card), legal privilege,
              confidential business information, credentials/secrets, or
              regulated data (HIPAA PHI, PCI-DSS cardholder data).
              Routing to a safer/audited model is required regardless of length.
              Examples: "My SSN is 123-45-6789. Help me fill this form.",
                        "Review this privileged attorney-client communication."

Label the MINIMUM sufficient tier: the cheapest model that would produce a
response of acceptable quality. Do NOT over-label moderate prompts as complex.\
"""

GENERATION_SYSTEM = (
    "You are a dataset curator for an LLM routing research project. "
    "Generate realistic, diverse user prompts that real developers, analysts, "
    "or business users might send to an AI assistant. "
    "Output ONLY a JSON array of strings — no tier labels, no explanations."
)

GENERATION_USER_TEMPLATE = """\
Domain: {domain_name}
Description: {domain_desc}
Style examples (for reference only — do NOT include tier labels in output):
{examples}

Generate {n} prompts for this domain. Rules:
- {tier_instruction}
- Vary prompt style: questions ("What is..."), imperatives ("Implement...", "Design..."),
  declaratives ("I need to understand..."), multi-sentence context-setters
- Each prompt should be self-contained and unambiguous
- Avoid repeating the same concept more than once
- Respond with ONLY a JSON array of strings, no other text

Output format:
["prompt 1", "prompt 2", ..., "prompt {n}"]
"""

# Per-tier generation instructions injected into the template
_TIER_INSTRUCTIONS: dict[str, str] = {
    "simple": (
        "Generate ONLY simple prompts — factual recall, single-concept definitions, "
        "unit conversions, yes/no questions. A one-line answer must fully satisfy each prompt. "
        "Do NOT generate moderate, complex, or sensitive prompts."
    ),
    "moderate": (
        "Generate ONLY moderate prompts — explanations, comparisons of 2-3 concepts, "
        "or multi-step reasoning fitting ~1-3 paragraphs. No code implementation needed. "
        "Do NOT generate simple, complex, or sensitive prompts."
    ),
    "complex": (
        "Generate ONLY complex prompts — code implementation, algorithm design, architecture, "
        "deep multi-step analysis, or system optimization. "
        "Do NOT generate simple, moderate, or sensitive prompts."
    ),
    "sensitive": (
        "Generate ONLY sensitive prompts — every prompt MUST contain at least one of: "
        "real-looking PII (SSN, passport number, credit card), credentials/secrets/API keys, "
        "legal privilege or attorney-client communication, or regulated data (HIPAA PHI, PCI-DSS). "
        "Do NOT generate simple, moderate, or complex prompts."
    ),
}

_DEFAULT_TIER_INSTRUCTION = (
    "Mix all four tiers naturally "
    "(rough target: 20% simple, 40% moderate, 30% complex, 10% sensitive)"
)

LABELING_SYSTEM = (
    "You are a labeling expert for an LLM routing system. "
    "Your task is to assign each prompt its minimum sufficient routing tier. "
    "Always reason step-by-step before giving your final answer."
)

LABELING_USER_TEMPLATE = """\
{tier_definitions}

Few-shot examples (verified by human review):
  simple:    "What does HTTP stand for?"
  moderate:  "Explain how JWT authentication works in a web application."
  complex:   "Implement a thread-safe LRU cache in Python with O(1) get and put. Include tests."
  sensitive: "My SSN is 123-45-6789. Can you help me fill out this form?"

Now label this prompt:
<prompt>
{prompt_text}
</prompt>

Think step by step:
1. Does this prompt contain PII, credentials, legal privilege, or regulated data? → if yes: sensitive
2. Does it require code implementation, architecture design, or deep multi-step analysis? → if yes: complex
3. Does it require a multi-paragraph explanation or comparison of concepts? → if yes: moderate
4. Otherwise → simple

Then output ONLY valid JSON (no markdown fences, no explanation after the JSON):
{{"tier": "<simple|moderate|complex|sensitive>", "confidence": <1-5>, "reasoning": "<one sentence>"}}
"""


# ── Anthropic client ───────────────────────────────────────────────────────────

def _make_client() -> anthropic.AsyncAnthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.error("ANTHROPIC_API_KEY is not set. Get a key at https://console.anthropic.com")
        sys.exit(1)
    return anthropic.AsyncAnthropic(api_key=key)


# ── Phase 1: Generate ──────────────────────────────────────────────────────────

async def _generate_domain(
    client: anthropic.AsyncAnthropic,
    domain: dict,
    n: int,
    sem: asyncio.Semaphore,
    tracker: CostTracker,
    tiers: list[str] | None = None,
) -> list[str]:
    # Filter examples to only show the requested tiers (keeps prompt focused)
    if tiers:
        examples = [e for e in domain["examples"] if any(e.startswith(t) for t in tiers)]
        if not examples:
            examples = domain["examples"]  # fallback: show all if domain has no matching examples
        if len(tiers) == 1:
            tier_instruction = _TIER_INSTRUCTIONS[tiers[0]]
        else:
            tier_instruction = (
                f"Generate ONLY {' or '.join(tiers)} prompts, roughly equal mix. "
                + " ".join(_TIER_INSTRUCTIONS[t].split("—")[1].strip().split(".")[0] + "."
                           for t in tiers if t in _TIER_INSTRUCTIONS)
            )
    else:
        examples = domain["examples"]
        tier_instruction = _DEFAULT_TIER_INSTRUCTION

    user_msg = GENERATION_USER_TEMPLATE.format(
        domain_name=domain["name"],
        domain_desc=domain["description"],
        examples="\n".join(f"  {e}" for e in examples),
        n=n,
        tier_instruction=tier_instruction,
    )
    async with sem:
        log.info("Generating %d prompts for domain=%s …", n, domain["name"])
        resp = await client.messages.create(
            model=GEN_MODEL,
            max_tokens=4096,
            system=GENERATION_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            temperature=1.0,
        )
    await tracker.record(GEN_MODEL, resp.usage.input_tokens, resp.usage.output_tokens)
    raw = resp.content[0].text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip().rstrip("`").strip()

    try:
        prompts = json.loads(raw)
        if not isinstance(prompts, list):
            raise ValueError("expected JSON array")
        return [str(p).strip() for p in prompts if str(p).strip()]
    except Exception as exc:
        log.warning("Domain %s: JSON parse failed (%s); trying line-split", domain["name"], exc)
        lines = [
            line.lstrip("0123456789. \"-'").rstrip("\"',")
            for line in raw.splitlines()
            if line.strip()
        ]
        return [l for l in lines if len(l) > 10][:n]


async def cmd_generate(args: argparse.Namespace, tracker: CostTracker | None = None) -> None:
    client   = _make_client()
    tracker  = tracker or CostTracker(getattr(args, "max_cost", float("inf")))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tiers = getattr(args, "tiers", None) or None  # None means "all tiers"
    if tiers:
        invalid = [t for t in tiers if t not in TIERS]
        if invalid:
            log.error("Unknown tiers: %s. Valid: %s", invalid, TIERS)
            sys.exit(1)
        log.info("Tier-targeted generation: %s", tiers)

    n_per_domain = max(1, args.n // len(DOMAINS))
    remainder    = args.n - n_per_domain * len(DOMAINS)

    sem   = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        _generate_domain(client, domain, n_per_domain + (1 if i < remainder else 0), sem, tracker, tiers)
        for i, domain in enumerate(DOMAINS)
    ]
    try:
        results = await asyncio.gather(*tasks)
    except BudgetExceededError as e:
        log.error("ABORTED: %s", e)
        sys.exit(1)

    # Append when --tiers is set (top-up mode); overwrite otherwise (fresh generation)
    file_mode = "a" if (tiers or getattr(args, "append", False)) else "w"
    total = 0
    with out_path.open(file_mode) as f:
        for domain, prompts in zip(DOMAINS, results):
            for p in prompts:
                f.write(json.dumps({"domain": domain["name"], "text": p}) + "\n")
                total += 1

    action = "Appended" if file_mode == "a" else "Generated"
    log.info("%s %d raw prompts → %s  (cost so far: $%.4f)", action, total, out_path, tracker.total)


# ── Phase 2: Label ─────────────────────────────────────────────────────────────

async def _label_once(
    client: anthropic.AsyncAnthropic,
    text: str,
    temperature: float,
    sem: asyncio.Semaphore,
    tracker: CostTracker,
) -> dict[str, Any] | None:
    user_msg = LABELING_USER_TEMPLATE.format(
        tier_definitions=TIER_DEFINITIONS,
        prompt_text=text,
    )
    async with sem:
        try:
            resp = await client.messages.create(
                model=LABEL_MODEL,
                max_tokens=512,
                system=LABELING_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
                temperature=temperature,
            )
        except BudgetExceededError:
            raise
        except Exception as exc:
            log.warning("API error during labeling: %s", exc)
            return None
    await tracker.record(LABEL_MODEL, resp.usage.input_tokens, resp.usage.output_tokens)

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip().rstrip("`").strip()

    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        log.warning("No JSON in label response for '%.60s'", text)
        return None

    try:
        result = json.loads(raw[start:end])
        if result.get("tier") not in TIERS:
            raise ValueError(f"unknown tier: {result.get('tier')}")
        result["confidence"] = int(result.get("confidence", 0))
        return result
    except Exception as exc:
        log.warning("Label parse failed for '%.60s': %s", text, exc)
        return None


async def _label_prompt(
    client: anthropic.AsyncAnthropic,
    item: dict,
    sem: asyncio.Semaphore,
    tracker: CostTracker,
) -> dict[str, Any] | None:
    """Label twice (T=0 and T=0.3); keep only where both agree and min confidence >= MIN_CONFIDENCE."""
    text = item["text"]
    label_a, label_b = await asyncio.gather(
        _label_once(client, text, 0.0, sem, tracker),
        _label_once(client, text, 0.3, sem, tracker),
    )

    if label_a is None or label_b is None:
        return None

    if label_a["tier"] != label_b["tier"]:
        log.debug("Disagreement on '%.60s': %s vs %s", text, label_a["tier"], label_b["tier"])
        return None

    conf_min = min(label_a["confidence"], label_b["confidence"])
    if conf_min < MIN_CONFIDENCE:
        log.debug("Low confidence on '%.60s': %d", text, conf_min)
        return None

    return {
        **item,
        "tier":       label_a["tier"],
        "confidence": conf_min,
        "reasoning":  label_a.get("reasoning", ""),
        "label_a":    label_a,
        "label_b":    label_b,
    }


async def cmd_label(args: argparse.Namespace, tracker: CostTracker | None = None) -> None:
    client   = _make_client()
    tracker  = tracker or CostTracker(getattr(args, "max_cost", float("inf")))
    in_path  = Path(getattr(args, "in"))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Resume: skip prompts already in the output file ──────────────────────
    already_labeled: set[str] = set()
    if out_path.exists() and not getattr(args, "no_resume", False):
        with out_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    already_labeled.add(json.loads(line)["text"])
        if already_labeled:
            log.info("Resuming — skipping %d already-labeled prompts", len(already_labeled))

    items: list[dict] = []
    with in_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                item = json.loads(line)
                if item["text"] not in already_labeled:
                    items.append(item)

    if not items:
        log.info("Nothing to label — all prompts already in %s", out_path)
        return

    log.info("Labeling %d prompts with %s (CONCURRENCY=%d) …", len(items), LABEL_MODEL, CONCURRENCY)

    sem        = asyncio.Semaphore(CONCURRENCY)
    tier_counts: dict[str, int] = {}
    kept = dropped = 0
    budget_hit = False

    # Write each result immediately so Ctrl+C or budget abort never loses progress
    with out_path.open("a") as f:
        tasks = {asyncio.ensure_future(_label_prompt(client, item, sem, tracker)): item
                 for item in items}
        for coro in asyncio.as_completed(tasks.keys()):
            try:
                result = await coro
            except BudgetExceededError as e:
                log.error("ABORTED: %s", e)
                budget_hit = True
                # cancel remaining tasks
                for t in tasks.keys():
                    t.cancel()
                break
            if result is not None:
                tier_counts[result["tier"]] = tier_counts.get(result["tier"], 0) + 1
                f.write(json.dumps(result) + "\n")
                f.flush()   # ensure it hits disk immediately
                kept += 1
            else:
                dropped += 1

    log.info("Kept %d / %d new (dropped %d: disagreements or low-confidence)", kept, len(items), dropped)
    log.info("Tier distribution (this run): %s", tier_counts)
    log.info("Labeled dataset → %s  (cost so far: $%.4f)", out_path, tracker.total)
    if budget_hit:
        log.info("Re-run the same command to resume from where it stopped.")
        sys.exit(1)


# ── Phase 3: Validate ──────────────────────────────────────────────────────────

async def cmd_validate(args: argparse.Namespace, tracker: CostTracker | None = None) -> None:
    client  = _make_client()
    tracker = tracker or CostTracker(getattr(args, "max_cost", float("inf")))

    with open(args.anchor) as f:
        anchor_dataset = json.load(f)
    anchor_prompts = anchor_dataset["prompts"]

    log.info("Validating labeler against %d anchor prompts …", len(anchor_prompts))

    sem     = asyncio.Semaphore(CONCURRENCY)
    items   = [{"domain": "anchor", "text": p["text"], "human_tier": p["tier"]} for p in anchor_prompts]
    try:
        results = await asyncio.gather(*[_label_prompt(client, item, sem, tracker) for item in items])
    except BudgetExceededError as e:
        log.error("ABORTED during validation: %s", e)
        sys.exit(1)

    agreed = 0
    total  = 0
    tier_agreement: dict[str, list[bool]] = {t: [] for t in TIERS}

    for item, result in zip(items, results):
        human = item["human_tier"]
        if result is None:
            log.warning("No label produced for anchor '%.60s'", item["text"])
            tier_agreement[human].append(False)
        else:
            match = result["tier"] == human
            tier_agreement[human].append(match)
            if match:
                agreed += 1
        total += 1

    overall = agreed / total if total else 0.0
    log.info("Anchor agreement: %.1f%% (%d/%d)", overall * 100, agreed, total)
    for tier in TIERS:
        vals = tier_agreement[tier]
        rate = sum(vals) / len(vals) if vals else 0.0
        log.info("  %s: %.1f%% (%d/%d)", tier, rate * 100, sum(vals), len(vals))

    if overall < ANCHOR_THRESHOLD:
        log.error(
            "Agreement %.1f%% < threshold %.1f%% — review LABELING_USER_TEMPLATE "
            "or tier definitions before proceeding.",
            overall * 100, ANCHOR_THRESHOLD * 100,
        )
        sys.exit(1)

    log.info("Validation passed.")


# ── Phase 4: Merge ─────────────────────────────────────────────────────────────

async def cmd_merge(args: argparse.Namespace) -> None:
    """
    Merge labeled JSONL into train.json (same schema as prompts.json).
    The 60 human-labeled anchor prompts are NOT included (held-out test set).
    """
    labeled_path = Path(args.labeled)
    out_path     = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    labeled: list[dict] = []
    with labeled_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                labeled.append(json.loads(line))

    tier_counters: dict[str, int] = {t: 0 for t in TIERS}
    prefix = {"simple": "gs", "moderate": "gm", "complex": "gc", "sensitive": "gx"}
    prompts = []
    for item in labeled:
        tier = item["tier"]
        tier_counters[tier] += 1
        pid = f"{prefix.get(tier, 'g?')}{tier_counters[tier]:04d}"
        prompts.append({
            "id":         pid,
            "tier":       tier,
            "text":       item["text"],
            "domain":     item.get("domain", "unknown"),
            "confidence": item.get("confidence", 0),
            "source":     "llm_labeled",
        })

    payload = {
        "version":      "1.1",
        "description":  (
            "LLM-labeled training dataset generated by experiments/generate_dataset.py. "
            "Human-labeled test set is in experiments/datasets/prompts.json (held out)."
        ),
        "tiers":        TIERS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_prompts":    len(prompts),
        "tier_counts":  tier_counters,
        "prompts":      prompts,
    }

    with out_path.open("w") as f:
        json.dump(payload, f, indent=2)

    log.info("Merged %d labeled prompts → %s", len(prompts), out_path)
    log.info("Tier counts: %s", tier_counters)


# ── run-all ────────────────────────────────────────────────────────────────────

async def cmd_run_all(args: argparse.Namespace) -> None:
    base    = Path("experiments/datasets")
    raw     = base / "raw_generated.jsonl"
    labeled = base / "labeled.jsonl"
    anchor  = Path("experiments/datasets/prompts.json")
    train   = base / "train.json"

    # Single tracker shared across all phases so --max-cost is a total cap
    tracker = CostTracker(getattr(args, "max_cost", float("inf")))
    if tracker.max_cost < float("inf"):
        log.info("Budget cap: $%.2f", tracker.max_cost)

    log.info("=== Phase 1: Generate ===")
    await cmd_generate(argparse.Namespace(n=args.n, out=str(raw)), tracker=tracker)

    log.info("=== Phase 2: Label ===")
    label_args = argparse.Namespace(out=str(labeled))
    setattr(label_args, "in", str(raw))
    await cmd_label(label_args, tracker=tracker)

    log.info("=== Phase 3: Validate ===")
    await cmd_validate(argparse.Namespace(anchor=str(anchor), labeled=str(labeled)), tracker=tracker)

    log.info("=== Phase 4: Merge ===")
    await cmd_merge(argparse.Namespace(labeled=str(labeled), anchor=str(anchor), out=str(train)))

    log.info("=== Done. Training set → %s  (total cost: $%.4f) ===", train, tracker.total)


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LLM-as-labeler dataset pipeline (requires ANTHROPIC_API_KEY)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="phase", required=True)

    p_gen = sub.add_parser("generate", help="Generate raw unlabeled prompts via Claude Haiku")
    p_gen.add_argument("--n",        type=int,   default=1200)
    p_gen.add_argument("--out",      default="experiments/datasets/raw_generated.jsonl")
    p_gen.add_argument("--max-cost", type=float, default=float("inf"),
                       metavar="USD", help="Abort if spend exceeds this amount in USD (e.g. 1.0)")
    p_gen.add_argument("--tiers",    nargs="+",  choices=TIERS, metavar="TIER",
                       help="Generate only these tiers, e.g. --tiers simple sensitive. "
                            "Automatically appends to existing output file.")
    p_gen.add_argument("--append",   action="store_true",
                       help="Append to existing output file instead of overwriting")

    p_lab = sub.add_parser("label", help="Label raw prompts with Claude Sonnet (dual-pass)")
    p_lab.add_argument("--in",        dest="in_file", required=True, metavar="IN")
    p_lab.add_argument("--out",       required=True)
    p_lab.add_argument("--max-cost",  type=float, default=float("inf"),
                       metavar="USD", help="Abort if spend exceeds this amount in USD (e.g. 5.0)")
    p_lab.add_argument("--no-resume", action="store_true",
                       help="Ignore existing output file and relabel everything from scratch")

    p_val = sub.add_parser("validate", help="Anchor-check labeler against human labels")
    p_val.add_argument("--anchor",   default="experiments/datasets/prompts.json")
    p_val.add_argument("--labeled",  required=True)
    p_val.add_argument("--max-cost", type=float, default=float("inf"), metavar="USD")

    p_mer = sub.add_parser("merge", help="Merge labeled JSONL into train.json")
    p_mer.add_argument("--labeled", required=True)
    p_mer.add_argument("--anchor",  default="experiments/datasets/prompts.json")
    p_mer.add_argument("--out",     default="experiments/datasets/train.json")

    p_all = sub.add_parser("run-all", help="Run all phases end-to-end")
    p_all.add_argument("--n",        type=int,   default=1200)
    p_all.add_argument("--max-cost", type=float, default=float("inf"),
                       metavar="USD", help="Total budget cap across all phases (e.g. 10.0)")

    return parser


async def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    if hasattr(args, "in_file"):
        setattr(args, "in", args.in_file)

    dispatch = {
        "generate": cmd_generate,
        "label":    cmd_label,
        "validate": cmd_validate,
        "merge":    cmd_merge,
        "run-all":  cmd_run_all,
    }
    await dispatch[args.phase](args)


if __name__ == "__main__":
    asyncio.run(main())
