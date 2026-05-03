# BIT — Crypto Trading Bot

A crypto-native, rule-based trading bot for Bybit spot markets. Built for transparency, auditability, and iterative validation before any live deployment.

## Status

> **v1 foundation — paper trading, no live orders**

## Scope (v1)

| Attribute | Value |
|---|---|
| Exchange | Bybit spot |
| Symbols | BTCUSDT, ETHUSDT, SOLUSDT |
| Direction | Long-only |
| Mode | Paper trading (default) |
| Capital | 500 USDT (assumption) |
| Strategies | Trend continuation, Breakout confirmation |

## Architecture

```
MarketDataService
    → FeatureEngine
        → SignalEngine
            → DecisionEngine  ←→  RiskEngine
                → ExecutionEngine
                    → JournalLearningStore
```

Every decision produces one of three explicit states: `ENTER`, `MONITOR`, or `REJECT`. All decisions are logged with their rationale.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env — add API keys only when ready for live trading
```

## Running tests

```bash
pytest
```

## Key rules

- `paper_trading = true` is the default — live trading requires explicit opt-in
- No futures, leverage, shorting, or ML in v1
- Every ENTER decision is logged with score, strategy contributions, and rationale
- All secrets via environment variables

See `CLAUDE.md` for full project direction and engineering rules.
See `docs/architecture.md` for service responsibilities and data contracts.
See `CHECKLIST.md` for the next implementation steps.
