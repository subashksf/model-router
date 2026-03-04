"""
Train and compare complexity classifiers for the model router.

Loads train.json (LLM-labeled), evaluates with 5-fold stratified CV,
then scores each model on the held-out 60-prompt test set (prompts.json).
Uses class_weight='balanced' throughout to compensate for tier imbalance.

Usage:
  pip install scikit-learn xgboost
  python experiments/train_classifier.py           # train + compare all models
  python experiments/train_classifier.py --save    # also save best model to experiments/models/
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB, ComplementNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder
from sklearn.svm import LinearSVC
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_validate
from sklearn.metrics import (
    accuracy_score, f1_score, classification_report, confusion_matrix
)

try:
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False

TIERS      = ["simple", "moderate", "complex", "sensitive"]
TRAIN_PATH = Path("experiments/datasets/train.json")
TEST_PATH  = Path("experiments/datasets/prompts.json")
MODEL_DIR  = Path("experiments/models")

# ── Load data ─────────────────────────────────────────────────────────────────

def load_dataset(path: Path) -> tuple[list[str], list[str]]:
    with open(path) as f:
        data = json.load(f)
    texts  = [p["text"] for p in data["prompts"]]
    labels = [p["tier"] for p in data["prompts"]]
    return texts, labels


# ── TF-IDF feature builder ────────────────────────────────────────────────────

def make_tfidf() -> TfidfVectorizer:
    return TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=20_000,
        sublinear_tf=True,
        min_df=2,
    )


# ── Model definitions ─────────────────────────────────────────────────────────

def build_models() -> dict[str, Pipeline]:
    models: dict[str, Pipeline] = {
        "LogReg": Pipeline([
            ("tfidf", make_tfidf()),
            ("clf",   LogisticRegression(
                class_weight="balanced", max_iter=1000, C=1.0, solver="lbfgs",
            )),
        ]),
        "LinearSVC": Pipeline([
            ("tfidf", make_tfidf()),
            ("clf",   LinearSVC(class_weight="balanced", max_iter=2000, C=1.0)),
        ]),
        "ComplementNB": Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=20_000,
                                      sublinear_tf=True, min_df=2)),
            ("clf",   ComplementNB(alpha=0.1)),
        ]),
        "RandomForest": Pipeline([
            ("tfidf", make_tfidf()),
            ("clf",   RandomForestClassifier(
                n_estimators=300, class_weight="balanced",
                max_depth=None, n_jobs=-1, random_state=42,
            )),
        ]),
        "GradientBoosting": Pipeline([
            ("tfidf", make_tfidf()),
            ("clf",   GradientBoostingClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.1, random_state=42,
            )),
        ]),
    }

    if HAS_XGBOOST:
        models["XGBoost"] = Pipeline([
            ("tfidf", make_tfidf()),
            ("clf",   XGBClassifier(
                n_estimators=300, max_depth=6, learning_rate=0.1,
                use_label_encoder=False, eval_metric="mlogloss",
                random_state=42, n_jobs=-1,
            )),
        ])

    return models


# ── Cross-validation ──────────────────────────────────────────────────────────

def cv_evaluate(
    model: Pipeline,
    X: list[str],
    y: list[str],
    n_splits: int = 5,
) -> dict:
    le = LabelEncoder().fit(TIERS)
    y_enc = le.transform(y)

    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    t0  = time.perf_counter()
    res = cross_validate(
        model, X, y_enc,
        cv=cv,
        scoring=["accuracy", "f1_macro", "f1_weighted"],
        return_train_score=False,
        n_jobs=1,
    )
    elapsed = time.perf_counter() - t0

    return {
        "cv_accuracy":    float(np.mean(res["test_accuracy"])),
        "cv_f1_macro":    float(np.mean(res["test_f1_macro"])),
        "cv_f1_weighted": float(np.mean(res["test_f1_weighted"])),
        "cv_time_s":      round(elapsed, 2),
    }


# ── Test-set evaluation ───────────────────────────────────────────────────────

def test_evaluate(
    model: Pipeline,
    X_train: list[str],
    y_train: list[str],
    X_test: list[str],
    y_test: list[str],
) -> dict:
    le = LabelEncoder().fit(TIERS)
    model.fit(X_train, le.transform(y_train))

    y_pred_enc = model.predict(X_test)
    y_test_enc = le.transform(y_test)
    y_pred     = le.inverse_transform(y_pred_enc)

    acc       = accuracy_score(y_test_enc, y_pred_enc)
    f1_macro  = f1_score(y_test_enc, y_pred_enc, average="macro", zero_division=0)
    f1_per    = f1_score(y_test_enc, y_pred_enc, average=None,    zero_division=0,
                         labels=le.transform(TIERS))
    report    = classification_report(y_test, y_pred, labels=TIERS, zero_division=0)
    cm        = confusion_matrix(y_test, y_pred, labels=TIERS).tolist()

    return {
        "test_accuracy":   float(acc),
        "test_f1_macro":   float(f1_macro),
        "test_f1_per_tier": dict(zip(TIERS, [round(float(f), 3) for f in f1_per])),
        "classification_report": report,
        "confusion_matrix": cm,
    }


# ── Pretty printer ────────────────────────────────────────────────────────────

def print_summary(results: list[dict]) -> None:
    header = f"{'Model':<20} {'CV Acc':>8} {'CV F1-M':>8} {'Test Acc':>9} {'Test F1-M':>10}"
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    for r in sorted(results, key=lambda x: x["test_f1_macro"], reverse=True):
        print(
            f"{r['name']:<20} "
            f"{r['cv_accuracy']:>8.1%} "
            f"{r['cv_f1_macro']:>8.1%} "
            f"{r['test_accuracy']:>9.1%} "
            f"{r['test_f1_macro']:>10.1%}"
        )
    print("=" * len(header))

    best = max(results, key=lambda x: x["test_f1_macro"])
    print(f"\nBest model: {best['name']}  (test macro-F1 = {best['test_f1_macro']:.1%})")
    print("\nClassification report:\n")
    print(best["classification_report"])

    print("Confusion matrix (rows=actual, cols=predicted):")
    col_w = 11
    print(" " * 12 + "".join(f"{t:>{col_w}}" for t in TIERS))
    for tier, row in zip(TIERS, best["confusion_matrix"]):
        print(f"{tier:<12}" + "".join(f"{v:>{col_w}}" for v in row))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Train and compare complexity classifiers")
    parser.add_argument("--save",     action="store_true", help="Save best model to experiments/models/")
    parser.add_argument("--cv-only",  action="store_true", help="Skip test-set eval (faster)")
    parser.add_argument("--n-splits", type=int, default=5,  help="CV folds (default 5)")
    args = parser.parse_args()

    if not TRAIN_PATH.exists():
        print(f"ERROR: {TRAIN_PATH} not found. Run the merge phase first.", file=sys.stderr)
        sys.exit(1)
    if not TEST_PATH.exists():
        print(f"ERROR: {TEST_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    X_train, y_train = load_dataset(TRAIN_PATH)
    X_test,  y_test  = load_dataset(TEST_PATH)

    print(f"Train: {len(X_train)} prompts  |  Test: {len(X_test)} prompts")
    from collections import Counter
    train_dist = Counter(y_train)
    print("Train distribution:", {t: train_dist[t] for t in TIERS})
    print("Test  distribution:", {t: Counter(y_test)[t] for t in TIERS})

    if not HAS_XGBOOST:
        print("\nNote: xgboost not installed — skipping XGBoost. Run: pip install xgboost")

    models  = build_models()
    results = []

    for name, model in models.items():
        print(f"\n[{name}] Running {args.n_splits}-fold CV …", end=" ", flush=True)
        cv_res = cv_evaluate(model, X_train, y_train, n_splits=args.n_splits)
        print(f"CV macro-F1={cv_res['cv_f1_macro']:.1%}  ({cv_res['cv_time_s']}s)", flush=True)

        if not args.cv_only:
            test_res = test_evaluate(model, X_train, y_train, X_test, y_test)
        else:
            test_res = {"test_accuracy": 0.0, "test_f1_macro": 0.0,
                        "test_f1_per_tier": {}, "classification_report": "", "confusion_matrix": []}

        results.append({"name": name, **cv_res, **test_res})

    print_summary(results)

    # Save results JSON
    results_dir = Path("experiments/results")
    results_dir.mkdir(exist_ok=True)
    ts   = time.strftime("%Y%m%dT%H%M%S")
    out  = results_dir / f"classifier_comparison_{ts}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results → {out}")

    if args.save:
        best  = max(results, key=lambda x: x["test_f1_macro"])
        MODEL_DIR.mkdir(exist_ok=True)
        # Retrain best model on full training set
        best_model = models[best["name"]]
        le = LabelEncoder().fit(TIERS)
        best_model.fit(X_train, le.transform(y_train))
        model_path = MODEL_DIR / f"{best['name'].lower()}_classifier.pkl"
        with open(model_path, "wb") as f:
            pickle.dump({"model": best_model, "label_encoder": le}, f)
        print(f"Saved best model ({best['name']}) → {model_path}")


if __name__ == "__main__":
    main()
