# BIT — 30-Day Paper Trading Runbook

Operator runbook for the v1 paper trading validation run.
This document is grounded in the repo as it exists today — no speculative infrastructure.

---

## 1. Scope and Purpose

### What this run validates

- The full pipeline cycles without crashing: market data → features → signals → decisions → journal
- Signal generation is realistic: not all-REJECT and not all-ENTER across 30 days
- Paper fills are recorded with correct fee and slippage simulation
- Portfolio state persists across container restarts without corruption
- The runner loop is observable: heartbeat freshness, health status, error isolation
- The dashboard accurately reflects live system state

### What is intentionally out of scope

- **Live order placement** — `ExecutionEngine._live_execute()` raises `NotImplementedError`. This is enforced, not optional.
- **Live unrealized PnL** — no continuous mark price feed; unrealized PnL shows `N/A`
- **Parameter optimization** — thresholds are not to be changed during the run unless a bug is found
- **New strategies** — v1 runs TrendContinuation and BreakoutConfirmation only
- **Risk model changes** — position sizing, max positions, and drawdown limits are fixed

### What "success" is not

A 30-day paper trading run is not a profit target. It is a **data collection and reliability validation** phase. PnL is not the criterion. Signal quality, operational continuity, and honest error accounting are.

---

## 2. Pre-Flight Checklist

Complete all items before starting the 30-day run. Do not skip items.

### 2.1 Environment

- [ ] `.env` file created from `.env.example`
- [ ] `PAPER_TRADING=true` confirmed (do not change)
- [ ] `BYBIT_TESTNET` set correctly (`true` for testnet, `false` for mainnet data)
- [ ] `CAPITAL_USDT` set to intended starting capital (default `500`)
- [ ] `RUN_INTERVAL_SECONDS` set (default `300` — matches the 5m candle cadence)
- [ ] `BYBIT_API_KEY` and `BYBIT_API_SECRET` either set (for klines/ticker) or intentionally blank

> Note: Paper trading works without credentials using public Bybit endpoints. Credentials are only required for authenticated endpoints. The system logs which mode it starts in.

### 2.2 Docker

- [ ] Docker ≥ 24 installed: `docker --version`
- [ ] Compose v2 available: `docker compose version` (not `docker-compose`)
- [ ] No leftover volumes from a previous run unless continuing intentionally: `docker volume ls | grep bit`
- [ ] Image builds cleanly: `docker compose build` (no errors)

### 2.3 Data directory

On first run, the data volume is empty. That is expected — the runner creates files.

If resuming a previous run:
- [ ] Confirm `runner_state.json` is not in `ERROR` state before restarting
- [ ] Confirm `portfolio_state.json` is not corrupt (will cause runner to exit immediately)

Inspect volume contents:
```bash
docker compose run --rm bot ls -la /app/data/
```

### 2.4 Readiness confirmation before starting

After first start, confirm all of the following before leaving the system unattended.

| Check | Command | Expected |
|---|---|---|
| Both containers running | `docker compose ps` | `running`, not `exited` |
| Bot container healthy | `docker compose ps` | health `healthy` (after ~1 min) |
| Dashboard reachable | `curl -s http://localhost:8765/health` | `{"status":"ok"}` |
| Credential check result | Dashboard → Readiness panel | `READY` (ok) or `WARNING` (skipped) — not `MISSING` (failed) |
| Runner loop running | Dashboard → Header | `Loop: running` |
| Journal growing | Wait 10 min, then check | Journal entry count > 0 |

---

## 3. Start Procedure

### 3.1 Start all services

```bash
docker compose up --build -d
```

`--build` rebuilds the image if source has changed. `-d` detaches (runs in background).

### 3.2 Confirm startup

```bash
# Watch startup logs for both services
docker compose logs -f

# Or per service
docker compose logs -f bot
docker compose logs -f dashboard
```

Expected in bot logs (within 60s of start):

```
BIT runner starting (paper=True, symbols=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'], interval=300s)
Credential check: ok — ...     # OR: skipped — No API credentials configured
BIT runner started.
```

