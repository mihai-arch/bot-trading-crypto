"""
ReadinessEvaluator

Evaluates operational readiness for continuous paper trading.

IMPORTANT: Readiness is distinct from Health.

  Health   = structural (is the code written and callable?)
  Readiness = operational (can we actually run paper trading continuously right now?)

Readiness checks may overlap with health but focus on:
  - Are secrets configured?
  - Is the portfolio tracker available?
  - Is the journal writable?
  - Is the scheduler running?
  - Has market connectivity been verified?

No live I/O. No network calls. Deterministic given the same inputs.
"""

import os
from pathlib import Path

from ..config import BITConfig
from .models import ReadinessItem, ReadinessStatus


class ReadinessEvaluator:
    """Evaluates paper trading operational readiness. No live I/O."""

    def evaluate(
        self,
        config: BITConfig,
        journal_entry_count: int,
        portfolio_available: bool,
        journal_path: Path,
        project_root: Path = Path("."),
    ) -> list[ReadinessItem]:
        """Return one ReadinessItem per check, in priority order."""
        return [
            self._check_config(config),
            self._check_portfolio(portfolio_available),
            self._check_journal_writable(journal_path),
            self._check_api_key(config),
            self._check_scheduler(),
            self._check_market_connectivity(config),
            self._check_journal_has_data(journal_entry_count),
            self._check_docker(project_root),
        ]

    # ── Individual checks ────────────────────────────────────────────────────

    @staticmethod
    def _check_config(config: BITConfig) -> ReadinessItem:
        return ReadinessItem(
            key="config",
            label="Config loaded",
            status=ReadinessStatus.READY,
            detail=(
                f"BITConfig loaded. Paper mode: {config.paper_trading}. "
                f"Symbols: {[str(s) for s in config.symbols]}."
            ),
        )

    @staticmethod
    def _check_portfolio(portfolio_available: bool) -> ReadinessItem:
        if portfolio_available:
            return ReadinessItem(
                key="portfolio",
                label="Portfolio tracker available (in-memory)",
                status=ReadinessStatus.WARNING,
                detail=(
                    "PaperPortfolioTracker is in-memory only. "
                    "State resets on process restart — no persistence yet."
                ),
            )
        return ReadinessItem(
            key="portfolio",
            label="Portfolio tracker not injected into dashboard",
            status=ReadinessStatus.WARNING,
            detail=(
                "Dashboard started without a shared PaperPortfolioTracker. "
                "Portfolio section shows N/A. Pass the tracker via create_app()."
            ),
        )

    @staticmethod
    def _check_journal_writable(journal_path: Path) -> ReadinessItem:
        try:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            writable = os.access(journal_path.parent, os.W_OK)
        except OSError:
            writable = False

        if writable:
            return ReadinessItem(
                key="journal_writable",
                label="Journal path writable",
                status=ReadinessStatus.READY,
                detail=str(journal_path),
            )
        return ReadinessItem(
            key="journal_writable",
            label="Journal path not writable",
            status=ReadinessStatus.MISSING,
            detail=f"Cannot write to {journal_path.parent}. Check directory permissions.",
        )

    @staticmethod
    def _check_api_key(config: BITConfig) -> ReadinessItem:
        if not config.bybit_api_key:
            return ReadinessItem(
                key="api_key",
                label="Bybit API key not configured",
                status=ReadinessStatus.MISSING,
                detail="Set BYBIT_API_KEY in .env. Required to fetch live market data.",
            )
        return ReadinessItem(
            key="api_key",
            label="API key present — not verified against exchange",
            status=ReadinessStatus.WARNING,
            detail=(
                "BYBIT_API_KEY is set in config but has not been validated "
                "with a live Bybit API call. Connection status is unknown."
            ),
        )

    @staticmethod
    def _check_scheduler() -> ReadinessItem:
        return ReadinessItem(
            key="scheduler",
            label="Scheduler / loop not running",
            status=ReadinessStatus.MISSING,
            detail=(
                "No run loop exists. Paper trading cannot run continuously "
                "until a scheduler that calls pipeline.run(symbol) is added."
            ),
        )

    @staticmethod
    def _check_market_connectivity(config: BITConfig) -> ReadinessItem:
        if not config.bybit_api_key:
            return ReadinessItem(
                key="market_connectivity",
                label="Market data connectivity: blocked by missing API key",
                status=ReadinessStatus.MISSING,
                detail="Cannot verify market data connectivity without a Bybit API key.",
            )
        return ReadinessItem(
            key="market_connectivity",
            label="Market data connectivity not verified",
            status=ReadinessStatus.WARNING,
            detail=(
                "MarketDataService is implemented but no live API call has been made. "
                "Connectivity to Bybit is assumed, not confirmed."
            ),
        )

    @staticmethod
    def _check_journal_has_data(journal_entry_count: int) -> ReadinessItem:
        if journal_entry_count > 0:
            return ReadinessItem(
                key="journal_data",
                label=f"Journal has data ({journal_entry_count} entries)",
                status=ReadinessStatus.READY,
                detail="At least one pipeline cycle has been recorded.",
            )
        return ReadinessItem(
            key="journal_data",
            label="No journal entries recorded yet",
            status=ReadinessStatus.WARNING,
            detail=(
                "No pipeline cycles have run. "
                "Start the bot loop to begin recording decisions."
            ),
        )

    @staticmethod
    def _check_docker(project_root: Path) -> ReadinessItem:
        for name in (
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        ):
            if (project_root / name).exists():
                return ReadinessItem(
                    key="docker",
                    label="Docker configured",
                    status=ReadinessStatus.READY,
                    detail=f"Found {project_root / name}.",
                )
        return ReadinessItem(
            key="docker",
            label="Docker not configured",
            status=ReadinessStatus.WARNING,
            detail=(
                "No docker-compose.yml found. Not blocking for local paper trading "
                "but recommended for production deployment and process supervision."
            ),
        )
