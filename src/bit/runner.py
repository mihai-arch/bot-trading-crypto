"""
BotRunner — v1 paper trading scheduler.

Runs the pipeline for all configured symbols on a fixed interval.
Tracks runtime state in memory and writes a JSON heartbeat file after each cycle.

Design:
  - Single asyncio event loop — no threads, no external schedulers.
  - One full cycle = pipeline.run() for each symbol in sequence.
  - Per-symbol errors are caught and logged; remaining symbols still run.
  - Heartbeat is written atomically via a temp file → rename pattern.
  - Sleep is between cycle END and next cycle START (no drift accumulation).

v1 constraints:
  - Paper trading only. Live mode raises NotImplementedError in ExecutionEngine.
  - No distributed coordination. Single-process, single event loop.
  - No adaptive scheduling. Fixed interval regardless of market conditions.

TODO (v1.5):
  - Persist portfolio state across restarts.
  - Adaptive interval based on market session / volatility.
  - WebSocket-based price streaming instead of REST polling.
  - Retry with exponential back-off for transient network failures.
"""

import asyncio
import json
import logging
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from .config import BITConfig
from .domain.enums import Symbol
from .pipeline import Pipeline
from .services.market_data import MarketDataService

logger = logging.getLogger(__name__)


class RunnerStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class RunnerState:
    """
    Mutable in-memory runtime state for BotRunner.

    Shared reference — read by DashboardService to populate loop_running,
    runner info, and dynamic scheduler health/readiness items.

    Not thread-safe — designed for single asyncio event loop use.
    """

    mode: str  # "PAPER" or "LIVE"
    status: RunnerStatus = RunnerStatus.STOPPED
    last_heartbeat: datetime | None = None
    last_cycle_start: datetime | None = None
    last_cycle_end: datetime | None = None
    last_successful_cycle: datetime | None = None
    last_error_message: str | None = None
    last_error_time: datetime | None = None
    symbols_last_cycle: list[str] = field(default_factory=list)
    startup_validated: bool = False
    startup_error: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict for the heartbeat file."""

        def _iso(dt: datetime | None) -> str | None:
            return dt.isoformat() if dt is not None else None

        return {
            "mode": self.mode,
            "status": str(self.status),
            "last_heartbeat": _iso(self.last_heartbeat),
            "last_cycle_start": _iso(self.last_cycle_start),
            "last_cycle_end": _iso(self.last_cycle_end),
            "last_successful_cycle": _iso(self.last_successful_cycle),
            "last_error_message": self.last_error_message,
            "last_error_time": _iso(self.last_error_time),
            "symbols_last_cycle": self.symbols_last_cycle,
            "startup_validated": self.startup_validated,
            "startup_error": self.startup_error,
        }


class BotRunner:
    """
    v1 paper trading run loop.

    Calls pipeline.run(symbol) for each configured symbol on a fixed interval.
    Continues on per-symbol errors. Stops cleanly on KeyboardInterrupt or SIGTERM.

    Usage:
        runner = BotRunner(config=config, pipeline=pipeline)
        asyncio.run(runner.start())

    The runner.state property exposes a shared RunnerState object that can be
    passed to DashboardService for live dashboard integration.
    """

    def __init__(
        self,
        config: BITConfig,
        pipeline: Pipeline,
        *,
        symbols: list[Symbol] | None = None,
        run_interval_seconds: int | None = None,
        heartbeat_path: Path | None = None,
        state: RunnerState | None = None,
        market_data: MarketDataService | None = None,
    ) -> None:
        if not config.paper_trading:
            raise ValueError(
                "BotRunner v1 only supports paper_trading=True. "
                "Live trading is not yet implemented."
            )
        self._config = config
        self._pipeline = pipeline
        self._symbols = symbols if symbols is not None else list(config.symbols)
        self._interval = (
            run_interval_seconds
            if run_interval_seconds is not None
            else config.run_interval_seconds
        )
        self._heartbeat_path = heartbeat_path or config.heartbeat_path
        self._running = False
        self._market_data = market_data

        mode = "PAPER" if config.paper_trading else "LIVE"
        self._state = state or RunnerState(mode=mode)

    @property
    def state(self) -> RunnerState:
        """Current runtime state. Shared reference — read by DashboardService."""
        return self._state

    async def start(self) -> None:
        """
        Start the run loop. Blocks until stop() is called or process is interrupted.

        Registers a SIGTERM handler so the loop exits cleanly under process supervision.
        KeyboardInterrupt (Ctrl+C) is also handled gracefully.
        """
        self._running = True
        self._state.status = RunnerStatus.RUNNING
        self._state.last_heartbeat = datetime.now(tz=timezone.utc)

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._handle_signal)

        logger.info(
            "BotRunner started. mode=%s symbols=%s interval=%ds",
            self._state.mode,
            [str(s) for s in self._symbols],
            self._interval,
        )
        self._write_heartbeat()

        try:
            await self._validate_startup()
            await self._loop()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("BotRunner interrupted — shutting down.")
        finally:
            self._running = False
            # Preserve ERROR status set by _validate_startup or _run_cycle;
            # only move to STOPPED when the loop ended cleanly.
            if self._state.status == RunnerStatus.RUNNING:
                self._state.status = RunnerStatus.STOPPED
            self._state.last_heartbeat = datetime.now(tz=timezone.utc)
            self._write_heartbeat()
            logger.info("BotRunner stopped.")

    def stop(self) -> None:
        """Request a clean stop. The current cycle will complete before the loop exits."""
        self._running = False

    def _handle_signal(self) -> None:
        logger.info("SIGTERM received — requesting stop.")
        self.stop()

    async def _validate_startup(self) -> None:
        """
        Verify market data connectivity before entering the run loop.

        Fetches a ticker for the first configured symbol using the public
        Bybit endpoint (no authentication required). If the call fails for
        any reason, sets state to ERROR and raises RuntimeError so the caller
        can fail clearly and visibly.

        No-op when no MarketDataService was injected (skips validation).
        """
        if self._market_data is None:
            return

        probe_symbol = self._symbols[0]
        logger.info(
            "Startup validation: testing public market data connectivity (%s)...",
            probe_symbol,
        )
        try:
            ticker = await self._market_data.get_ticker(probe_symbol)
            self._state.startup_validated = True
            logger.info(
                "Startup validation passed. %s last_price=%s",
                probe_symbol,
                ticker.last_price,
            )
        except Exception as exc:
            err_msg = f"Market data connectivity check failed ({probe_symbol}): {type(exc).__name__}: {exc}"
            self._state.startup_error = err_msg
            self._state.status = RunnerStatus.ERROR
            self._state.last_error_message = err_msg
            self._state.last_error_time = datetime.now(tz=timezone.utc)
            logger.error("Startup validation failed — runner will not start. %s", err_msg)
            raise RuntimeError(err_msg) from exc

    async def _loop(self) -> None:
        """Scheduling loop: run cycle → sleep interval → repeat until stopped."""
        while self._running:
            await self._run_cycle()
            if self._running:
                logger.debug("Sleeping %ds before next cycle.", self._interval)
                await asyncio.sleep(self._interval)

    async def _run_cycle(self) -> None:
        """
        Execute one full cycle: pipeline.run() for each symbol in sequence.

        Per-symbol errors are caught, logged, and recorded in state without
        aborting remaining symbols or crashing the loop.
        """
        cycle_start = datetime.now(tz=timezone.utc)
        self._state.last_cycle_start = cycle_start
        self._state.last_heartbeat = cycle_start
        self._state.symbols_last_cycle = []

        logger.info("Cycle start. symbols=%s", [str(s) for s in self._symbols])

        cycle_had_error = False

        for symbol in self._symbols:
            if not self._running:
                logger.info("Stop requested mid-cycle — skipping remaining symbols.")
                break
            try:
                entry = await self._pipeline.run(symbol)
                self._state.symbols_last_cycle.append(str(symbol))
                logger.info(
                    "symbol=%s decision=%s score=%.3f",
                    symbol,
                    entry.decision_state,
                    float(entry.composite_score),
                )
            except Exception as exc:
                cycle_had_error = True
                err_msg = f"{type(exc).__name__}: {exc}"
                self._state.last_error_message = err_msg
                self._state.last_error_time = datetime.now(tz=timezone.utc)
                self._state.status = RunnerStatus.ERROR
                logger.error(
                    "Pipeline error. symbol=%s error=%s",
                    symbol,
                    err_msg,
                    exc_info=True,
                )

        cycle_end = datetime.now(tz=timezone.utc)
        self._state.last_cycle_end = cycle_end
        self._state.last_heartbeat = cycle_end

        if not cycle_had_error:
            self._state.last_successful_cycle = cycle_end
            if self._running:
                self._state.status = RunnerStatus.RUNNING

        self._write_heartbeat()

        duration = (cycle_end - cycle_start).total_seconds()
        logger.info("Cycle end. duration=%.1fs had_error=%s", duration, cycle_had_error)

    def _write_heartbeat(self) -> None:
        """
        Write runtime state to heartbeat JSON file.

        Uses atomic temp-file → rename to avoid partial reads by the dashboard.
        Never raises — heartbeat failures are logged as warnings only.
        """
        try:
            self._heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._heartbeat_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._state.to_dict(), indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._heartbeat_path)
        except Exception as exc:
            logger.warning("Failed to write heartbeat: %s", exc)
