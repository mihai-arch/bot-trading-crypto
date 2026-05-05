"""
BotRunner — v1 run loop for BIT.

Executes one pipeline cycle per configured symbol on a fixed interval.
Writes RunnerState at each lifecycle transition so the dashboard can
surface loop health even when the runner is not directly queried.
Saves portfolio state after each fill so positions survive restarts.

Lifecycle:
    STARTING → startup validation → RUNNING → (loop) → STOPPING → STOPPED
    STARTING → validation fails   → ERROR

Usage:
    config = BITConfig()
    portfolio = PaperPortfolioTracker(config.capital_usdt)
    pipeline = ... # assembled from all services, with portfolio injected
    runner = BotRunner(config=config, pipeline=pipeline, portfolio=portfolio)
    asyncio.run(runner.start())

Or via the CLI entry point:
    python -m bit.runner
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from .config import BITConfig
from .pipeline import Pipeline
from .services.credential_check import CredentialCheckResult
from .services.paper_portfolio import PaperPortfolioTracker
from .services.portfolio_store import PortfolioStateStore
from .services.runner_state import RunnerState, RunnerStateStore, RunnerStatus

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class BotRunner:
    """
    v1 BIT run loop.

    All dependencies are injected at construction time. The runner:
      - Validates startup configuration before entering the loop.
      - Runs pipeline.run(symbol) for each configured symbol each cycle.
      - Captures per-symbol errors without crashing the whole loop.
      - Writes RunnerState at every lifecycle transition.
      - Saves portfolio state after each fill.
    """

    def __init__(
        self,
        config: BITConfig,
        pipeline: Pipeline,
        portfolio: PaperPortfolioTracker,
        credential_checker: Callable[[], Awaitable[CredentialCheckResult]] | None = None,
    ) -> None:
        self._config = config
        self._pipeline = pipeline
        self._portfolio = portfolio
        self._credential_checker = credential_checker
        self._stop_event = asyncio.Event()
        self._state = RunnerState(updated_at=_now(), status=RunnerStatus.STARTING)

    # ── Public interface ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Run the main loop until stop() is called or a fatal startup error occurs.

        Per-cycle pipeline errors are logged and recorded but do not stop the loop.
        Fatal errors (uncaught exceptions outside the per-symbol try/except) set
        ERROR status and re-raise so the caller sees the failure.
        """
        self._write(status=RunnerStatus.STARTING)
        logger.info(
            "BIT runner starting (paper=%s, symbols=%s, interval=%ds)",
            self._config.paper_trading,
            [s.value for s in self._config.symbols],
            self._config.run_interval_seconds,
        )

        try:
            cred_status = await self._validate_startup()
        except Exception as exc:
            self._write(
                status=RunnerStatus.ERROR,
                startup_error=str(exc),
                startup_validated=False,
            )
            logger.error("Startup validation failed: %s", exc)
            return

        self._write(
            status=RunnerStatus.RUNNING,
            startup_validated=True,
            credential_check=cred_status,
        )
        logger.info("BIT runner started.")

        try:
            while not self._stop_event.is_set():
                await self._run_cycle()
                # Sleep for the configured interval, waking early on stop().
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.run_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass  # Normal: interval elapsed, run next cycle.
        except Exception as exc:
            self._write(status=RunnerStatus.ERROR, last_error=str(exc))
            logger.error("Runner fatal error: %s", exc, exc_info=True)
            raise
        finally:
            self._write(status=RunnerStatus.STOPPED)
            logger.info("BIT runner stopped.")

    async def stop(self) -> None:
        """
        Signal the run loop to stop after the current cycle completes.

        Returns immediately — wait on the task returned by start() to confirm
        the loop has fully shut down.
        """
        logger.info("Runner stop requested.")
        self._write(status=RunnerStatus.STOPPING)
        self._stop_event.set()

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _validate_startup(self) -> str:
        """
        Startup checks. Returns credential_check status string.

        Raises RuntimeError on fatal misconfiguration or failed credential check.

        1. Config sanity (paper mode, symbols configured) — no network.
        2. Credential check (if a checker was injected) — one authenticated GET.
           Failed check raises RuntimeError and halts startup.
           Skipped/ok check returns the status string written to RunnerState.
        """
        if not self._config.paper_trading:
            raise RuntimeError(
                "Live trading is not implemented in v1. Set paper_trading=True."
            )
        if not self._config.symbols:
            raise RuntimeError("No symbols configured. Set at least one symbol.")

        if self._credential_checker is None:
            return "skipped"

        result = await self._credential_checker()
        logger.info("Credential check: %s — %s", result.status, result.detail)
        if result.status == "failed":
            raise RuntimeError(f"Credential check failed: {result.detail}")
        return result.status

    async def _run_cycle(self) -> None:
        """Run one evaluation cycle for each configured symbol."""
        cycle_start = _now()
        self._write(
            status=RunnerStatus.RUNNING,
            last_cycle_start=cycle_start,
            last_heartbeat=cycle_start,
            processed_symbols=[],
        )

        processed: list[str] = []
        cycle_error: str | None = None

        for symbol in self._config.symbols:
            try:
                entry = await self._pipeline.run(symbol)
                processed.append(symbol.value)

                # Persist portfolio state after each fill so positions survive restarts.
                if entry.fill_price is not None and entry.fill_qty is not None:
                    self._save_portfolio()

            except Exception as exc:
                msg = f"{symbol.value}: {exc}"
                logger.error(
                    "Pipeline error for %s: %s", symbol.value, exc, exc_info=True
                )
                cycle_error = msg
                # One symbol failing must not prevent remaining symbols from running.

        cycle_end = _now()
        update: dict = {"last_cycle_end": cycle_end, "processed_symbols": processed}
        if cycle_error:
            update["last_error"] = cycle_error
        else:
            update["last_successful_cycle"] = cycle_end
        self._write(**update)

        # Always persist portfolio at cycle end so saved_at stays current.
        # This ensures the dashboard can show "data as of N seconds ago" even
        # between fills (when positions don't change but the timestamp matters).
        self._save_portfolio()

    def _save_portfolio(self) -> None:
        """Persist portfolio state to disk; logs but never raises on failure."""
        try:
            PortfolioStateStore.save(
                self._portfolio,
                self._config.portfolio_state_path,
            )
        except Exception as exc:
            logger.warning("Failed to save portfolio state: %s", exc)

    def _write(self, **kwargs) -> None:
        """
        Update named state fields, stamp updated_at, and write to disk.

        Write failures are logged but never crash the runner — state visibility
        is best-effort; the trade loop must not be blocked by I/O errors.
        """
        self._state = self._state.model_copy(
            update={"updated_at": _now(), **kwargs}
        )
        try:
            RunnerStateStore.write(self._state, self._config.runner_state_path)
        except Exception as exc:
            logger.warning("Failed to write runner state: %s", exc)