If you see `Startup validation failed`, check the credential check section in §5.

### 3.3 Confirm container health

```bash
docker compose ps
```

After 60–90 seconds, both containers should show `(healthy)`.

If not healthy after 2 minutes, inspect:
```bash
docker inspect bit-bot-1 --format '{{json .State.Health}}' | python -m json.tool
```

### 3.4 Confirm dashboard

Open `http://localhost:8765` in a browser.

Verify:
- Mode shows `PAPER`
- Symbols list shows `BTCUSDT`, `ETHUSDT`, `SOLUSDT`
- Loop shows `running` (green)
- Readiness panel shows no red `MISSING` items (except scheduler will show READY once running)
- Health panel shows `Scheduler / Loop: IMPLEMENTED`

### 3.5 Confirm first journal entry

Wait one full cycle (default: 5 minutes), then check:

```bash
docker compose exec bot wc -l /app/data/journal.jsonl
```

Should show ≥ 3 lines (one per symbol per cycle). If zero after 10 minutes, see §5.6.

---

## 4. Daily Operations Checklist

Run this every day during the 30-day period. It takes 5 minutes.

### 4.1 Container health

```bash
docker compose ps
```

Expected: both services `running (healthy)`. If either is `unhealthy` or `exited`, see §5.

### 4.2 Runner heartbeat freshness

```bash
docker compose exec bot python -m bit.healthcheck
```

Expected output: `HEALTHY: runner is running`

Also check via dashboard: Header → `Loop: running`, `Last run: <timestamp within last 10 min>`

### 4.3 Journal growth

```bash
docker compose exec bot wc -l /app/data/journal.jsonl
```

Compare to yesterday's count. Should grow by at least `3 × (86400 / RUN_INTERVAL_SECONDS)` lines per day (3 symbols × cycles per day). For `RUN_INTERVAL_SECONDS=300`, that's ≥ 864 lines/day.

Spot-check last few entries:
```bash
docker compose exec bot tail -3 /app/data/journal.jsonl | python -m json.tool
```

Verify: `decision_state` is one of `ENTER`, `MONITOR`, `REJECT`. Not all the same value every time.

### 4.4 Fill / signal count

Check dashboard → Recent Fills and Recent Decisions panels.

In paper trading at conservative thresholds (enter ≥ 0.65):
- Expect mostly `MONITOR` and `REJECT`
- `ENTER` signals should be infrequent but non-zero over 30 days
- If zero `ENTER` across a full week: review signal scores in journal (may indicate data or threshold issue)

### 4.5 Persistence files

```bash
docker compose exec bot ls -la /app/data/
```

Expected:
- `journal.jsonl` — modification time matches most recent cycle
- `runner_state.json` — modification time within last `RUN_INTERVAL_SECONDS` seconds
- `portfolio_state.json` — present once at least one fill has been recorded

If `portfolio_state.json` is absent after fills have occurred, check runner logs for save errors.

### 4.6 Dashboard readiness panel

Open `http://localhost:8765` and check the Readiness panel. Target state:

| Item | Expected status |
|---|---|
| Config loaded | READY |
| Portfolio tracker | WARNING (in-memory is expected) |
| Portfolio state | READY (after first fill) or WARNING (no fills yet) |
| Journal writable | READY |
| API key | WARNING (present, not re-verified) or MISSING (if no key — acceptable for paper) |
| Credential check | READY (ok) or WARNING (skipped — no credentials) |
| BotRunner | READY |
| Market connectivity | WARNING (assumed, not continuously verified) |
| Journal data | READY (once entries exist) |
| Docker | READY |

Any red `MISSING` items that were not there at startup require investigation.

### 4.7 PnL sanity check

Dashboard → Portfolio section:
- `Available USDT` should decrease as positions open (capital deployed)
- `Realized PnL` should accumulate as positions close (SELL fills recorded)
- `Open positions` count should be 0–3 (per `MAX_OPEN_POSITIONS=3` default)

