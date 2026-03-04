# Dataset Generation & Labeling — Design Decisions

**File**: `experiments/generate_dataset.py`
**Decision date**: 2026-03-03
**Status**: In use

---

## 1. Why LLM-as-a-Labeler?

The human-labeled benchmark (`experiments/datasets/prompts.json`) has 60 prompts
(15 per tier). That is enough for evaluation but far too small to train a learned
classifier reliably — even with cross-validation, variance is high.

Goal: expand to **~1,000 filtered training records** while keeping the 60 prompts
as a held-out test set that is never touched during training.

LLM-as-labeler was chosen over:
- **Crowd-sourcing**: too slow and expensive for a research prototype.
- **Manual extension**: infeasible to produce 1,000+ records by hand.
- **Generate-by-tier-and-trust**: see decision 3 below.

---

## 2. Generate-Then-Label (not Generate-by-Tier)

**Decision**: Generate prompts first without any tier information, then label them
in a separate pass.

**Why not generate-by-tier?**
When you tell a model "generate 150 complex prompts", it adds artificial signals
to satisfy the constraint (long preamble, forced keywords). The resulting prompts
are not representative of real user traffic — they are toy examples of what
complexity looks like to the generator. The label becomes embedded in the style
of the prompt, creating distribution shift at inference time.

**Why generate-then-label?**
The generator is free to write natural, realistic prompts. The labeler assigns
tiers purely on content, the same way it would at inference time. Labels reflect
genuine routing decisions, not prompt-generation artefacts.

---

## 3. Quality-Aligned Labels (not Policy-Aligned)

**Decision**: Labels represent the *minimum sufficient tier* to produce a response
of acceptable quality — not the tier that the current heuristic classifier assigns.

**Why?**
The heuristic classifier is the thing we are trying to replace. If we trained on
its outputs, we would be learning to replicate its bugs (e.g., all moderate prompts
falling into simple). Ground truth must reflect what *actually* warrants a more
capable model.

Operational definition: "The cheapest model that would produce a response the
user would consider satisfactory."

---

## 4. Dual-Pass Labeling with Agreement Filter

**Decision**: Label each prompt twice — once at temperature 0.0, once at 0.3.
Keep a prompt only when:
- Both passes agree on the tier.
- The minimum confidence score across both passes is ≥ 3 (out of 5).

**Why two passes?**
A single call at temperature 0 can produce confidently wrong outputs (the model
commits to a label early and CoT rationalises it). Two independent samples reveal
ambiguous cases where the model is uncertain.

**Why temperature 0.3 for the second pass?**
We want a different sample, not a nearly-identical deterministic one (T=0 twice).
T=0.3 adds enough stochasticity to surface genuine disagreements without making
outputs random.

**Why confidence ≥ 3?**
Scores 1-2 indicate the labeler itself is uncertain ("could go either way").
Including these would add label noise. A conservative threshold is better here
because label quality affects downstream model accuracy more than sample size.

**Expected yield**: ~85% of generated prompts pass the dual-pass filter, giving
~1,020 training records from 1,200 generated.

---

## 5. Anchor Validation (Gate Before Training)

**Decision**: Before using the labeled dataset, run all 60 human-labeled prompts
through the labeler. If labeler–human agreement is < 85%, abort and fix the
labeling prompt.

**Why?**
If the labeler systematically disagrees with human labels, the training data is
corrupted. The 85% threshold was chosen to match a reasonable inter-annotator
agreement baseline for a 4-class task. Per-tier breakdown is logged to pinpoint
which tier is drifting.

**Anchor prompts are held-out**: They are used only for this sanity check and
for final evaluation. They are never included in the training set.

---

## 6. Domain Coverage

Eight domains × 150 prompts each = 1,200 raw prompts:

| Domain | Rationale |
|--------|-----------|
| general_knowledge | Baseline factual / simple / moderate |
| software_engineering | Core classifier challenge: moderate vs complex boundary |
| data_science_ml | Complex analysis; distinct from SE |
| devops_cloud | Infrastructure design; overlaps sensitive (credentials) |
| web_development | High-volume domain; many simple/moderate |
| databases | Query optimization → complex; PII in data → sensitive |
| security_compliance | Primary source of sensitive prompts |
| business_legal | Contracts, NDA → sensitive; analysis → moderate/complex |

Domains were selected to exercise all four tiers and cover realistic production
traffic from the target user base (developers and analysts).

---

## 7. Generation Model

`claude-haiku-4-5-20251001` — cheapest capable model.

The generator's only job is to produce syntactically plausible, domain-relevant
prompts. It does not assign labels. Low cost allows generating surplus prompts
cheaply; temperature 1.0 maximises diversity.

---

## 8. Labeling Model

`claude-opus-4-6` — most capable Anthropic model.

Labels are the signal that trains the classifier. We want the highest accuracy
possible here. Opus cost is acceptable at 1,200 prompts × 2 passes = 2,400 calls.

---

## 9. Chain-of-Thought Labeling Prompt

The labeler prompt uses a four-step decision tree:
1. Check for PII / credentials / regulated data → sensitive
2. Check for code generation / architecture / deep analysis → complex
3. Check for multi-paragraph explanation / comparison → moderate
4. Otherwise → simple

This ordering enforces the routing policy's priority (sensitive overrides
complexity; complex overrides moderate). Few-shot examples are drawn directly
from the 60 human-labeled prompts (one per tier).

---

## 10. Output Format

Training set (`train.json`) uses the same schema as `prompts.json`, extended with:
- `"domain"`: generation domain
- `"confidence"`: minimum across both labeling passes
- `"source": "llm_labeled"` (to distinguish from human labels in analysis)

IDs follow the pattern `gs####` / `gm####` / `gc####` / `gx####` (g = generated).
Human-labeled IDs (`s##`, `m##`, `c##`, `x##`) are preserved in the test set file.

---

## 11. What We Do NOT Do

- We do not include the 60 human-labeled prompts in the training set.
- We do not use the heuristic classifier's output as a label.
- We do not generate prompts by tier (see decision 2).
- We do not train on prompts where the two labeling passes disagree.
- We do not set a target tier distribution — the model generates naturally and
  the labeler assigns; imbalance is handled by class weighting in the classifier.

---

## 12. Next Steps After Dataset Creation

1. Run `experiments/eval_classifier.py --all` after training to compare:
   - heuristic baseline
   - logistic regression (TF-IDF features)
   - XGBoost (TF-IDF + hand-crafted features)
   - embedding classifier (sentence-transformers + XGBoost / MLP)
2. Use LOO-CV on the 60-prompt test set to measure gains (McNemar's test for
   statistical significance).
3. When a learned classifier beats heuristic on macro F1, swap it in at
   `gateway/classifier/` and re-run the gateway integration tests.
