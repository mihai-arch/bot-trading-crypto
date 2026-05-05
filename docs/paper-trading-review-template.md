# BIT — Paper Trading Review Template

Operator-focused daily and weekly review for the 30-day paper trading run.
Each daily review should take 2–3 minutes. Copy a block per day/week.

Reference: `docs/paper-trading-runbook.md` for full incident procedures.

---

## Daily Review

Copy one block per day. Fill in values from the commands below.

```
## Day __  YYYY-MM-DD  HH:MM UTC

SYSTEM
  docker compose ps           bot=healthy  dash=healthy  [ ] [ ]
  bit.healthcheck             HEALTHY / UNHEALTHY: ___
  dashboard loop_running      true / false
  readiness MISSING items     none / ___

DATA
  journal count today         ___  (yesterday: ___)  delta: ___  (≥864/day at 5m)
  last decision states        ___  ___  ___  (not all REJECT / not all ENTER)
  last_error in runner state  none / ___

SIGNALS
  new fills (24h)             ___  (dashboard → Recent Fills)
  decision mix looks normal   Y / N

PORTFOLIO
  available USDT              ___  (started: 500, drawdown guard: <450)
  open positions              ___ / 3
  realized PnL                ___
  data_source                 live / persisted / null

ERRORS (24h)
  pipeline errors             ___  (acceptable: <15 total, no single-symbol repeats)
  other errors / crashes      ___

ACTION NEEDED                 Y / N  →
```

### Commands for daily review

```bash
docker compose ps
docker compose exec bot python -m bit.healthcheck
# dashboard: http://localhost:8765
docker compose exec bot wc -l /app/data/journal.jsonl
docker compose exec bot tail -3 /app/data/journal.jsonl | python -m json.tool
docker compose logs --since 24h bot | grep -c "Pipeline error"
docker compose logs --since 24h bot | grep -i "error\|critical" | tail -10
docker compose exec bot ls -la /app/data/
```

---

## Weekly Review

Run at end of days 7, 14, 21, 28.

```
## Week __  Day ___ → ___  (YYYY-MM-DD → YYYY-MM-DD)

SIGNAL DISTRIBUTION (run snippet below)
  ENTER: ___   MONITOR: ___   REJECT: ___   Total: ___
  ENTER rate: ___%  (target: 0.5–20%)
  Both strategies produced non-zero scores: Y / N

PER-SYMBOL ACTIVITY (run snippet below)
  BTCUSDT  cycles: ___  fills: ___
  ETHUSDT  cycles: ___  fills: ___
  SOLUSDT  cycles: ___  fills: ___
  All three symbols cycling: Y / N

SCORE DISTRIBUTION (run snippet below)
  min: ___  max: ___  avg: ___
  ≥0.40 (monitor threshold): ___%
  ≥0.65 (enter threshold):   ___%
  Scores varying (not locked to 0.0 or 1.0): Y / N

STRATEGY ACTIVITY (run snippet below)
  TrendContinuation non-zero scores: ___
  BreakoutConfirmation non-zero scores: ___

PNL / DRAWDOWN
  realized PnL cumulative: ___
  available USDT end of week: ___
  open positions end of week: ___
  drawdown guard triggered this week: Y / N

PERSISTENCE
  container restarts this week: ___
  portfolio restored correctly after restart: Y / N / not tested
  portfolio_state.json present and valid: Y / N

JOURNAL INTEGRITY
  total entries: ___  expected ~___ (days×288×3)
  no unexpected gap > 30 min: Y / N  (gaps if any: ___)

ERROR PROFILE
  pipeline errors total this week: ___
  same-symbol repeated errors (>1h): Y / N
  any CRITICAL in logs: Y / N

OBSERVATIONS / ANOMALIES


NEXT-WEEK WATCH ITEMS

```

### Commands for weekly review

```bash
# Signal distribution
docker compose exec bot python -c "
import json, collections
entries = [json.loads(l) for l in open('/app/data/journal.jsonl')]
states = collections.Counter(e['decision_state'] for e in entries)
print('States:', dict(states))
print('Total:', len(entries), '  Fills:', sum(1 for e in entries if e.get('fill_price')))
"

# Per-symbol
docker compose exec bot python -c "
import json, collections
entries = [json.loads(l) for l in open('/app/data/journal.jsonl')]
by_sym = collections.Counter(e['symbol'] for e in entries)
fills_sym = collections.Counter(e['symbol'] for e in entries if e.get('fill_price'))
print('Cycles:', dict(by_sym))
print('Fills:', dict(fills_sym))
"

# Score distribution
docker compose exec bot python -c "
import json
entries = [json.loads(l) for l in open('/app/data/journal.jsonl')]
scores = [float(e['composite_score']) for e in entries]
n = len(scores)
if n:
    print(f'min={min(scores):.3f}  max={max(scores):.3f}  avg={sum(scores)/n:.3f}')
    print(f'>=0.40: {sum(s>=0.40 for s in scores)/n*100:.1f}%  >=0.65: {sum(s>=0.65 for s in scores)/n*100:.1f}%')
"

# Strategy activity (non-zero scores per strategy)
docker compose exec bot python -c "
import json, collections
entries = [json.loads(l) for l in open('/app/data/journal.jsonl')]
by_strat = collections.defaultdict(int)
for e in entries:
    for k, v in (e.get('raw_signal_scores') or {}).items():
        if v > 0:
            by_strat[k] += 1
print('Non-zero signal counts:', dict(by_strat))
"

# Error profile
docker compose logs --since 168h bot | grep -c "Pipeline error"
docker compose logs --since 168h bot | grep -i "critical" | wc -l

# Journal gap check (timestamps sorted, look for gaps > 30 min)
docker compose exec bot python -c "
import json
from datetime import datetime, timezone, timedelta
ts = sorted(datetime.fromisoformat(json.loads(l)['cycle_timestamp']) for l in open('/app/data/journal.jsonl'))
gaps = [(ts[i+1]-ts[i], ts[i]) for i in range(len(ts)-1) if ts[i+1]-ts[i] > timedelta(minutes=30)]
print(f'Gaps >30min: {len(gaps)}')
for g, t in gaps[:5]:
    print(f'  {g} after {t.isoformat()}')
"
```

