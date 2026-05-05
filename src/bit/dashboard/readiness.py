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
        portfolio_persistence_status: str = "not_found",
        credential_check_status: str | None = None,
        runner_state_status: str | None = None,
    ) -> list[ReadinessItem]:
        """
        Return one ReadinessItem per check, in priority order.

        Args:
            credential_check_status: Value of RunnerState.credential_check.
                "ok" / "skipped" / "failed: ..." / None (never checked).
            runner_state_status: Value of RunnerState.status as a string,
                or None if no runner state file exists.
        """
        return [
            self._check_config(config),
            self._check_portfolio(portfolio_available, portfolio_persistence_status),
            self._check_portfolio_persistence(portfolio_persistence_status, config.portfolio_state_path),
            self._check_journal_writable(journal_path),
            self._check_api_key(config),
            self._check_credential_check(credential_check_status, config),
            self._check_scheduler(runner_state_status),
            self._check_market_connectivity(journal_entry_count),
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
    def _check_portfolio(
        portfolio_available: bool,
        portfolio_persistence_status: str = "not_found",
    ) -> ReadinessItem:
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
        if portfolio_persistence_status == "ok":
            return ReadinessItem(
                key="portfolio",
                label="Portfolio data from persisted snapshot",
                status=ReadinessStatus.WARNING,
                detail=(
                    "Dashboard reads portfolio_state.json written by the bot. "
                    "Data reflects the last saved state. "
                    "Pass the live tracker via create_app() for real-time portfolio data."
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
    def _check_portfolio_persistence(status: str, portfolio_state_path: Path) -> ReadinessItem:
        if status == "ok":
            return ReadinessItem(
                key="portfolio_persistence",
                label="Portfolio state persisted to disk",
                status=ReadinessStatus.READY,
                detail=f"State file present and valid at {portfolio_state_path}.",
            )
        if status == "corrupt":
            return ReadinessItem(
                key="portfolio_persistence",
                label="Portfolio state file corrupt",
                status=ReadinessStatus.MISSING,
                detail=(
                    f"{portfolio_state_path} exists but cannot be parsed. "
                    "Delete the file to start fresh, or inspect it manually."
                ),
            )
        # not_found
        return ReadinessItem(
            key="portfolio_persistence",
            label="Portfolio state not yet persisted",
            status=ReadinessStatus.WARNING,
            detail=(
                f"No state file at {portfolio_state_path}. "
                "State will be lost on restart until the first fill is recorded."
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
                status=ReadinessStatus.WARNING,
                detail=(
                    "Paper trading uses Bybit public endpoints — no API key required. "
                    "Set BYBIT_API_KEY only for authenticated startup validation."
                ),
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
    def _check_credential_check(
        credential_check_status: str | None,
        config: BITConfig,
    ) -> ReadinessItem:
        """
        Authenticated credential validation — separate from API key presence.

        "ok"     → READY: key confirmed valid against Bybit.
        "skipped"→ WARNING (or MISSING if no key): no credentials to check.
        "failed" → MISSING: credentials rejected by Bybit.
        None     → WARNING: runner hasn't started yet; check not performed.
        """
        if not config.bybit_api_key:
            return ReadinessItem(
                key="credential_check",
                label="Credential check: no API key configured",
                status=ReadinessStatus.MISSING,
                detail=(
                    "Set BYBIT_API_KEY and BYBIT_API_SECRET in .env. "
                    "Required for live market data; optional for paper trading."
                ),
            )

        if credential_check_status == "ok":
            return ReadinessItem(
                key="credential_check",
                label="Credentials validated against Bybit",
                status=ReadinessStatus.READY,
                detail="API key accepted by Bybit at runner startup.",
            )

        if credential_check_status is not None and credential_check_status.startswith("failed"):
            return ReadinessItem(
                key="credential_check",
                label="Credential check failed",
                status=ReadinessStatus.MISSING,
                detail=credential_check_status,
            )

        if credential_check_status == "skipped":
            return ReadinessItem(
                key="credential_check",
                label="Credential check skipped (no credentials configured)",
                status=ReadinessStatus.WARNING,
                detail=(
                    "Runner started without credentials. "
                    "Public endpoints are used; authentication was not verified."
                ),
            )

        # None: runner hasn't run yet
        return ReadinessItem(
            key="credential_check",
            label="Credentials not yet verified",
            status=ReadinessStatus.WARNING,
            detail=(
                "API key is present but has not been validated. "
                "Start the bot runner to perform the credential check."
            ),
        )

    @staticmethod
    def _check_scheduler(runner_state_status: str | None) -> ReadinessItem:
        if runner_state_status == "running":
            return ReadinessItem(
                key="scheduler",
                label="BotRunner running",
                status=ReadinessStatus.READY,
                detail="Run loop active. Heartbeats written to runner_state.json.",
            )
        if runner_state_status == "error":
            return ReadinessItem(
                key="scheduler",
                label="BotRunner in error state",
                status=ReadinessStatus.MISSING,
                detail=(
                    "Runner stopped with an error. "
                    "Check logs, then restart with: python -m bit"
                ),
            )
        if runner_state_status in ("stopped", "stopping"):
            return ReadinessItem(
                key="scheduler",
                label="BotRunner stopped",
                status=ReadinessStatus.WARNING,
                detail="Runner is stopped. Restart with: python -m bit (or docker compose up bot)",
            )
        if runner_state_status == "starting":
            return ReadinessItem(
                key="scheduler",
                label="BotRunner starting up",
                status=ReadinessStatus.WARNING,
                detail="Run loop is initialising. Waiting for startup validation.",
            )
        # None or unknown: never started
        return ReadinessItem(
            key="scheduler",
            label="Run loop not started",
            status=ReadinessStatus.MISSING,
            detail=(
                "No run loop active. BotRunner is implemented — "
                "start with: python -m bit (or docker compose up bot)"
            ),
        )

    @staticmethod
    def _check_market_connectivity(journal_entry_count: int) -> ReadinessItem:
        if journal_entry_count > 0:
            return ReadinessItem(
                key="market_connectivity",
                label=f"Market data connectivity confirmed ({journal_entry_count} cycles)",
                status=ReadinessStatus.READY,
                detail=(
                    f"{journal_entry_count} pipeline cycles completed. "
                    "Bybit public endpoints are reachable and returning data."
                ),
            )
        return ReadinessItem(
            key="market_connectivity",
            label="Market data connectivity not yet verified",
            status=ReadinessStatus.WARNING,
            detail=(
                "No pipeline cycles have completed yet. "
                "Start the bot runner to confirm Bybit connectivity."
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
        # /.dockerenv is present in all Docker containers — reliable inside-container signal.
        if os.path.exists("/.dockerenv"):
            return ReadinessItem(
                key="docker",
                label="Running inside Docker container",
                status=ReadinessStatus.READY,
                detail="Process supervised by Docker Compose with healthchecks.",
            )
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
