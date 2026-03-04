"""
Learned classifier — scikit-learn Pipeline loaded from a .pkl artifact.

Drop-in replacement for heuristic.classify(); returns the same Classification type.

The artifact is loaded once (lazy, LRU-cached) from:
  1. CLASSIFIER_MODEL_PATH env var (if set)
  2. gateway/classifier/artifacts/logreg_classifier.pkl (default)

To generate the artifact, run:
  python gateway/classifier/training/train.py
"""

from __future__ import annotations

import functools
import os
import pickle
from pathlib import Path

from gateway.classifier.features import estimate_tokens, extract_text
from gateway.classifier.heuristic import Classification
from gateway.schemas import Message

_DEFAULT_ARTIFACT = Path(__file__).parent / "artifacts" / "logreg_classifier.pkl"


@functools.lru_cache(maxsize=1)
def _load_artifact() -> dict:
    path = Path(os.environ.get("CLASSIFIER_MODEL_PATH", str(_DEFAULT_ARTIFACT)))
    if not path.exists():
        raise FileNotFoundError(
            f"Classifier artifact not found at {path}. "
            "Run: python gateway/classifier/training/train.py"
        )
    with open(path, "rb") as f:
        return pickle.load(f)


async def classify(messages: list[Message]) -> Classification:
    """Classify messages using the trained sklearn Pipeline."""
    artifact = _load_artifact()
    model    = artifact["model"]
    le       = artifact["label_encoder"]

    text       = extract_text(messages)
    token_count = estimate_tokens(text)

    pred_enc   = model.predict([text])[0]
    complexity = le.inverse_transform([pred_enc])[0]

    return Classification(
        complexity=complexity,
        token_count=token_count,
        signals=["learned_classifier"],
    )
