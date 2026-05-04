# BIT — Local Deployment Guide

Local paper trading deployment using Docker Compose.
No cloud, no Kubernetes, no reverse proxy — just two containers sharing a volume.

---

## Prerequisites

- Docker ≥ 24 and Docker Compose v2 (`docker compose`, not `docker-compose`)
- A Bybit API key (optional — paper trading works on public endpoints)

---

## Quick Start

```bash
# 1. Copy config and fill in credentials (optional for paper trading)
cp .env.example .env

# 2. Build and start both services
docker compose up --build

# 3. Open the dashboard
open http://localhost:8765
```

Stop everything cleanly:

```bash
docker compose down
```

---

## Services

| Service | Command | Port | Purpose |
|---|---|---|---|
| `bot` | `python -m bit` | — | BotRunner pipeline loop |
| `dashboard` | `uvicorn bit.dashboard.app:app` | 8765 | Operator dashboard |

Both services use the **same image** (built once from `Dockerfile`).

---

## Persistent Data

Both containers mount the named volume `bit_data` at `/app/data`:

```
/app/data/
  journal.jsonl          ← pipeline decisions and fills (append-only)
  portfolio_state.json   ← paper portfolio (cash, positions, realized PnL)
  runner_state.json      ← runner lifecycle + heartbeat
```

Data survives `docker compose down / up`. To start completely fresh:

```bash
docker volume rm bit_bit_data
```

> Warning: this erases all journal history, portfolio state, and runner state.

---

## Health Checks

### Bot container

Runs `python -m bit.healthcheck`. Reads `runner_state.json` and reports:

| Condition | Status |
|---|---|
| File missing | UNHEALTHY |
| File corrupt | UNHEALTHY |
| `status != "running"` | UNHEALTHY |
| File not updated in `run_interval_seconds × 3` seconds | UNHEALTHY |
| `status == "running"` and file is fresh | HEALTHY |

Default interval: 60 s, 3 retries, 45 s start period.
Default stale threshold: 300 s × 3 = 900 s (15 minutes).

Check container health:

```bash
docker compose ps
docker inspect bit-bot-1 --format '{{.State.Health.Status}}'
```

### Dashboard container

Runs `urllib.request.urlopen('http://localhost:8765/health')`.
Healthy if `/health` returns HTTP 200. No curl needed.

Default interval: 30 s, 3 retries, 15 s start period.

---

## Common Commands

```bash
# View live logs
docker compose logs -f bot
docker compose logs -f dashboard

# Rebuild after code changes
docker compose up --build

# Run a single service
docker compose up dashboard

# Open a shell in the bot container (for debugging)
docker compose exec bot bash

# Check what's in the data volume
docker compose exec bot ls -la /app/data/

# Restart only the bot runner
docker compose restart bot
```

---

## Environment Variables

All variables from `.env` are injected into both containers via `env_file: .env`.

Key variables:

| Variable | Default | Notes |
|---|---|---|
| `PAPER_TRADING` | `true` | Must stay `true` in v1 |
| `BYBIT_API_KEY` | `""` | Optional for paper trading |
| `BYBIT_API_SECRET` | `""` | Optional for paper trading |
| `BYBIT_TESTNET` | `true` | Use testnet API endpoints |
| `CAPITAL_USDT` | `500` | Starting paper capital |
| `RUN_INTERVAL_SECONDS` | `300` | Seconds between pipeline cycles |

See `.env.example` for the full list.

---

## Startup Sequence

1. `bot` container starts: loads config, restores portfolio from disk (if file exists)
2. BotRunner enters `STARTING` state, writes `runner_state.json`
3. If credentials are configured: calls `GET /v5/user/query-api` to validate them
   - `ok` → runner enters `RUNNING`, credential status written to `runner_state.json`
   - `failed` → runner enters `ERROR`, container exits, Docker restarts it (`unless-stopped`)
   - `skipped` → no credentials; paper mode uses public endpoints only
4. Runner loop begins: one pipeline cycle per symbol every `RUN_INTERVAL_SECONDS`
5. `dashboard` container starts (depends on `bot`), reads journal and runner state files
6. Dashboard shows `loop_running: true` once runner state is fresh and shows `running`

---

## What "Healthy" Means

A healthy bot container = the pipeline is actively cycling.

The healthcheck is **not** a liveness probe (process alive) but a **readiness probe**
(are trades actually being evaluated?). If the runner stalls for more than 15 minutes
without writing a heartbeat, Docker marks the container unhealthy.

This design means:
- A running-but-hung process is correctly flagged as unhealthy
- A recently-started process is given 45 s before the first check
- Three consecutive missed checks trigger unhealthy status

---

## Constraints

- No live trading — `ExecutionEngine._live_execute()` raises `NotImplementedError` in v1
- Port `8765` is bound to `127.0.0.1` only — not exposed to the network
- No database — all state is in flat files on the named volume
- No reverse proxy — for LAN/local access only

---

## Running the 30-Day Paper Trading Validation

See `docs/paper-trading-runbook.md` for the pre-flight checklist, daily operations,
incident handling, weekly review, and success criteria.