> Unrealized PnL is `N/A` — this is expected. Mark prices are not continuously fetched in v1.

### 4.8 Error rate check

```bash
docker compose logs --since 24h bot | grep -i "error\|exception\|critical" | tail -20
```

Isolated per-symbol errors (network timeout on one symbol while others succeed) are acceptable — the runner isolates them. Repeated errors on the same symbol, or errors that increment `last_error` in runner state without recovery, require investigation.

---

## 5. Incident Handling

### 5.1 Credential validation failure

**Symptom:** Bot container exits immediately. Logs show `Startup validation failed: Credential check failed`.

**Cause:** `BYBIT_API_KEY` or `BYBIT_API_SECRET` is set but invalid.

**Fix:**
1. Check the key and secret in `.env`
2. Either correct them or remove them (paper trading works without credentials)
3. `docker compose up -d bot`

If you intentionally want to run without credentials:
```bash
# In .env:
BYBIT_API_KEY=
BYBIT_API_SECRET=
```

### 5.2 Dashboard unhealthy

**Symptom:** `docker compose ps` shows dashboard as `unhealthy` or `(health: starting)` for > 2 minutes.

**Check:**
```bash
docker compose logs dashboard | tail -20
curl -s http://localhost:8765/health
```

If curl works, the healthcheck timing is off — investigate with:
```bash
docker inspect bit-dashboard-1 --format '{{json .State.Health.Log}}' | python -m json.tool
```

If curl fails, the FastAPI process crashed. Check logs and restart:
```bash
docker compose restart dashboard
```

### 5.3 Runner unhealthy or stale

**Symptom:** `python -m bit.healthcheck` returns exit code 1, or bot container shows `unhealthy`.

**Diagnose:**
```bash
docker compose exec bot python -m bit.healthcheck
docker compose logs --tail 50 bot
```

**If status is `stopped` or `error`:** The runner hit a fatal error and stopped. Check logs, fix root cause, restart:
```bash
docker compose restart bot
```

**If status is `running` but stale:** Heartbeat is not updating. The loop may be hung on a slow API call. Restart the container:
```bash
docker compose restart bot
```

**If the loop restarts but immediately exits again:** There is a persistent startup failure. Check the `startup_error` field via dashboard → Runner State section.

### 5.4 Corrupt portfolio state

**Symptom:** Bot container exits with `CRITICAL: Portfolio state file is corrupt`. Logged as:
```
Portfolio state file is corrupt: <error>
Inspect or delete data/portfolio_state.json to start fresh.
```

**Option A — Inspect and recover:**
```bash
docker compose exec bot cat /app/data/portfolio_state.json
```
If the JSON is repairable, edit it directly.

**Option B — Start fresh (loses fill history in state, journal is unaffected):**
```bash
docker compose exec bot rm /app/data/portfolio_state.json
docker compose restart bot
```
The journal (`journal.jsonl`) is unaffected — all fill history is preserved there.

### 5.5 Corrupt runner state

**Symptom:** `python -m bit.healthcheck` reports `runner state file is corrupt`.

**Fix:** The runner rewrites this file on every startup. Simply restart:
```bash
docker compose restart bot
```

If restart fails due to corrupt state file, delete it:
```bash
docker compose exec bot rm /app/data/runner_state.json
docker compose restart bot
```

### 5.6 No journal growth

**Symptom:** `wc -l /app/data/journal.jsonl` is not increasing.

**Diagnose:**
```bash
docker compose logs --tail 50 bot
docker compose exec bot python -m bit.healthcheck
```

Possible causes:
- Runner is in `ERROR` or `STOPPED` state → restart bot
- Pipeline is raising unhandled exceptions for all symbols → check logs for repeated errors
- `RUN_INTERVAL_SECONDS` is very large → check config
- Journal path is not writable → check `Journal writable: READY` in dashboard readiness panel

### 5.7 No signals for an extended period

**Symptom:** All journal entries show `REJECT`, composite scores near 0, for multiple consecutive days.

**This may be normal** — the bot is conservative by design. However:

