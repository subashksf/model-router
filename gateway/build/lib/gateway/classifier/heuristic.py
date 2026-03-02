"""
Heuristic classifier — no model call required, sub-millisecond latency.

Scoring ladder:
  simple   (0–1) : short messages, no code / legal / PII signals
  moderate (2)   : medium length or mild domain signals
  complex  (3)   : long context, code, structured reasoning
  sensitive(4)   : PII, legal, security keywords → hard-route to safest tier

Replace or augment this with a prompted model call or fine-tuned local
model once the heuristics prove insufficient for your domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gateway.schemas import Message

# ---------------------------------------------------------------------------
# Keyword sets — extend per-domain
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS = re.compile(
    r"\b(ssn|social security|passport|credit.?card|hipaa|phi|pii|"
    r"attorney|legal.?advice|lawsuit|privileged|confidential|password|"
    r"secret.?key|api.?key)\b",
    re.IGNORECASE,
)

_COMPLEX_PATTERNS = re.compile(
    r"\b(implement|refactor|architect|design.?pattern|algorithm|optimize|"
    r"debug|explain.?step|compare|analyze|summarize.{0,30}document|"
    r"write.{0,20}function|generate.{0,20}code)\b",
    re.IGNORECASE,
)

_SIMPLE_PATTERNS = re.compile(
    r"\b(what.?is|define|who.?is|when.?did|yes.?or.?no|translate|"
    r"spell.?check|convert|format)\b",
    re.IGNORECASE,
)

# Token estimate: ~4 chars per token
_CHARS_PER_TOKEN = 4
_SIMPLE_THRESHOLD = 300    # tokens
_COMPLEX_THRESHOLD = 1_500  # tokens


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class Classification:
    complexity: str  # "simple" | "moderate" | "complex" | "sensitive"
    token_count: int
    signals: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return {"simple": 0, "moderate": 1, "complex": 2, "sensitive": 3}[self.complexity]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def classify(messages: list[Message]) -> Classification:
    """Return a Classification for the given message list."""
    text = " ".join(
        m.content if isinstance(m.content, str) else " ".join(
            p.get("text", "") for p in m.content if isinstance(p, dict)
        )
        for m in messages
    )
    token_count = max(1, len(text) // _CHARS_PER_TOKEN)
    signals: list[str] = []

    # Sensitive check is highest priority
    if _SENSITIVE_PATTERNS.search(text):
        signals.append("sensitive_keyword")
        return Classification("sensitive", token_count, signals)

    # Length signal
    if token_count >= _COMPLEX_THRESHOLD:
        signals.append("long_context")
        complexity = "complex"
    elif token_count <= _SIMPLE_THRESHOLD:
        complexity = "simple"
    else:
        complexity = "moderate"

    # Keyword overrides
    if _COMPLEX_PATTERNS.search(text):
        signals.append("complex_keyword")
        complexity = "complex"
    elif _SIMPLE_PATTERNS.search(text) and complexity != "complex":
        signals.append("simple_keyword")
        complexity = "simple"

    return Classification(complexity, token_count, signals)
