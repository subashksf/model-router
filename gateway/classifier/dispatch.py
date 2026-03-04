"""
Classifier dispatcher — selects implementation based on CLASSIFIER_MODE env var.

  CLASSIFIER_MODE=heuristic  (default) — sub-millisecond, no model artifact needed
  CLASSIFIER_MODE=learned               — TF-IDF + LogReg from .pkl artifact

Setting CLASSIFIER_MODE=learned requires the artifact at CLASSIFIER_MODEL_PATH
(or gateway/classifier/artifacts/logreg_classifier.pkl).
"""

from __future__ import annotations

import logging
import os

from gateway.schemas import Message
from gateway.classifier.heuristic import Classification

log = logging.getLogger(__name__)

_MODE = os.environ.get("CLASSIFIER_MODE", "heuristic").lower()

if _MODE not in ("heuristic", "learned"):
    log.warning("Unknown CLASSIFIER_MODE=%r — falling back to heuristic", _MODE)
    _MODE = "heuristic"

log.info("Classifier mode: %s", _MODE)


async def classify(messages: list[Message]) -> Classification:
    if _MODE == "learned":
        from gateway.classifier.model import classify as _classify
    else:
        from gateway.classifier.heuristic import classify as _classify
    return await _classify(messages)
