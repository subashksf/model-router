"""
Offline training script — produces the classifier artifact used by model.py.

Trains a TF-IDF + LogisticRegression pipeline on the LLM-labeled dataset,
evaluates on the held-out 60-prompt test set, then saves the artifact.

Usage (from project root):
  python gateway/classifier/training/train.py

  # Custom paths:
  python gateway/classifier/training/train.py \\
      --train experiments/datasets/train.json \\
      --test  experiments/datasets/prompts.json \\
      --out   gateway/classifier/artifacts/logreg_classifier.pkl
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("train")

TIERS = ["simple", "moderate", "complex", "sensitive"]

_DEFAULT_TRAIN = Path("experiments/datasets/train.json")
_DEFAULT_TEST  = Path("experiments/datasets/prompts.json")
_DEFAULT_OUT   = Path("gateway/classifier/artifacts/logreg_classifier.pkl")


def load_dataset(path: Path) -> tuple[list[str], list[str]]:
    with open(path) as f:
        data = json.load(f)
    texts  = [p["text"] for p in data["prompts"]]
    labels = [p["tier"] for p in data["prompts"]]
    return texts, labels


def build_pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    return Pipeline([
        ("tfidf", TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=20_000,
            sublinear_tf=True,
            min_df=2,
        )),
        ("clf", LogisticRegression(
            class_weight="balanced",
            max_iter=1000,
            C=1.0,
            solver="lbfgs",
        )),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the LogReg complexity classifier")
    parser.add_argument("--train", type=Path, default=_DEFAULT_TRAIN)
    parser.add_argument("--test",  type=Path, default=_DEFAULT_TEST)
    parser.add_argument("--out",   type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    try:
        from sklearn.preprocessing import LabelEncoder
        from sklearn.metrics import accuracy_score, f1_score, classification_report
    except ImportError:
        log.error("scikit-learn not installed. Run: pip install scikit-learn")
        sys.exit(1)

    if not args.train.exists():
        log.error("Training set not found: %s", args.train)
        log.error("Run: python experiments/generate_dataset.py run-all --n 1200")
        sys.exit(1)

    if not args.test.exists():
        log.error("Test set not found: %s", args.test)
        sys.exit(1)

    X_train, y_train = load_dataset(args.train)
    X_test,  y_test  = load_dataset(args.test)
    log.info("Train: %d prompts | Test: %d prompts", len(X_train), len(X_test))

    le = LabelEncoder().fit(TIERS)
    y_train_enc = le.transform(y_train)
    y_test_enc  = le.transform(y_test)

    log.info("Training TF-IDF + LogReg (class_weight=balanced) …")
    pipeline = build_pipeline()
    pipeline.fit(X_train, y_train_enc)

    y_pred_enc = pipeline.predict(X_test)
    acc      = accuracy_score(y_test_enc, y_pred_enc)
    f1_macro = f1_score(y_test_enc, y_pred_enc, average="macro", zero_division=0)
    log.info("Test accuracy: %.1f%%  macro-F1: %.1f%%", acc * 100, f1_macro * 100)
    print("\n" + classification_report(
        le.inverse_transform(y_test_enc),
        le.inverse_transform(y_pred_enc),
        labels=TIERS, zero_division=0,
    ))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "wb") as f:
        pickle.dump({"model": pipeline, "label_encoder": le}, f)
    log.info("Artifact saved → %s", args.out)


if __name__ == "__main__":
    main()
