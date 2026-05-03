# BIT — Roadmap

## Phase 1 — Foundation (current)

Goal: Clean, typed, testable skeleton. No live logic.

- [x] Project structure and tooling
- [x] Domain models (enums, market data, features, signals, decisions, risk, execution, journal)
- [x] Config with Pydantic BaseSettings
- [x] Service skeletons with honest stubs
- [x] Strategy stubs (TrendContinuation, BreakoutConfirmation)
- [x] Pipeline orchestrator skeleton
- [x] Test structure with conftest fixtures
- [x] Tests for deterministic services (DecisionEngine, RiskEngine, ExecutionEngine paper mode)

---

## Phase 2 — Market Data

Goal: Real data flowing from Bybit into typed domain models.

- [ ] Implement `MarketDataService.get_klines()` via Bybit REST
- [ ] Implement `MarketDataService.get_ticker()` via Bybit REST
- [ ] Implement `MarketDataService.get_orderbook_top()` via Bybit REST
- [ ] Implement `MarketDataService.get_instrument_filter()` — cache result, changes rarely
- [ ] Implement `MarketDataService.get_portfolio_state()` — paper portfolio state manager
- [ ] Handle Bybit rate limits and retry logic
- [ ] Unit tests with mocked HTTP responses

---

## Phase 3 — Feature Engine

Goal: Indicators computed from real kline data.

- [ ] EMA (fast/slow) per timeframe
- [ ] RSI(14) per timeframe
- [ ] ATR(14) per timeframe
- [ ] Volume SMA(20) per timeframe
- [ ] 20-bar high/low range per timeframe
- [ ] Spread % from ticker
- [ ] Unit tests: fixed kline sequences → expected indicator values

---

## Phase 4 — Strategies

Goal: Two scored strategies returning honest signals.

- [ ] `TrendContinuationStrategy.evaluate()`:
  - H1 EMA trend direction
  - M15 RSI momentum filter
  - M5 pullback to EMA zone
  - M5 volume above average
- [ ] `BreakoutConfirmationStrategy.evaluate()`:
  - H1 consolidation range detection
  - M15 close above 20-bar high
  - M15 volume > 1.5x average
  - M15 RSI filter (not overbought)
- [ ] Unit tests: synthetic feature sets → expected signal scores

---

## Phase 5 — Full Pipeline Integration

Goal: All services wired together, end-to-end cycle running.

- [ ] Wire `Pipeline` with real service instances
- [ ] Paper portfolio state tracker (tracks simulated fills)
- [ ] Scheduled or event-driven loop over symbols
- [ ] End-to-end integration test with mocked market data
- [ ] Journal output validates schema on read

---

## Phase 6 — Paper Trading Validation

Goal: 30+ days of consistent paper trading results.

- [ ] Run paper trading loop for BTCUSDT, ETHUSDT, SOLUSDT
- [ ] Review journal: expectancy per strategy, win rate, drawdown
- [ ] Tune `enter_threshold` and `monitor_threshold` based on data
- [ ] Confirm fee and slippage model is realistic
- [ ] Confirm instrument constraints are respected (no rejected fills)
- [ ] No parameter changes without documented rationale

---

## Phase 7 — Live Trading

Goal: Real capital deployment, only after Phase 6 validation.

- [ ] Implement `ExecutionEngine._live_execute()` via Bybit REST
- [ ] Add order status polling / WebSocket fill confirmation
- [ ] Add position reconciliation against Bybit account state
- [ ] Staged rollout: one symbol, reduced size
- [ ] Monitor expectancy and drawdown in real time

---

## Post-v1 Considerations (not scheduled)

These are only worth adding after a clean data foundation exists:

- Regime detection (trend / range / volatile / thin)
- Volatility-scaled position sizing
- Portfolio-level risk (correlated exposure limits)
- Additional strategies
- Advanced execution (TWAP, limit order management)
- ML — only where a labeled dataset and validation process exist