1. Verify market data is actually flowing:
   ```bash
   docker compose logs --tail 20 bot | grep "Pipeline"
   ```
2. Check that composite scores in journal are not uniformly 0.0 (which would suggest a feature computation issue, not just a weak market regime)
3. Check signal scores per strategy in the Recent Decisions dashboard panel
4. Do not change thresholds during the 30-day run — note the observation and assess at the weekly review

### 5.8 API / network failure

**Symptom:** Logs show `BybitNetworkError` or `httpx.TimeoutException` repeatedly.

**Expected behavior:** Per-symbol pipeline errors are caught and isolated. The runner writes `last_error` to `runner_state.json` and continues to the next symbol and the next cycle. This is not a fatal error.

**Confirm isolation is working:**
```bash
docker compose logs --tail 30 bot | grep "Pipeline error"
# Should show error for specific symbol, not crash of the whole runner
```

If errors persist for > 1 hour on all symbols, check network connectivity and Bybit status.

---

## 6. Weekly Review Checklist

Run at the end of each week (days 7, 14, 21, 28).

### 6.1 Signal distribution

```bash
docker compose exec bot python -c "
import json, collections
entries = [json.loads(l) for l in open('/app/data/journal.jsonl')]
states = collections.Counter(e['decision_state'] for e in entries)
print('Decision distribution:', dict(states))
print('Total entries:', len(entries))
print('Fills:', sum(1 for e in entries if e.get('fill_price')))
"
```

Expected: REJECT > MONITOR >> ENTER. All-REJECT with zero ENTER over a full week is a signal quality concern.

### 6.2 Per-symbol activity

```bash
docker compose exec bot python -c "
import json, collections
entries = [json.loads(l) for l in open('/app/data/journal.jsonl')]
by_symbol = collections.Counter(e['symbol'] for e in entries)
print('Entries per symbol:', dict(by_symbol))
enters = [e for e in entries if e['decision_state'] == 'ENTER']
enters_by_symbol = collections.Counter(e['symbol'] for e in enters)
print('Fills per symbol:', dict(enters_by_symbol))
"
```

Verify all three symbols are receiving cycles (not just one dominating).

### 6.3 Score distribution

```bash
docker compose exec bot python -c "
import json
entries = [json.loads(l) for l in open('/app/data/journal.jsonl')]
scores = [float(e['composite_score']) for e in entries]
n = len(scores)
if n:
    print(f'Scores: min={min(scores):.3f}, max={max(scores):.3f}, avg={sum(scores)/n:.3f}')
    above_monitor = sum(1 for s in scores if s >= 0.40)
    above_enter = sum(1 for s in scores if s >= 0.65)
    print(f'Above monitor threshold (0.40): {above_monitor}/{n} ({100*above_monitor/n:.1f}%)')
    print(f'Above enter threshold (0.65):   {above_enter}/{n} ({100*above_enter/n:.1f}%)')
"
```

### 6.4 PnL and drawdown

Check dashboard → Portfolio:
- Realized PnL trend (positive, negative, flat)
- Number of open positions over time
- Available USDT (capital deployment ratio)

There is no automated drawdown alert in v1. Manually check: if `available_usdt` drops below `capital_usdt × (1 - max_drawdown_pct)` (default: below 450 USDT on 500 capital), the bot will begin rejecting new entries due to the drawdown guard in `RiskEngine`.

### 6.5 Persistence continuity

If the system was restarted during the week:
```bash
docker compose logs bot | grep "Portfolio restored\|starting fresh"
```

Expected: `Portfolio restored from data/portfolio_state.json` (not "starting fresh") if fills had been recorded before the restart.

### 6.6 Error profile

```bash
docker compose logs --since 168h bot | grep -c "Pipeline error"
docker compose logs --since 168h bot | grep -c "ERROR\|CRITICAL"
```

Low single-digit pipeline errors per week (network timeouts) are acceptable. Dozens per day indicate a systemic issue.

---

## 7. 30-Day Success Criteria

