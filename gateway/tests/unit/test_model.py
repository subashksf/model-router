"""Unit tests for gateway.classifier.model (learned classifier).

Uses a real sklearn Pipeline trained on tiny in-memory data so tests
run without a pre-existing artifact file and without hitting any API.
"""

import pickle
import pytest

from gateway.classifier.model import _load_artifact
from gateway.schemas import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def msg(content: str) -> list[Message]:
    return [Message(role="user", content=content)]


def make_dummy_artifact(tmp_path) -> str:
    """Train a tiny TF-IDF + LogReg pipeline and pickle it to tmp_path."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import LabelEncoder

    TIERS = ["simple", "moderate", "complex", "sensitive"]

    X = [
        # simple
        "What is the capital of France?",
        "Define API.",
        "What does HTTP stand for?",
        "Who wrote Hamlet?",
        # moderate
        "Explain the difference between TCP and UDP.",
        "What are the SOLID principles?",
        "How does gradient descent work?",
        "Compare SQL and NoSQL databases.",
        # complex
        "Implement a thread-safe LRU cache in Python with O(1) operations.",
        "Design a distributed rate limiter that handles 1M requests per second.",
        "Architect a real-time collaborative editing system like Google Docs.",
        "Implement a recursive descent parser for arithmetic expressions.",
        # sensitive
        "My SSN is 123-45-6789. Help me fill out this form.",
        "Review this privileged attorney-client communication.",
        "Our production API key sk-prod-xxx was leaked. What do we do?",
        "This PHI data needs to be transferred. What are the HIPAA steps?",
    ]
    y_labels = (
        ["simple"] * 4 + ["moderate"] * 4 + ["complex"] * 4 + ["sensitive"] * 4
    )

    le      = LabelEncoder().fit(TIERS)
    y_enc   = le.transform(y_labels)
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
        ("clf",   LogisticRegression(max_iter=200, class_weight="balanced")),
    ])
    pipeline.fit(X, y_enc)

    artifact_path = tmp_path / "logreg_classifier.pkl"
    with open(artifact_path, "wb") as f:
        pickle.dump({"model": pipeline, "label_encoder": le}, f)
    return str(artifact_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLearnedClassifier:
    @pytest.fixture(autouse=True)
    def use_dummy_artifact(self, tmp_path, monkeypatch):
        """Point CLASSIFIER_MODEL_PATH to a fresh dummy artifact for each test."""
        path = make_dummy_artifact(tmp_path)
        monkeypatch.setenv("CLASSIFIER_MODEL_PATH", path)
        _load_artifact.cache_clear()
        yield
        _load_artifact.cache_clear()

    async def test_returns_classification_object(self):
        from gateway.classifier.model import classify
        from gateway.classifier.heuristic import Classification

        result = await classify(msg("What is the capital of France?"))
        assert isinstance(result, Classification)

    async def test_complexity_is_valid_tier(self):
        from gateway.classifier.model import classify

        result = await classify(msg("Implement a thread-safe LRU cache."))
        assert result.complexity in {"simple", "moderate", "complex", "sensitive"}

    async def test_token_count_is_positive(self):
        from gateway.classifier.model import classify

        result = await classify(msg("hello world"))
        assert result.token_count >= 1

    async def test_signals_contains_learned_classifier(self):
        from gateway.classifier.model import classify

        result = await classify(msg("What is Python?"))
        assert "learned_classifier" in result.signals

    async def test_obvious_sensitive_prompt(self):
        from gateway.classifier.model import classify

        result = await classify(msg("My SSN is 123-45-6789. Help me fill out this form."))
        assert result.complexity == "sensitive"

    async def test_obvious_complex_prompt(self):
        from gateway.classifier.model import classify

        result = await classify(msg("Implement a thread-safe LRU cache in Python with O(1) operations."))
        assert result.complexity == "complex"

    async def test_missing_artifact_raises_file_not_found(self, monkeypatch):
        monkeypatch.setenv("CLASSIFIER_MODEL_PATH", "/nonexistent/path.pkl")
        _load_artifact.cache_clear()
        from gateway.classifier.model import classify

        with pytest.raises(FileNotFoundError, match="Classifier artifact not found"):
            await classify(msg("hello"))

    async def test_multi_message_input(self):
        from gateway.classifier.model import classify

        messages = [
            Message(role="system", content="You are helpful."),
            Message(role="user",   content="What is the capital of France?"),
        ]
        result = await classify(messages)
        assert result.complexity in {"simple", "moderate", "complex", "sensitive"}


class TestDispatch:
    @pytest.fixture(autouse=True)
    def use_dummy_artifact(self, tmp_path, monkeypatch):
        path = make_dummy_artifact(tmp_path)
        monkeypatch.setenv("CLASSIFIER_MODEL_PATH", path)
        _load_artifact.cache_clear()
        yield
        _load_artifact.cache_clear()

    async def test_heuristic_mode(self, monkeypatch):
        import importlib
        import gateway.classifier.dispatch as dispatch_mod

        monkeypatch.setenv("CLASSIFIER_MODE", "heuristic")
        # Reload to pick up new env var
        importlib.reload(dispatch_mod)

        result = await dispatch_mod.classify(msg("What is the capital of France?"))
        assert result.complexity in {"simple", "moderate", "complex", "sensitive"}
        assert "learned_classifier" not in result.signals

    async def test_learned_mode(self, monkeypatch):
        import importlib
        import gateway.classifier.dispatch as dispatch_mod

        monkeypatch.setenv("CLASSIFIER_MODE", "learned")
        importlib.reload(dispatch_mod)

        result = await dispatch_mod.classify(msg("What is the capital of France?"))
        assert result.complexity in {"simple", "moderate", "complex", "sensitive"}
        assert "learned_classifier" in result.signals

    async def test_unknown_mode_falls_back_to_heuristic(self, monkeypatch):
        import importlib
        import gateway.classifier.dispatch as dispatch_mod

        monkeypatch.setenv("CLASSIFIER_MODE", "invalid_mode")
        importlib.reload(dispatch_mod)

        result = await dispatch_mod.classify(msg("hello"))
        assert result.complexity in {"simple", "moderate", "complex", "sensitive"}
