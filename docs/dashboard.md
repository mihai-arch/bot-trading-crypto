# BIT Dashboard

A local operator dashboard for monitoring the BIT trading bot during paper trading and live operation.

---

## How to Run

Install dashboard dependencies:

```bash
pip install -e ".[dev]"
```

Start the dashboard server:

```bash
python -m bit.dashboard
```

Or via uvicorn directly:

```bash
uvicorn bit.dashboard.app:app --host 127.0.0.1 --port 8765
```

Open in a browser: `http://127.0.0.1:8765`

The dashboard auto-refreshes every 10 seconds by reloading the page.
The `/api/snapshot` endpoint returns the same data as JSON for scripting or polling.

---

## What Data Is Real vs Unavailable

### Real (reads actual project state)

| Data | Source |
|---|---|
| Trading mode (PAPER / LIVE) | `BITConfig.paper_trading` |
| All risk thresholds and config | `BITConfig` |
| Historical decisions (ENTER / MONITOR / REJECT) | `JournalLearningStore.read_all()` — reads `data/journal.jsonl` |
| Historical fills (price, qty, fee) | Same journal — `JournalEntry.fill_price` / `fill_qty` / `fee_usdt` |
| Cash balance | `PaperPortfolioTracker.cash` |
| Open positions (symbol, qty, avg entry) | `PaperPortfolioTracker.snapshot()` |
| Realized PnL | `PaperPortfolioTracker.realized_pnl` |
| Journal entry count | `JournalLearningStore.entry_count()` |
| API key presence (not validity) | `BITConfig.bybit_api_key != ""` |

### Unavailable in v1 (shown explicitly as N/A or MISSING)

| Data | Reason |
|---|---|
| Mark prices / live prices | No market data loop running |
| Unrealized PnL on open positions | Requires live mark prices |
| Loop / scheduler status | No scheduler exists yet |
| Last heartbeat timestamp | No scheduler exists yet |
| Docker / container status | No docker-compose.yml yet |
| API key validity | No live verification call made |
| Market data connectivity | Not verified at runtime |

The dashboard **never fakes data**. If something is unavailable, it is shown as `N/A`,
`UNAVAILABLE`, or `MISSING` with an explanation. No placeholder values.

---

## Dependency Injection — Shared PaperPortfolioTracker

The `PaperPortfolioTracker` is in-memory only. When the pipeline and the dashboard
run in the same process, they must share the **same tracker instance** — otherwise the
dashboard sees an empty portfolio.

### Standalone dashboard (separate process, read-only)

The default app created by `uvicorn bit.dashboard.app:app` does NOT have access to
the pipeline's in-memory tracker. The portfolio section will show `NOT INJECTED — N/A`.
Decisions and fills are still readable from the journal file on disk.

### Integrated (same process as pipeline)

Use `create_app()` and pass the shared tracker:

```python
from bit.config import BITConfig
from bit.services.journal import JournalLearningStore
from bit.services.paper_portfolio import PaperPortfolioTracker
from bit.dashboard.app import create_app

config = BITConfig()
journal = JournalLearningStore(config)
portfolio = PaperPortfolioTracker(starting_cash=config.capital_usdt)

# Pass portfolio to the dashboard so it reads live in-memory state.
app = create_app(config=config, journal=journal, portfolio=portfolio)

# Then use the same `portfolio` instance when constructing Pipeline.
```

When the bot and dashboard run in the same process (e.g., via `asyncio`), the portfolio
section shows live state. When run as a separate process, the portfolio section shows N/A
but the journal-sourced data (decisions, fills) is always current.

---

## Dashboard Sections

| Section | Data source | Live in v1? |
|---|---|---|
| Header | Config, journal count | Yes |
| Portfolio / Balance | PaperPortfolioTracker | Only if injected |
| Risk Config | BITConfig | Yes |
| Health | Static structural probes | Yes (structural, not live connectivity) |
| Open Positions | PaperPortfolioTracker | Only if injected |
| Recent Decisions | Journal JSONL | Yes (reads file) |
| Recent Fills | Journal JSONL | Yes (reads file) |
| Runtime Gaps | Known gaps list | Yes (always honest) |
| Paper Trading Readiness | Evaluated checklist | Yes |

---

## What "Readiness" Means in This Project

The **Readiness** panel answers: "Can the bot run paper trading continuously right now?"

It distinguishes between:

- **READY (✓)** — this item is confirmed functional
- **WARNING (~)** — present but with a known limitation (e.g., in-memory only, not verified)
- **MISSING (✗)** — this item is absent and blocks continuous paper trading

Readiness is **operational**, not structural. A service can be fully implemented
(health = IMPLEMENTED) but still show as WARNING in readiness if it hasn't been
verified against a live exchange.

The **Health** panel answers a different question: "Is this service coded and structurally available?"
It reflects the codebase state, not runtime connectivity.

---

## What Remains Before Continuous Paper Trading

1. **Scheduler / run loop** — Add a `Runner` that calls `pipeline.run(symbol)` on a
   configurable interval for all symbols. This is the most critical blocker.

2. **Portfolio state persistence** — `PaperPortfolioTracker` resets on restart.
   Add a JSON sidecar file that saves/restores state between runs.

3. **Verified market data connection** — Start the loop with a real API key and confirm
   `get_klines()` / `get_ticker()` return real data.

4. **Process supervision** — The dashboard cannot tell if the bot loop has stalled.
   A heartbeat file or a shared timestamp is needed.

5. **Docker (optional but recommended)** — For production paper trading, a
   `docker-compose.yml` with the bot and dashboard as separate services.

---

## API

### `GET /`
Returns the dashboard HTML page.

### `GET /api/snapshot`
Returns the full `DashboardSnapshot` as JSON. Same data as the HTML page.

```json
{
  "mode": "PAPER",
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "as_of": "2026-05-02T09:14:33Z",
  "last_journal_write": null,
  "last_pipeline_run": null,
  "loop_running": false,
  "journal_entry_count": 0,
  "portfolio": null,
  "risk_config": { ... },
  "open_positions": [],
  "recent_decisions": [],
  "recent_fills": [],
  "health": [ ... ],
  "readiness": [ ... ],
  "runtime_gaps": [ ... ]
}
```

---

## Running Tests

```bash
pytest tests/dashboard/ -v
```