At the end of day 30, assess against all of the following. Every item must pass before the run is considered complete.

### Operational reliability

- [ ] Bot container was healthy for ≥ 95% of the run period (< 36 hours cumulative downtime)
- [ ] No unrecovered crashes — all incidents were detected and resolved
- [ ] Docker restart policy (`unless-stopped`) caught automatic restarts where needed
- [ ] All incidents were documented with cause and resolution

### Data integrity

- [ ] `journal.jsonl` is intact and parseable end-to-end: `python -c "import json; [json.loads(l) for l in open('journal.jsonl')]"`
- [ ] Entry count is consistent with expected cycles: ≈ `3 symbols × 8640 cycles/30 days` = ~25,920 entries (at 5m interval)
- [ ] No gaps > 30 minutes in cycle timestamps (outside of documented downtime)
- [ ] `portfolio_state.json` matches journal fill history at end of run

### Signal quality

- [ ] At least one `ENTER` signal was generated per symbol over the 30 days
- [ ] Composite scores vary over time — not locked to 0.0 or 1.0
- [ ] Both TrendContinuation and BreakoutConfirmation strategies contributed at least one non-zero score at some point
- [ ] ENTER rate is between 0.5% and 20% of all cycles (pure guesses at extremes are not useful data)

### Paper execution

- [ ] All paper fills have realistic fill prices (within 0.1% of ticker at cycle time)
- [ ] Fee and slippage are applied: `fee_usdt > 0` on all fills
- [ ] No fills exceed `max_position_pct` of available capital

### Error profile

- [ ] Zero `CRITICAL` errors in bot logs (corrupt state, startup failures) after initial setup
- [ ] Per-symbol network errors do not exceed 5% of cycles for any single symbol
- [ ] No repeated `last_error` pattern for > 1 hour without recovery

### Persistence

- [ ] At least one container restart was performed during the run and the portfolio state was restored correctly
- [ ] `portfolio_state.json` exists and is valid at end of run

---

## 8. Next-Phase Gate

### What must be true before considering live mode

All 30-day success criteria in §7 must be met, plus:

1. **Positive or near-zero paper expectancy** — at least a weak positive edge demonstrated. Running at a consistent large loss is a signal quality problem, not a configuration problem.
2. **Strategy attribution** — know which strategy (TrendContinuation vs BreakoutConfirmation) contributed which fills. Do not proceed with a black-box result.
3. **Threshold review** — `enter_threshold` and `monitor_threshold` reviewed against the score distribution. If zero fills occurred because the threshold is too high, lower it, re-run, and restart the 30-day clock.
4. **Bybit credentials confirmed valid** — credential check shows `ok` at startup, not `skipped`.
5. **Instrument constraints confirmed** — no fills were rejected by `RiskEngine` due to qty_step or min_notional violations.

### What remains unfinished in the system

These gaps exist today and are known. They do not block paper trading but must be addressed before live:

| Gap | Impact on paper trading | Required for live |
|---|---|---|
| No live mark prices | Unrealized PnL is N/A | Not blocking for accounting |
| `ExecutionEngine._live_execute()` raises NotImplementedError | Live orders impossible | Must implement |
| No order status polling | N/A for paper | Required for live |
| No position reconciliation against exchange | N/A for paper | Required for live |
| `get_orderbook_top()` / `get_recent_trades()` are stubs | Features using these are unavailable | Needed for some strategies |
| No regime detection | Strategies run in all conditions | Post-v1 improvement |
| No drawdown alert | Manual monitoring only | Recommended for live |

---

## Reference

| Resource | Location |
|---|---|
| Docker Compose setup and commands | `docs/deployment.md` |
| Dashboard sections and API | `docs/dashboard.md` |
| Architecture and service contracts | `docs/architecture.md` |
| Environment variable reference | `.env.example` |
| API snapshot (JSON) | `http://localhost:8765/api/snapshot` |
| Bot runner logs | `docker compose logs -f bot` |
| Dashboard logs | `docker compose logs -f dashboard` |
