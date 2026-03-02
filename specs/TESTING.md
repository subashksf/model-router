# Testing Strategy

**Status:** Draft
**Version:** 0.1
**Last updated:** 2026-03-01

---

## 1. Philosophy

Tests should give confidence that the system routes correctly and does not regress. Prefer:
- **Fast unit tests** for the classifier and router (no I/O, no mocks needed)
- **Integration tests with mocked providers** for the full request pipeline
- **E2E tests** only for the critical user path (one test that hits a real provider)

Avoid:
- Testing internal implementation details
- Mocking SQLAlchemy at the unit level (use a real test DB instead)
- Over-testing the provider adapters against live APIs in CI

---

## 2. Test Pyramid

```
         ┌──────────┐
         │  E2E (1) │   Real Docker Compose stack, 1 real API call
         └──────────┘
      ┌──────────────────┐
      │  Integration (20)│   FastAPI TestClient, providers mocked
      └──────────────────┘
   ┌──────────────────────────┐
   │     Unit (50+)           │   classifier, router, policy, cost calc
   └──────────────────────────┘
```

---

## 3. Unit Tests

Location: `gateway/tests/unit/`

### 3.1 Classifier (`test_classifier.py`)

| Test | Input | Expected |
|------|-------|----------|
| Short factual question | "What is the capital of France?" | `simple` |
| Long context > 1500 tokens | 6000-char string | `complex` |
| Sensitive keyword — PII | "my SSN is 123-45-6789" | `sensitive` |
| Sensitive keyword — legal | "I need legal advice about a lawsuit" | `sensitive` |
| Complex keyword | "Implement a distributed rate limiter" | `complex` |
| Simple keyword overrides moderate length | "What is <500-token explanation>?" | `simple` |
| Sensitive overrides long context | Long text + "confidential" keyword | `sensitive` |
| Multi-modal content (list) | `[{"type": "text", "text": "hello"}]` | `simple` |
| Empty messages list | `[]` | should not raise; returns some tier |
| Classifier exception → fallback | (mock internal error) | falls back to `complex` |

### 3.2 Router (`test_router.py`)

| Test | Setup | Expected |
|------|-------|----------|
| Simple routes to cheap tier | default policy, `complexity=simple` | `haiku` model |
| Complex routes to premium tier | default policy, `complexity=complex` | `sonnet` model |
| Feature tag override | policy has `autocomplete → simple`, `complexity=complex` | `haiku` model |
| Unknown tenant falls back to default | tenant `"unknown"` | default policy applied |
| Missing tier falls back to complex | policy with `simple` missing | `complex` tier used |
| Policy YAML parse error | malformed YAML file | raises exception |
| First override wins | two overlapping overrides | first one used |

### 3.3 Policy Loader (`test_policy.py`)

| Test | Input | Expected |
|------|-------|----------|
| Valid policy file loads | `default_policy.yaml` | `Policy` object returned |
| Missing tiers | YAML without `sensitive` key | loads, falls back at route time |
| Invalid provider name | `provider: unknown` | loads (not validated at load time) |
| lru_cache hit | load same file twice | file read once |

### 3.4 Cost Calculation (`test_telemetry.py`)

| Test | Input | Expected |
|------|-------|----------|
| Haiku cost formula | 1000 tokens in, 500 out | `1000 * 0.00025 / 1000 + 500 * 0.00125 / 1000` |
| Baseline (GPT-4o) cost | same tokens | `1000 * 0.005 / 1000 + 500 * 0.015 / 1000` |
| Savings = baseline - actual | above two | `baseline - actual` |
| Unknown model in cost table | `model="future-model"` | cost = 0, warning logged |
| Zero tokens | 0 in, 0 out | cost = 0 |

---

## 4. Integration Tests

Location: `gateway/tests/integration/`
Setup: `pytest-asyncio`, `httpx.AsyncClient` with `TestClient(app)`, provider mocked.

### 4.1 Chat endpoint (`test_chat.py`)

Use a fixture that patches `gateway.providers.registry.get_provider` to return a mock provider that returns a deterministic `ChatCompletionResponse`.

