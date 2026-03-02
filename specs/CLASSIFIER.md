# Classifier Specification

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Purpose

The classifier assigns every inbound request a **complexity tier** before routing. It is the component most responsible for routing accuracy and the component most likely to be replaced or augmented over time.

---

## 2. Interface Contract

```python
async def classify(messages: list[Message]) -> Classification
```

**Input:** The full `messages` array from the request body.
**Output:** A `Classification` dataclass.

```python
@dataclass
class Classification:
    complexity: Literal["simple", "moderate", "complex", "sensitive"]
    token_count: int       # estimated input token count
    signals: list[str]    # human-readable list of signals that fired
```

This interface is stable. Any classifier implementation (heuristic, model-based, hybrid) must satisfy it. The gateway layer never calls internal classifier methods directly.

---

## 3. Complexity Tiers

| Tier | Meaning | Default model tier |
|------|---------|-------------------|
| `simple` | Short, factual, single-step | Cheap (Haiku, GPT-4o-mini) |
| `moderate` | Medium length, light reasoning | Mid (configurable) |
| `complex` | Long context, multi-step reasoning, code | Premium (Sonnet, GPT-4o) |
| `sensitive` | PII, legal, security, compliance keywords | Safe (Opus, or on-prem) |

`sensitive` is always elevated regardless of token count or other signals. It is a **hard override**.

---

## 4. Heuristic Classifier Rules (current implementation)

Rules are evaluated in priority order. First matching rule wins.

### Rule 1: Sensitive keyword match (highest priority)
If any word in the full message text matches the sensitive pattern, return `sensitive` immediately.

Sensitive keywords (case-insensitive, word-boundary matched):
```
ssn, social security, passport, credit card, hipaa, phi, pii,
attorney, legal advice, lawsuit, privileged, confidential,
password, secret key, api key
```

**Signal emitted:** `sensitive_keyword`

### Rule 2: Token count
Estimate: `token_count = max(1, len(full_text) // 4)`

| Token count | Tier |
|-------------|------|
| ≤ 300 | `simple` |
| 301 – 1499 | `moderate` |
| ≥ 1500 | `complex` |

**Signals emitted:** `long_context` (if ≥ 1500), none otherwise

### Rule 3: Keyword overrides (applied after token count)
Complex keywords can **upgrade** a `simple` or `moderate` result to `complex`.
Simple keywords can **downgrade** a `moderate` result to `simple` (but cannot downgrade `complex`).

**Complex keywords (upgrades to `complex`):**
```
implement, refactor, architect, design pattern, algorithm, optimize,
debug, explain step, compare, analyze, summarize <text> document,
write function, generate code
```
**Signal emitted:** `complex_keyword`

**Simple keywords (downgrades moderate → simple):**
```
what is, define, who is, when did, yes or no, translate, spell check,
convert, format
```
**Signal emitted:** `simple_keyword`

### Evaluation order summary
```
1. Sensitive keyword match? → sensitive (done)
2. Compute token count → base tier
3. Complex keyword match? → upgrade to complex
4. Simple keyword match AND base tier != complex? → downgrade to simple
5. Return result
```

---

## 5. Text Extraction

Before applying rules, all messages are concatenated into a single text string:
- For `string` content: use directly.
- For `list` content (multi-modal): extract `text` from each dict part; skip non-text parts.
- Role is ignored for classification (system prompts contribute to token count and keyword matching).

---

## 6. Token Count Estimation

The heuristic uses `len(text) // 4` as a character-per-token approximation.

This is intentionally imprecise. The purpose is to bucket requests by size, not to predict exact token usage. Actual token counts are retrieved from the provider's response and stored in `usage_events`.

A future implementation may use `tiktoken` for more accurate pre-request estimation.

---

## 7. Accuracy Targets

| Tier | Target precision | Notes |
|------|-----------------|-------|
| `sensitive` | 100% (no false negatives allowed) | Use broad keyword list; false positives are acceptable |
| `complex` | ≥ 75% | Most impactful tier for cost savings |
| `simple` | ≥ 70% | Under-routing (calling simple things complex) is safe but wasteful |
| `moderate` | ≥ 60% | Moderate is a middle tier; routing errors here have low cost impact |

Accuracy is measured by comparing classifier output against human-labeled samples.

---

## 8. Failure Behavior

If the classifier raises an exception (e.g. unexpected message format), the gateway **falls back to `complex`** with `signal = ["classifier_error"]`. This ensures no request is incorrectly routed to a cheap model due to a classification bug.

Errors are logged at `ERROR` level with the full exception.

---

## 9. Extension Points

### Replacing with a model-based classifier
Create `gateway/classifier/model_based.py` with:
```python
async def classify(messages: list[Message]) -> Classification: ...
```
Select via a `CLASSIFIER_BACKEND` env var: `heuristic` (default) | `model`.

### Shadow scoring
Run both classifiers and log disagreements to a `classifier_disagreements` table without changing routing behavior. Use this to build an accuracy eval dataset.

### Per-tenant keyword overrides
Future: allow tenants to add domain-specific keyword lists in their policy YAML:
```yaml
classifier:
  sensitive_keywords: ["merger", "acquisition", "NDA"]
  simple_keywords: ["pricing page", "FAQ"]
```
