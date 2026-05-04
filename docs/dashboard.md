# BIT Dashboard

A local operator dashboard for monitoring the BIT trading bot during paper trading and live operation.

---

## How to Run

### Docker Compose (recommended)

```bash
cp .env.example .env   # fill in BYBIT_API_KEY, BYBIT_API_SECRET (optional for paper)
docker compose up
```

This starts two containers that share a named volume (`bit_data`):

| Container | Command | Purpose |
|---|---|---|
| `bot` | `python -m bit` | BotRunner — pipeline loop, writes to `data/` |
| `dashboard` | `uvicorn bit.dashboard.app:app` | Dashboard — reads `data/`, serves `http://localhost:8765` |

Stop cleanly: `docker compose down`

View logs: `docker compose logs -f bot` / `docker compose logs -f dashboard`

### Local (without Docker)

Install dependencies:

```bash
pip install -e ".[dev]"
```

Start the bot runner in one terminal:

```bash
python -m bit
```

Start the dashboard in another terminal:

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
| Mark prices / live prices | No continuous market data loop |
| Unrealized PnL on open positions | Requires live mark prices |
| API key validity (live check) | Checked only at runner startup; shown as WARNING until runner runs |
| Market data connectivity | Not verified continuously; assumed once credentials pass |

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

## v1 Persistence

BIT now persists two runtime state files to disk. Both use atomic writes (write to `.tmp` then rename) to prevent partial writes on crash.

### Portfolio state (`data/portfolio_state.json`)

Written by `PortfolioStateStore.save()` after each fill. Restores:
- Cash balance
- Open positions (symbol, quantity, average entry price)
- Realized PnL
- Last known mark prices (for unrealized PnL display after restart)

**On restart:**
```python
result = PortfolioStateStore.load(config.portfolio_state_path, starting_cash=config.capital_usdt)
if result.status == "ok":
    tracker = result.tracker  # positions and cash fully restored
elif result.status == "corrupt":
    raise RuntimeError(f"Portfolio state corrupt: {result.error}")
else:  # not_found — first run
    tracker = PaperPortfolioTracker(starting_cash=config.capital_usdt)
```

The dashboard surfaces persistence status:
- **READY** — state file present and valid
- **WARNING** — no file yet (first run, or no fills recorded)
- **MISSING** — file exists but cannot be parsed (inspect/delete to recover)

### Runner state (`data/runner_state.json`)

Written by the run loop (when implemented) on heartbeat, cycle start/end, and shutdown. Contains:
- `status` — running / stopped / starting / error
- `startup_validated` — whether startup checks passed
- `last_heartbeat`, `last_cycle_start`, `last_cycle_end`, `last_successful_cycle`
- `last_error`, `processed_symbols`

The dashboard reads this file at each snapshot, so it can show loop health even when the runner is in a separate process. State is considered **stale** if the file has not been updated in 120 seconds.

**`credential_check`** field records the startup credential validation result:
- `"ok"` — API key was validated against Bybit at runner startup
- `"skipped"` — no credentials configured; paper trading uses public endpoints
- `"failed: <message>"` — credentials were rejected; runner will not start
- `null` — runner has not yet started

### Limitations (v1)

- Portfolio state is saved **only when `PortfolioStateStore.save()` is called explicitly** after a fill. The tracker itself does not auto-save.
- No background persistence thread — saves are synchronous and caller-controlled.
- If the process crashes mid-fill (between `apply_fill` and `save`), the last fill may be lost. This is acceptable for v1 paper trading.
- Runner state is written by the runner, not the dashboard. The dashboard is read-only for both files.

---

## What Remains Before Continuous Paper Trading

1. ~~**Scheduler / run loop**~~ — **Done.** `BotRunner` in `bit.runner` runs the pipeline
   loop on a configurable interval. Start with `python -m bit` or `docker compose up bot`.

2. ~~**Portfolio state persistence**~~ — **Done.** `PortfolioStateStore` saves/restores
   positions, cash, realized PnL, and last mark prices to `data/portfolio_state.json`.

3. **Verified market data connection** — Start the runner with a real API key; credentials
   are validated at startup via `GET /v5/user/query-api`. Confirm `get_klines()` /
   `get_ticker()` return real data in the journal.

4. ~~**Process supervision heartbeat**~~ — **Done.** `RunnerStateStore` writes loop status to
   `data/runner_state.json` on every heartbeat and cycle. The dashboard reads this file and
   marks state stale after 120 seconds without an update.

5. ~~**Docker**~~ — **Done.** `docker-compose.yml` defines `bot` and `dashboard` services
   with shared named volume, healthchecks, and `restart: unless-stopped`.

### Container Health Checks

**Bot container** (`python -m bit.healthcheck`):
Reads `runner_state.json`. Healthy if `status == "running"` and file is newer than
`run_interval_seconds × 3` seconds. Exits 1 if file is missing, corrupt, stalled, or shows
an error/stopped status.

**Dashboard container** (`http://localhost:8765/health`):
Simple HTTP check — healthy if `/health` returns HTTP 200.

### Authenticated Startup Validation

When `BYBIT_API_KEY` and `BYBIT_API_SECRET` are set, the runner calls `GET /v5/user/query-api`
at startup before the first pipeline cycle. The result is written to `RunnerState.credential_check`:

- `"ok"` — key is valid; runner continues normally
- `"failed: ..."` — key rejected by Bybit; runner stops with an error
- `"skipped"` — no credentials; paper trading uses public endpoints only

If credentials are absent but paper trading is enabled, the runner still starts (public endpoints
are sufficient for klines and ticker data).

### Out of Scope in v1

- Live (non-paper) trading — `ExecutionEngine._live_execute()` raises `NotImplementedError`
- Continuous live mark prices for unrealized PnL — requires a WebSocket ticker feed
- Autonomous ML optimization — no ML models in v1

---

## Paper Trading Operations

For the 30-day validation run — pre-flight checklist, daily operations, incident
handling, weekly review, and success criteria — see `docs/paper-trading-runbook.md`.

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
