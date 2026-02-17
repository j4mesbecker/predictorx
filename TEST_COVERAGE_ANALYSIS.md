# PredictorX Test Coverage Analysis

## Current State

The project has **zero tests**. The `tests/` directory contains only an empty `__init__.py`. `pytest` and `pytest-asyncio` are listed in `requirements.txt` but unused. There is no `conftest.py`, `pytest.ini`, or `pyproject.toml` test configuration.

The codebase spans **61 Python files and ~6,974 lines** across 8 modules: `core/`, `web/`, `db/`, `config/`, `pipeline/`, `adapters/`, `telegram/`, and `data/`.

---

## Priority 1: Core Scoring & Domain Models (Highest Impact)

These modules contain the mathematical and business logic that directly determines trade decisions. Bugs here mean real money lost.

### `core/models.py` — Domain Models

**Testability: Excellent** (pure dataclasses, zero external dependencies)

What to test:
- `Prediction.is_actionable` — boundary conditions at `edge=0.04` and `confidence_score=0.55`
- `Prediction.urgency` — all three tiers (HIGH/MEDIUM/LOW) with boundary values at `0.04`, `0.08`, `0.55`, `0.65`, `0.85`
- `Opportunity.urgency` — delegation to `Prediction.urgency`
- Default field values and optional field handling

### `core/scoring/kelly.py` — Kelly Criterion Position Sizing

**Testability: Excellent** (pure math with deterministic gates)

What to test:
- **7 sequential validation gates**, each with pass/fail paths:
  1. Balance floor check
  2. Daily deployment cap
  3. Max positions limit
  4. Minimum edge threshold
  5. Kelly formula (invalid market prices at 0 or 1, negative Kelly)
  6. Cost-per-contract (division by zero protection)
  7. Contract rounding and max-trade clipping
- `_get_dynamic_limits()` — tier boundaries at $500, $1000, $2500, $5000
- Kelly formula correctness: `(b*p - q) / b` with fractional scaling
- Payout odds for "yes" vs "no" sides (different formulas)
- End-to-end: full pipeline with all gates passing

### `core/scoring/confidence.py` — Confidence Scoring

**Testability: Good** (pure weighted-sum math, side effects on Prediction)

What to test:
- Each of 5 confidence factors independently with boundary values
- Whale sentiment mapping across all 3 code paths (context value, prediction value, neutral default)
- Weighted sum correctness with known inputs
- Score clamping to `[0.0, 1.0]`
- List sorting by confidence in `score_predictions()`

### `core/scoring/calibration.py` — Probability Calibration

**Testability: Fair** (filesystem dependency, global cache)

What to test:
- Linear interpolation correctness with known calibration pairs
- Boundary handling: raw probability at/below min and at/above max calibration points
- Fallback behavior when calibration file is missing
- Cache behavior (second call uses cache)

Issues to address first:
- Hardcoded path (`/Users/jamesbecker/Desktop/...`) will fail in CI — needs to be configurable or mocked
- Global `_calibration_data` cache causes test interaction — needs reset fixture

---

## Priority 2: Edge Maps & Strategy Logic (High Impact)

These are pure-function modules that compute trade signals. They have no external dependencies beyond static lookup tables, making them ideal test targets with high ROI.

### `core/strategies/spx_edge_map.py` — S&P 500 Edge Signals

**Testability: Excellent** (pure functions, static data only)

What to test:
- Calibration bucket lookup across all price ranges (5-90c) and boundary conditions
- Distance zone factor selection for all 7 zones
- Decision logic for each price range: sweet spot (10-49c), low (5-10c), mid (50-70c), high (70+c)
- Confidence scoring: edge, sample, distance, and event components with weighted composite
- Kelly sizing: division-by-zero handling when `cost=0`, negative edge
- Grade assignment at all 5 boundaries (A+, A, B, C, F)

Recommended approach: parametrized tests with a matrix of `market_price_cents × distance_from_spx × event_type`.

### `core/strategies/weather_edge_map.py` — Weather Edge Signals

**Testability: Excellent** (pure functions, static data only)

