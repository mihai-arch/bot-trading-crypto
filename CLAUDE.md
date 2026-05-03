# BIT — Crypto Trading Bot

## 1. Project Overview

**BIT** is a crypto-native algorithmic trading bot built from scratch. It has no relationship to any prior Polymarket or prediction-market architecture.

BIT is designed to be:
- **Rule-based** and fully auditable in v1
- **Transparent** — every decision must be inspectable in logs
- **Iterative** — start small, validate, then scale responsibly
- **Crypto-native** — respects exchange constraints (tick size, qty step, min notional, fees, slippage)

Target exchange: **Bybit spot**
Target symbols: **BTCUSDT, ETHUSDT, SOLUSDT**
Capital assumption: **500 USD**
Direction: **Long-only**

---

## 2. Current Status

> **v1 foundation complete. Phase 2 (MarketDataService) is next.**

- Project structure, tooling, and docs are initialized.
- All domain models and typed schemas are defined.
- Service skeletons exist with honest stubs (raise `NotImplementedError`).
- `DecisionEngine` and `RiskEngine` are fully implemented (deterministic logic only).
- `ExecutionEngine` paper mode is implemented. Live mode raises `NotImplementedError`.
- `JournalLearningStore` (JSONL) is implemented.
- `Pipeline` orchestrator is wired but depends on `FeatureEngine`.
- `MarketDataService` is implemented: `get_klines`, `get_ticker`, `get_instrument_filter`.
- `bit.bybit.client.BybitRestClient` — thin async HTTP layer with injectable transport.
- `bit.bybit.parsers` — pure parsing functions, fully unit-tested.
- Test structure exists; all deterministic services and parsers have passing tests.

**Next:** Implement `FeatureEngine` (indicators) → Strategies → paper portfolio tracker.

---

## 3. v1 Scope

### Included
- Bybit spot market only
- BTCUSDT, ETHUSDT, SOLUSDT
- Long-only entries
- Paper trading with realistic fee and slippage simulation
- Rule-based signals: trend continuation, breakout confirmation
- Auditable decision pipeline with explicit ENTER / MONITOR / REJECT states

### Explicitly Excluded in v1
- Futures, perpetuals, margin
- Leverage
- Short selling
- News sentiment
- Social sentiment
- Heavy ML or neural networks
- Autonomous parameter optimization

---

## 4. v1 Architecture

```
MarketDataService
    → FeatureEngine
        → SignalEngine
            → DecisionEngine  ←→  RiskEngine
                → ExecutionEngine
                    → Journal / LearningStore
```

### Services

| Service | Responsibility |
|---|---|
| `MarketDataService` | Fetch and normalize market data from Bybit |
| `FeatureEngine` | Compute indicators and structured features from raw data |
| `SignalEngine` | Apply trading strategies, produce scored signals |
| `DecisionEngine` | Combine signals into ENTER / MONITOR / REJECT decisions |
| `RiskEngine` | Enforce position sizing, drawdown limits, exposure rules |
| `ExecutionEngine` | Place/cancel orders; paper trade or live with same interface |
| `Journal/LearningStore` | Log all decisions, trades, outcomes for analysis |

### Market Inputs

- `kline_5m`, `kline_15m`, `kline_1h`
- `ticker_live`
- `orderbook_top`
- `recent_trades`
- `instrument_filters` (tick size, qty step, min notional)
- `portfolio_state`

### v1 Strategies

- **Trend continuation** — enter on confirmed uptrend continuation signals
- **Breakout confirmation** — enter on volume-confirmed range breakouts

---

## 5. Trading Philosophy

### Decision States

- `ENTER` — all conditions met, risk approved, execute entry
- `MONITOR` — setup forming, no action yet, watch closely
- `REJECT` — conditions not met or risk rejected, log reason and skip

### Decision Rules

- Every decision must be **explicitly scored** (not black-box)
- Every ENTER or REJECT must have a logged rationale
- Alpha logic (signals) must be **separated** from risk logic and execution logic
- Paper trading must model: fees, slippage, fill latency, partial fills, min notional

### v1 Learning (Light Only)

No autonomous ML optimization in v1. Allow only:
- Expectancy tracking per strategy
- Threshold sensitivity analysis
- Strategy ranking by regime
- Regime observation logging

**No ML before a clean data foundation is established.**

---

## 6. Long-term Vision

BIT should evolve into a robust, regime-aware, multi-strategy trading system that remains interpretable and controllable at every stage.

Future capabilities (post-v1, only when justified):
- Regime detection (trend, range, volatile, low-volume)
- Smarter position sizing (Kelly-adjacent, volatility-scaled)
- Additional strategies and asset coverage
- Portfolio-level risk management
- Advanced execution logic (TWAP, smart order routing)
- ML only where validated on clean, labeled historical data

---

## 7. Engineering Rules

- **Simplicity first** — no premature abstractions, no over-engineering
- **No fake AI complexity** — rule-based beats black-box when explainability matters
- **Typed schemas** — use Pydantic or dataclasses for all inter-service contracts
- **Separation of concerns** — alpha ≠ risk ≠ execution; keep them isolated
- **Testability from day one** — every deterministic function must have unit tests
- **Environment variables for all secrets** — no keys in code or config files
- **Respect exchange constraints** — tick size, qty step, min notional, rate limits
- **Log everything** — decisions, signals, rejections, fills, errors
- **Small commits** — reviewable, single-purpose changes
- **Docs alongside code** — schemas and architecture docs updated with changes

---

## 8. Working Rules for Claude Code

- **Always read `CLAUDE.md` before making significant changes** — do not assume context
- Preserve the **crypto-native, Bybit spot, long-only** direction unless explicitly told otherwise
- **Do not introduce futures, leverage, margin, or shorting** without an explicit request and justification
- **Do not add ML systems** unless explicitly requested and backed by clean data and a validation plan
- Prefer **incremental implementation** — build one layer at a time, validate, then proceed
- **Document assumptions** — if you make an architectural assumption, note it in a comment or update docs
- If architecture changes materially, **update docs first** or alongside the code change
- **When unsure, optimize for clarity and debuggability**, not cleverness or performance
- Use `paper_trading=True` mode as the default; live trading requires explicit opt-in
- Use Python type hints throughout; prefer Pydantic models for data contracts
- When adding a new service, follow the existing pattern: separate module, typed inputs/outputs, unit tests

---

## 9. Suggested Next Implementation Steps

1. **Scaffold repo structure** — `bit/`, `tests/`, `docs/`, `schemas/`, `.env.example`
2. **Define data schemas** — Pydantic models for kline, ticker, orderbook, signal, decision, fill
3. **Implement `MarketDataService`** — Bybit REST + WebSocket, normalize to internal schema
4. **Implement `FeatureEngine`** — indicators (EMA, RSI, ATR, volume profile) from normalized klines
5. **Implement `SignalEngine`** — trend continuation and breakout strategies, return scored signals
6. **Implement `DecisionEngine`** — combine signals, apply thresholds, emit ENTER/MONITOR/REJECT
7. **Implement `RiskEngine`** — position sizing (% of capital), max open positions, drawdown guard
8. **Implement `ExecutionEngine` (paper mode)** — simulate fills with fee + slippage model
9. **Implement `Journal`** — log all decisions and fills to SQLite or JSONL for analysis
10. **Paper trade 30+ days** — validate expectancy, fix signal quality, tune thresholds
11. **Live trading** — only after step 10 shows consistent positive expectancy

---

*Update this file whenever project assumptions, architecture, or scope materially change.*
