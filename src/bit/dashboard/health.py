"""
HealthChecker

Structural availability probes — checks whether each service is
implemented, partially implemented, or missing.

IMPORTANT: These are NOT live connectivity checks.
They reflect the current codebase state, not runtime operational status.

  Health   = "Is this service implemented?" (structural, static)
  Readiness = "Can we run paper trading right now?" (operational, dynamic)

No network I/O. No subprocesses. No imports that can fail at runtime.
"""

import os
from pathlib import Path

from ..config import BITConfig
from .models import HealthItem, ServiceStatus


class HealthChecker:
    """
    Probes structural service availability.

    All probes are deterministic given the same codebase state.
    Pass project_root to control where Docker files are searched.
    """

    def probe_all(
        self,
        config: BITConfig,
        journal_path: Path,
        project_root: Path = Path("."),
    ) -> list[HealthItem]:
        """Return one HealthItem per service, in pipeline order."""
        return [
            self._probe_market_data(),
            self._probe_feature_engine(),
            self._probe_signal_engine(),
            self._probe_decision_engine(),
            self._probe_risk_engine(),
            self._probe_execution_engine(config),
            self._probe_journal(journal_path),
            self._probe_scheduler(),
            self._probe_docker(project_root),
        ]

    # ── Individual probes ────────────────────────────────────────────────────

    @staticmethod
    def _probe_market_data() -> HealthItem:
        return HealthItem(
            name="MarketDataService",
            status=ServiceStatus.PARTIAL,
            detail=(
                "get_klines / get_ticker / get_instrument_filter: implemented. "
                "get_orderbook_top / get_recent_trades / get_portfolio_state: stubs."
            ),
        )

    @staticmethod
    def _probe_feature_engine() -> HealthItem:
        return HealthItem(
            name="FeatureEngine",
            status=ServiceStatus.IMPLEMENTED,
            detail="EMA, RSI, ATR, volume MA, range bounds, relative_volume, ema_distance_pct.",
        )

    @staticmethod
    def _probe_signal_engine() -> HealthItem:
        return HealthItem(
            name="SignalEngine",
            status=ServiceStatus.IMPLEMENTED,
            detail=(
                "Fan-out → filter (score > 0) → select best. "
                "Strategies: TrendContinuation, BreakoutConfirmation."
            ),
        )

    @staticmethod
    def _probe_decision_engine() -> HealthItem:
        return HealthItem(
            name="DecisionEngine",
            status=ServiceStatus.IMPLEMENTED,
            detail="ENTER / MONITOR / REJECT with configurable score thresholds.",
        )

    @staticmethod
    def _probe_risk_engine() -> HealthItem:
        return HealthItem(
            name="RiskEngine",
            status=ServiceStatus.IMPLEMENTED,
            detail="Position sizing, max open positions, min notional, qty_step snapping.",
        )

    @staticmethod
    def _probe_execution_engine(config: BITConfig) -> HealthItem:
        if config.paper_trading:
            return HealthItem(
                name="ExecutionEngine",
                status=ServiceStatus.IMPLEMENTED,
                detail="Paper mode active — fee + slippage simulation. Live mode not implemented.",
            )
        return HealthItem(
            name="ExecutionEngine",
            status=ServiceStatus.STUB,
            detail=(
                "Live mode selected but ExecutionEngine._live_execute() "
                "raises NotImplementedError. Live trading is not yet supported."
            ),
        )

    @staticmethod
    def _probe_journal(journal_path: Path) -> HealthItem:
        try:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            writable = os.access(journal_path.parent, os.W_OK)
        except OSError:
            writable = False

        if writable:
            return HealthItem(
                name="JournalLearningStore",
                status=ServiceStatus.IMPLEMENTED,
                detail=f"Append-only JSONL at {journal_path}",
            )
        return HealthItem(
            name="JournalLearningStore",
            status=ServiceStatus.DEGRADED,
            detail=f"Cannot write to {journal_path.parent}. Check directory permissions.",
        )

    @staticmethod
    def _probe_scheduler() -> HealthItem:
        return HealthItem(
            name="Scheduler / Loop",
            status=ServiceStatus.MISSING,
            detail=(
                "No run loop exists. pipeline.run(symbol) must be called manually "
                "per cycle. Continuous paper trading is not possible yet."
            ),
        )

    @staticmethod
    def _probe_docker(project_root: Path) -> HealthItem:
        for name in (
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        ):
            if (project_root / name).exists():
                return HealthItem(
                    name="Docker",
                    status=ServiceStatus.IMPLEMENTED,
                    detail=f"Found {project_root / name}",
                )
        return HealthItem(
            name="Docker",
            status=ServiceStatus.MISSING,
            detail=(
                "No docker-compose.yml found. "
                "Container health monitoring and process supervision not available."
            ),
        )
