# BIT — Crypto Trading Bot

A crypto-native, rule-based trading bot for Bybit spot markets. Built for transparency, auditability, and iterative validation before any live deployment.

## Status

> **Paper only.** The default strategy is a 50/50 blend. Live order placement is disabled. No API key is required or sent.

## Scope

| Attribute | Value |
|---|---|
| Exchange | Bybit spot, public market data |
| Symbols | BTCUSDT, ETHUSDT, SOLUSDT |
| Direction | Long-only |
| Mode | Paper trading. The process refuses to start otherwise |
| Starting bankroll | 1000 USDT (set `CAPITAL_USDT`) |
| Goal reported in the log | 2000 USDT |
| Default strategy | 50% daily core rotation + 50% 4h EMA trend |
| Previous strategy | `STRATEGY_MODE=legacy` (15m trend and breakout checklist) |

### What the blend does

Half of the bankroll is the **rotation sleeve**. Once a day, on the daily candle that closed at 00:00 UTC, it holds that sleeve in whichever of BTC, ETH or SOL had the highest 30-day return. If every return is zero or negative it holds cash. The fill is the next daily open.

The other half is the **conservative sleeve**, split into three sub-accounts that are not rebalanced into each other. Each sub-account is long its coin when the 20-period EMA is above the 100-period EMA on closed 4h candles, sized by `min(1, 0.40 / 30-day realized vol)`, and flat otherwise. Fills are the next 4h open.

Signals use closed candles only. Each fill models a 0.1% fee and 0.05% adverse slippage. The buy fee is included in the cost basis, so P&L is net of both sides. The bot only acts when it sees the new candle within 30 minutes of the open. A later restart keeps the existing position until the next close.

## Run the paper bot on Windows

From PowerShell, in the folder that contains this README:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .

copy .env.example .env
py -m bit.paper
```

Leave `BYBIT_API_KEY` and `BYBIT_API_SECRET` empty. The bot uses the public Bybit mainnet kline feed and will not place an order.

`py -m bit` does the same thing while `STRATEGY_MODE=blend` (the default). The old checklist is still available:

```powershell
# in .env
# STRATEGY_MODE=legacy
py -m bit
```

The log line to watch:

```text
goal progress equity=1000 USDT goal=2000 USDT progress=50.0% remaining=1000 USDT
```

State is written to `data\blend_state.json` after every cycle. Closing the PC and starting the bot again resumes the same positions and equity.

### How to stop it

In the bot's window, press `Ctrl+C`.

Or, from a second PowerShell window in the same folder, create the kill file. The bot exits on its next cycle and does not send any further paper orders. Positions stay as they are.

```powershell
New-Item -ItemType File -Force -Path data\STOP
```

Delete that file before you start the bot again:

```powershell
Remove-Item data\STOP
```

### If it halts on a loss

A drop to 75% of the stage's starting equity flattens the book and stays halted. Six consecutive losing round-trips also block new entries until you reset. A 3% loss versus the start of the UTC day blocks new entries until the next 00:00 UTC and clears itself.

Reset a latched halt (this does not put the lost money back; it starts a new stage from the current equity):

```powershell
py -m bit.paper --reset-halt
py -m bit.paper
```

## Re-run the backtest

The research backtester lives in `backtest/`. Cached Bybit klines are in `backtest/data/`. With the dev extras installed:

```powershell
py -m pip install -e ".[dev]"
py -m backtest.replay
py -m pytest tests/backtest tests/services/test_risk_guard.py -q
```

`py -m backtest.fetch_klines` refreshes the cache. The committed files are Binance spot candles in the same shape as Bybit klines, because Bybit's market-data API answers 403 from some networks. On that cache the blend's out-of-sample result sits next to report section 9 (about +20% CAGR, +36% total, -46% max drawdown, Sharpe 0.68). No key is used.

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

## Setup (macOS / Linux)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
```

Windows setup is in the section above. Do not put API keys in `.env` for the paper run.

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