---

## Incident Log

Copy one block per incident.

```
## Incident  YYYY-MM-DD HH:MM UTC

Detected by:  dashboard / healthcheck / manual / logs
Impact:       loop stopped / fills missed / data loss / degraded / none

SYMPTOMS


TIMELINE
  HH:MM  first sign of problem
  HH:MM  investigated with ___
  HH:MM  root cause identified
  HH:MM  fix applied
  HH:MM  confirmed recovered

ROOT CAUSE


RESOLUTION (commands run)
  $ ___

RECOVERY STATE
  bot container:          healthy / restarted / rebuilt
  journal count before/after: ___ / ___
  portfolio state:        intact / reset / recovered from file
  runner state:           running / clean restart
  data loss:              none / ___

FOLLOW-UP / PREVENTION

```

---

## 30-Day Evaluation Criteria

Assess at end of day 30. All items in each section must pass before the run is considered complete and the next phase can be considered.

### Operational reliability

| # | Criterion | Pass? | Notes |
|---|---|---|---|
| R1 | Bot healthy ≥ 95% of run period (<36h cumulative downtime) | | |
| R2 | No unrecovered crashes — all incidents resolved | | |
| R3 | Docker restart policy caught automatic restarts where needed | | |
| R4 | All incidents logged with cause and resolution | | |

### Data integrity

| # | Criterion | Pass? | Notes |
|---|---|---|---|
| D1 | `journal.jsonl` intact end-to-end (no parse errors) | | |
| D2 | Entry count ~25,920 (3 symbols × 8,640 cycles; adjust for downtime) | | |
| D3 | No unexplained gaps > 30 min in cycle timestamps | | |
| D4 | `portfolio_state.json` matches journal fill history at end of run | | |

### Signal quality

| # | Criterion | Pass? | Notes |
|---|---|---|---|
| S1 | ≥1 ENTER per symbol over 30 days | | |
| S2 | ENTER rate 0.5%–20% of all cycles | | |
| S3 | Composite scores vary — not locked to 0.0 or 1.0 | | |
| S4 | Both TrendContinuation and BreakoutConfirmation produced non-zero scores | | |

### Paper execution

| # | Criterion | Pass? | Notes |
|---|---|---|---|
| E1 | Fill prices within 0.1% of ticker at cycle time | | |
| E2 | `fee_usdt > 0` on all fills (fee and slippage applied) | | |
| E3 | No fills exceeded `max_position_pct` of available capital | | |

### Error profile

| # | Criterion | Pass? | Notes |
|---|---|---|---|
| X1 | Zero CRITICAL errors after initial setup | | |
| X2 | Per-symbol pipeline errors < 5% of cycles for any single symbol | | |
| X3 | No `last_error` pattern persisting > 1 hour without recovery | | |

### Persistence

| # | Criterion | Pass? | Notes |
|---|---|---|---|
| P1 | ≥1 container restart performed and portfolio state restored correctly | | |
| P2 | `portfolio_state.json` present and valid at end of run | | |

### Next-phase gate (additional)

| # | Criterion | Pass? | Notes |
|---|---|---|---|
| G1 | Strategy attribution known — which strategy drove which fills | | |
| G2 | `enter_threshold` reviewed against score distribution (not too tight) | | |
| G3 | Bybit credentials confirmed valid (`credential_check = ok`) | | |
| G4 | No instrument constraint violations (qty_step / min_notional rejections) | | |
| G5 | Paper expectancy not strongly negative (review before live mode) | | |

---

## Quick-Reference Commands

```bash
# Health
docker compose ps
docker compose exec bot python -m bit.healthcheck

# Data volume
docker compose exec bot ls -la /app/data/

# Live bot logs
docker compose logs -f bot

# Last N journal entries
docker compose exec bot tail -N /app/data/journal.jsonl | python -m json.tool

# Runner state
docker compose exec bot python -c "import json; print(json.dumps(json.load(open('/app/data/runner_state.json')), indent=2))"

# Restart bot only
docker compose restart bot

# API snapshot (JSON)
curl -s http://localhost:8765/api/snapshot | python -m json.tool
```