| Test | Request | Expected |
|------|---------|----------|
| Simple query returns 200 | `{"messages":[{"role":"user","content":"hi"}]}` | 200, valid `ChatCompletionResponse` |
| Response `model` field reflects actual routed model | default policy | response `model` != request `model` |
| Feature tag override changes routing | `X-Feature-Tag: autocomplete` + complex query | routes to simple tier |
| Tenant policy selects correct model | custom tenant YAML with different model | routed to tenant's model |
| Missing `messages` field | `{}` | 422 Unprocessable Entity |
| Provider raises exception | mock raises `Exception("API error")` | 502 with error detail |
| Streaming returns SSE | `stream: true` | `Content-Type: text/event-stream`, ends with `[DONE]` |
| Telemetry is emitted | any request | `emit` called with correct routing info |
| Telemetry DB failure does not fail request | mock DB raises exception | 200 returned, error logged |

### 4.2 Stats endpoint (`test_stats.py`)

Use a test DB (in-memory SQLite for unit, real Postgres for CI integration).

| Test | Setup | Expected |
|------|-------|----------|
| Empty DB returns zeros | no events | `totalCostUsd: 0`, empty arrays |
| Events aggregated correctly | 3 events with known costs | sum matches |
| Window filter works | events at t-1h and t-48h | `24h` window excludes t-48h |
| Tenant filter works | events for tenant A and B | tenant A filter returns only A |
| byFeature groups correctly | 3 features, 10 requests each | 3 entries |
| `untagged` label for null feature_tag | events with no feature tag | `featureTag: "untagged"` |

---

## 5. E2E Test

Location: `gateway/tests/e2e/`

**One test only.** Requires real API keys (skip in CI unless secrets are available).

```python
@pytest.mark.e2e
async def test_full_round_trip():
    """Send a real request through the full stack and verify the response."""
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        resp = await client.post(
            "/v1/chat/completions",
            headers={"X-Feature-Tag": "e2e-test"},
            json={"model": "auto", "messages": [{"role": "user", "content": "Say hello."}]},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"]
    assert data["model"] in ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"]
```

---

## 6. Routing Accuracy Evaluation

Not automated in CI (requires human labels). Process:

1. Collect 100 real requests from production (or generate a representative sample).
2. Human-label each request with the "correct" tier.
3. Run the classifier over the sample and compare.
4. Compute precision/recall per tier.
5. Record in `specs/accuracy-eval/YYYY-MM.md`.

Target: ≥ 70% overall accuracy on first eval. See CLASSIFIER.md §7 for per-tier targets.

---

## 7. CI Pipeline

```yaml
# .github/workflows/test.yml (to be created)
jobs:
  test:
    steps:
      - name: Install deps
        run: uv sync --dev
      - name: Lint
        run: ruff check gateway/
      - name: Type check
        run: mypy gateway/
      - name: Unit + Integration tests
        run: pytest gateway/tests/ -m "not e2e"
        env:
          DATABASE_URL: postgresql+asyncpg://router:secret@localhost:5432/model_router_test
      - name: E2E (on main only)
        if: github.ref == 'refs/heads/main'
        run: pytest gateway/tests/ -m e2e
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

---

## 8. Test Fixtures

### Policy fixture
```python
@pytest.fixture
def policy_dir(tmp_path):
    """Write a minimal default_policy.yaml to a temp dir."""
    policy = {
        "tiers": {
            "simple":    {"provider": "mock", "model": "mock-cheap"},
            "moderate":  {"provider": "mock", "model": "mock-mid"},
            "complex":   {"provider": "mock", "model": "mock-premium"},
            "sensitive": {"provider": "mock", "model": "mock-safe"},
        },
        "overrides": [],
    }
    (tmp_path / "default_policy.yaml").write_text(yaml.dump(policy))
    monkeypatch.setenv("POLICIES_DIR", str(tmp_path))
    return tmp_path
```

### Mock provider fixture
```python
@pytest.fixture
def mock_provider():
    provider = AsyncMock(spec=BaseProvider)
    provider.complete.return_value = ChatCompletionResponse(
        id="test-id", created=0, model="mock-cheap",
        choices=[ChatChoice(message=Message(role="assistant", content="hello"))],
    )
    return provider
```
