# BIT — Architecture

## Pipeline Flow

One evaluation cycle per symbol per tick:

```
MarketDataService.get_*()
    ↓  raw typed domain models
FeatureEngine.compute()
    ↓  FeatureSet (indicators per timeframe)
SignalEngine.evaluate()
    ↓  list[Signal] (one per strategy, scored 0–1)
DecisionEngine.decide()
    ↓  Decision (ENTER | MONITOR | REJECT + composite score + rationale)
    │
    └─ if ENTER:
           RiskEngine.approve()
               ↓  SizingResult (qty, notional, approved flag)
               └─ if approved:
                      ExecutionEngine.execute()
                          ↓  Fill (fill price, fee, slippage)
                          └─ JournalLearningStore.record()
```

Every cycle produces a `JournalEntry` regardless of decision state.

---

## HTTP / Parsing Layer (bit.bybit)

`MarketDataService` does not talk to Bybit directly. It delegates to two thin modules:

```
bit.bybit.client.BybitRestClient
    — async HTTP, timeout, status codes, retCode validation, envelope extraction
    — returns raw result dict; knows nothing about domain models
    — injectable _transport for test isolation (no live network in tests)

bit.bybit.parsers
    — pure functions: result dict → domain model
    — no HTTP, no state, no side effects
    — fully testable with plain Python dicts
```

This split means HTTP failure tests never need domain knowledge, and parser
correctness tests never need HTTP mocking.

Exceptions:
- `BybitAPIError`     — non-zero retCode from exchange
- `BybitNetworkError` — HTTP error, timeout, invalid JSON
- `BybitParseError`   — unexpected response structure (subclass of ValueError)

---

## Service Responsibilities

### MarketDataService
- Thin glue between `BybitRestClient` and parsers
- Implemented (v1): `get_klines`, `get_ticker`, `get_instrument_filter` (with in-memory cache)
- Stubs: `get_orderbook_top`, `get_recent_trades`, `get_portfolio_state`
- Supports async context manager (`async with MarketDataService(config) as svc`)
- Output types: `Kline`, `Ticker`, `InstrumentFilter`

### FeatureEngine
- Pure computation: no I/O, no state
- Input: lists of `Kline` per timeframe + `Ticker`
- Output: `FeatureSet` with `KlineFeatures` per timeframe (EMA, RSI, ATR, volume MA, range bounds)
- All computations are deterministic and unit-testable

### SignalEngine
- Holds a list of registered `BaseStrategy` instances
- Fan-out: calls `strategy.evaluate(features)` on each, collecting all `Signal` objects
- Filter: keeps only signals with `score > 0` as viable candidates
- Select: picks the highest-scoring candidate; ties broken by a fixed `_STRATEGY_PRIORITY` order
- Returns `AggregatedSignal` containing all evaluations + the selected candidate + rationale
- Adding a new strategy requires no changes to SignalEngine itself

### DecisionEngine
- Accepts `AggregatedSignal` from SignalEngine
- If `selected` is None (no viable strategy): always emits `REJECT` with `composite_score = 0`
- Otherwise `composite_score = selected_signal.score`
- Applies thresholds from config to emit `ENTER`, `MONITOR`, or `REJECT`
- All strategy IDs listed in `contributing_strategies` regardless of their individual score
- Full evaluation detail threaded into `rationale` for journal traceability
- No side effects — deterministic given the same inputs and config

### RiskEngine
- Enforces capital allocation: `max_position_pct * available_usdt`
- Enforces max open positions
- Snaps quantity to `qty_step` (floor)
- Validates against `min_order_qty` and `min_order_usdt`
- Returns `SizingResult(approved=False, rejection_reason=...)` on failure
- No side effects — deterministic given the same inputs and config

### ExecutionEngine
- Paper mode: simulates market fill with configurable fee + slippage
- Live mode: submits to Bybit REST API (not implemented in v1)
- Interface is identical in both modes — callers do not need to branch on mode
- Controlled by `BITConfig.paper_trading`

### JournalLearningStore
- Appends one `JournalEntry` per pipeline cycle to a JSONL file
- Records: decision state, scores, rationale, fill details, is_paper flag
- Provides `read_all()` for analysis
- v1: JSONL file in `data/journal.jsonl`
- Future: migrate to SQLite for querying

---

## Domain Model Layers

```
bit.domain.enums       — Symbol, Timeframe, DecisionState, OrderSide, StrategyId
bit.domain.market      — Kline, Ticker, OrderbookTop, RecentTrade, InstrumentFilter, PortfolioState, Position
bit.domain.features    — FeatureSet, KlineFeatures
bit.domain.signals     — Signal, AggregatedSignal
bit.domain.decisions   — Decision
bit.domain.risk        — SizingResult
bit.domain.execution   — Order, Fill
bit.domain.journal     — JournalEntry
```

Domain models are pure Pydantic models. They have no methods beyond validation.
Services import domain models; domain models never import services or config.

---

## Data Flow Contracts

| From | To | Type |
|---|---|---|
| MarketDataService | FeatureEngine | `list[Kline]` × 3 timeframes + `Ticker` |
| FeatureEngine | SignalEngine | `FeatureSet` |
| SignalEngine | DecisionEngine | `AggregatedSignal` |
| DecisionEngine | RiskEngine | `Decision` (ENTER only) |
| RiskEngine | ExecutionEngine | `SizingResult` |
| ExecutionEngine | JournalLearningStore | `Fill` |
| All stages | JournalLearningStore | `JournalEntry` |

---

## Configuration

All config lives in `BITConfig` (Pydantic BaseSettings). Loaded from environment variables or `.env` file. No hardcoded values in service code.

See `.env.example` for all available settings.

---

## Paper vs. Live Execution

`BITConfig.paper_trading = True` (default) → `ExecutionEngine` simulates fills locally.
`BITConfig.paper_trading = False` → `ExecutionEngine` submits to Bybit REST API.

The rest of the pipeline is identical in both modes. This design ensures paper trading results are representative of live behavior.