What to test:
- `get_actual_yes_rate()` across all 8 calibration brackets, plus <15c and >=85c boundaries
- City-specific data lookups for all 10 cities, plus unknown city fallback
- Month-specific data for all 12 months (especially October with negative ROI)
- Market type lookups for all 5 types
- Blended win rate calculation (40% city, 30% month, 30% type)
- `our_probability` conflict logic: >0.70 halves edge, <0.30 boosts 1.2x
- Kelly sizing with division-by-zero protection
- `get_trade_recommendation()` position sizing with deploy-amount caps

### `core/strategies/options_strategy.py` — Options Signal Generator

**Testability: Excellent** (9 pure helper functions + 4 main functions)

What to test:
- `_round_to_strike()` / `_round_to_strike_up()` for all strike increments
- `_next_weekly_expiry()` from every day of the week, DTE boundary conditions
- `_estimate_premium()` across all OTM distance buckets with VIX scaling
- `_compute_conviction_grade()` across all trigger/regime/confirmation combinations
- `_is_blocked()` for blackout dates and CRISIS regime
- `_get_max_risk()` for all grade/regime combinations
- `compute_naked_put_signal()` / `compute_naked_call_signal()` end-to-end with Brando level matching
- `compute_options_exit_guidance()` profit/loss/time exit paths
- `compute_daily_options_intel()` regime-based guidance and CRISIS early return

---

## Priority 3: Database & Repository Layer (Medium-High Impact)

### `db/repository.py` — Data Access Layer

**Testability: Good** (use in-memory SQLite for tests)

What to test:
- Full CRUD lifecycle for predictions: save → retrieve → settle
- `get_pending_predictions()` correctly filters by `outcome=None`
- `get_recent_predictions()` strategy filtering and ordering
- `get_performance_summary()` aggregation with wins, losses, and strategy breakdown
- `save_whale_signal()` + `get_recent_whale_signals()` time-range and min-amount filtering
- `update_market_cache()` upsert behavior (insert vs update)
- `save_external_intel()` bulk insertion
- Empty result handling for all queries

Recommended fixture:
```python
@pytest.fixture
def repo():
    r = Repository("sqlite:///:memory:")
    init_db("sqlite:///:memory:")
    yield r
```

### `db/models.py` — ORM Schema

**Testability: Good** (integration test with in-memory DB)

What to test:
- All 9 tables created correctly
- Column constraints (nullable, unique, indexes)
- Default values applied
- Foreign key relationship (AlertRecord → PredictionRecord)

---

## Priority 4: Web API Routes (Medium Impact)

### `web/routes/` — FastAPI Endpoints

