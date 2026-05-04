"""
BIT runner container health check.

Called by the Docker HEALTHCHECK instruction to decide whether the bot
container is healthy.

Healthy means:
  1. runner_state.json exists and was parsed successfully (status = "ok")
  2. runner status is "running"
  3. The file was written within the staleness window

Unhealthy means any other state: missing file, corrupt file, wrong status,
or a stale heartbeat (runner is silent for too long).

Exit codes:
    0 — healthy
    1 — unhealthy

Usage:
    python -m bit.healthcheck

The staleness window is 3 × run_interval_seconds (default 300 s → 900 s)
so a container is only marked unhealthy after missing 3 consecutive cycles.
"""

import sys
from datetime import datetime, timezone

from .config import BITConfig
from .services.runner_state import RunnerStateStore

# Mark unhealthy after this many seconds without a state update.
# 3 × default 300s interval = 900s (15 min). Override via BITConfig.run_interval_seconds.
_STALE_MULTIPLIER = 3


def main() -> None:
    config = BITConfig()
    stale_threshold = config.run_interval_seconds * _STALE_MULTIPLIER

    result = RunnerStateStore.read(config.runner_state_path)

    if result.status == "not_found":
        print(f"UNHEALTHY: no runner state file at {config.runner_state_path}")
        sys.exit(1)

    if result.status == "corrupt" or result.state is None:
        print(f"UNHEALTHY: runner state file is corrupt — {result.error}")
        sys.exit(1)

    state = result.state

    if state.status != "running":
        print(f"UNHEALTHY: runner status is '{state.status}'")
        sys.exit(1)

    if result.file_mtime is not None:
        age = (datetime.now(tz=timezone.utc) - result.file_mtime).total_seconds()
        if age > stale_threshold:
            print(
                f"UNHEALTHY: runner state is stale "
                f"({age:.0f}s > {stale_threshold}s threshold)"
            )
            sys.exit(1)

    print(f"HEALTHY: runner is {state.status}")
    sys.exit(0)


if __name__ == "__main__":
    main()