**Testability: Good** (FastAPI's `TestClient` makes this straightforward)

What to test:
- `GET /api/dashboard` — response structure, rounding, null-safe field access
- `GET /api/opportunities` — query param validation (1-50 range), empty results, VIX fallback
- `GET /api/performance` — date range filtering, cumulative P&L calculation
- `GET /api/performance/predictions` — strategy filtering
- `GET /api/calibration` — missing calibration module fallback
- `GET /health` — availability

Issues to address:
- Routes create `Repository` instances inline — should use FastAPI's dependency injection for testability
- Routes access `repo._session()` directly — breaks encapsulation, add public methods instead

---

## Priority 5: Telegram Formatters (Easy Wins)

### `telegram/formatters.py` — Message Formatting

**Testability: Excellent** (pure string functions, no side effects)

What to test:
- All 14 format functions with representative inputs
- Missing dict keys (`.get()` defaults exercised)
- Empty lists and None values
- Blocked alert conditional formatting
- HTML tag correctness
- Currency and percentage formatting

This module is the lowest-effort, highest-confidence test target. Good place to start building test infrastructure.

---

## Priority 6: Pipeline & External Integrations (Lower Priority for Unit Tests)

### `pipeline/kalshi_executor.py` — Order Execution

**Testability: Medium** (global state, SDK dependency)

What to test:
- 5 safety checks: duplicate detection, per-trade limit, daily limit, balance floor, loss limit
- Order status determination (filled vs resting vs canceled)
- Cost calculation for NO orders (`100 - yes_price`)
- Daily state reset on date change

### `pipeline/spx_monitor.py` — SPX Price Monitoring

**Testability: Medium-High** (complex but isolated logic)

What to test:
- Drop threshold detection at 1%, 1.5%, 2%, 3%, 5%
- Clustering guard logic
- VIX spike/reversion detection
- Monthly and day-of-week adjustments
- Safety gates (blackout dates, VIX regime)

### `pipeline/spx_bracket_scanner.py` — Bracket Scanning

**Testability: Medium** (regex parsing, API pagination)

What to test:
- Title regex parsing for bracket ranges
- Sweet spot filtering (price 10-49c, distance >50pts)
- Grade-based sorting
- Budget enforcement with deployment tracking

### `adapters/` and `telegram/bot.py`

These are thin wrappers around external systems. Test at integration level or with mocks. Lower priority than core logic.

---

## Cross-Cutting Issues to Fix First

### 1. No Test Configuration

Add `conftest.py` with shared fixtures:
```python
# tests/conftest.py
import pytest
from db.repository import Repository
from db.models import init_db

@pytest.fixture
def repo():
    url = "sqlite:///:memory:"
    init_db(url)
    return Repository(url)

@pytest.fixture
def sample_prediction():
    from core.models import Prediction
    return Prediction(
        strategy="weather",
        market_ticker="KXTEMP-NYC-24FEB01-B45",
        side="no",
        edge=0.08,
        confidence_score=0.72,
        market_price=0.35,
        our_probability=0.60,
    )
```

Add pytest configuration to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### 2. Global Singletons Block Test Isolation

`config/settings.py` caches settings in a module-level variable. `calibration.py` caches calibration data globally. Tests need reset fixtures:
```python
@pytest.fixture(autouse=True)
def reset_singletons():
    import config.settings as s
    s._settings = None
    import core.scoring.calibration as c
    c._calibration_data = None
    yield
```

### 3. Hardcoded Paths

`calibration.py` has a hardcoded path to `/Users/jamesbecker/Desktop/...`. This will fail in any environment other than the original developer's machine. Needs to be made configurable through settings.

### 4. Missing Dependency Injection in Web Routes

Routes create `Repository` directly. Use FastAPI's `Depends()` for testability:
```python
# Before (hard to test)
def get_dashboard():
    settings = get_settings()
    repo = Repository(settings.database_sync_url)

# After (testable)
def get_dashboard(repo: Repository = Depends(get_repo)):
```

---

## Recommended Test Implementation Order

| Phase | Target | Tests | Rationale |
|-------|--------|-------|-----------|
| 1 | `core/models.py` | ~15 | Pure dataclasses, zero deps, validates domain logic |
| 2 | `core/scoring/kelly.py` | ~25 | Critical financial math, 7 gates to cover |
| 3 | `core/scoring/confidence.py` | ~15 | Weighted scoring directly affects trade decisions |
| 4 | `core/strategies/spx_edge_map.py` | ~30 | Pure functions, parametrized matrix testing |
| 5 | `core/strategies/weather_edge_map.py` | ~30 | Pure functions, lookup table verification |
| 6 | `core/strategies/options_strategy.py` | ~35 | 9 helpers + 4 main functions, all deterministic |
| 7 | `telegram/formatters.py` | ~20 | Pure string formatting, easy confidence builder |
| 8 | `db/repository.py` | ~15 | CRUD with in-memory SQLite |
| 9 | `web/routes/` | ~15 | FastAPI TestClient integration |
| 10 | `pipeline/kalshi_executor.py` | ~10 | Safety checks for real-money execution |

**Estimated total: ~210 tests** to achieve meaningful coverage of the most critical paths.

---

## Summary

The highest-risk gap is in the **financial calculation modules** (`kelly.py`, `confidence.py`, edge maps). These compute position sizes and trade signals that directly control real money. A single bug in Kelly sizing or edge calculation could cause significant losses, and there are currently zero tests validating their correctness.

The highest-ROI starting point is the **pure-function modules** (`models.py`, `spx_edge_map.py`, `weather_edge_map.py`, `options_strategy.py`, `formatters.py`) because they require no mocking, have deterministic outputs, and contain the densest business logic.
