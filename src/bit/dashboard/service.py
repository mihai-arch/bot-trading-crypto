"""
DashboardService

Assembles a DashboardSnapshot from all available data sources:
  - BITConfig                 (always available — loaded from .env)
  - JournalLearningStore      (reads data/journal.jsonl from disk)
  - PaperPortfolioTracker     (optional — in-memory; must be injected)
  - HealthChecker             (structural probes, no live I/O)
  - ReadinessEvaluator        (operational readiness checklist)

No fake data. Fields that cannot be populated are None or empty.
Callers (templates, JSON endpoint) must handle None explicitly.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..config import BITConfig
from ..domain.journal import JournalEntry
from ..services.journal import JournalLearningStore
from ..services.paper_portfolio import PaperPortfolioTracker
from ..services.portfolio_store import PortfolioStateStore
from ..services.runner_state import RunnerStateStore
from .health import HealthChecker
from .models import (
    DashboardSnapshot,
    DecisionRow,
    FillRow,
    PortfolioSummary,
    PositionRow,
    RiskConfig,
    RuntimeGap,
    RunnerStateSnapshot,
)
from .readiness import ReadinessEvaluator

_MAX_RECENT = 20


def _entry_to_decision_row(entry: JournalEntry) -> DecisionRow:
    """
    Map a JournalEntry to a DecisionRow for display.

    strategy_selected is derived from raw_signal_scores: the strategy with
    the highest score (> 0). If all scores are 0, strategy_selected is None.
    """
    strategy_selected: str | None = None
    if entry.raw_signal_scores:
        best = max(entry.raw_signal_scores, key=lambda k: entry.raw_signal_scores[k])
        if entry.raw_signal_scores[best] > 0:
            strategy_selected = best

    return DecisionRow(
        timestamp=entry.cycle_timestamp,
        symbol=str(entry.symbol),
        state=str(entry.decision_state),
        composite_score=entry.composite_score,
        strategy_selected=strategy_selected,
        fill_price=entry.fill_price,
        fill_qty=entry.fill_qty,
        fee_usdt=entry.fee_usdt,
        is_paper=entry.is_paper,
    )


def _entry_to_fill_row(entry: JournalEntry) -> FillRow | None:
    """
    Map a JournalEntry to a FillRow if execution occurred.

    Returns None if the entry has no fill (MONITOR or REJECT cycles).
    Side is always "BUY" in v1 (long-only pipeline). Update when SELL
    exits are recorded in JournalEntry.
    """
    if entry.fill_price is None or entry.fill_qty is None:
        return None
    return FillRow(
        timestamp=entry.cycle_timestamp,
        symbol=str(entry.symbol),
        side="BUY",  # v1: long-only; all pipeline fills are entry orders
        qty=entry.fill_qty,
        fill_price=entry.fill_price,
        fee_usdt=entry.fee_usdt or Decimal("0"),
        is_paper=entry.is_paper,
    )


def _collect_fills(entries: list[JournalEntry]) -> list[FillRow]:
    """Extract FillRow objects from entries that contain fill data."""
    result: list[FillRow] = []
    for entry in entries:
        row = _entry_to_fill_row(entry)
        if row is not None:
            result.append(row)
    return result


def _build_runtime_gaps(
    config: BITConfig,
    portfolio: PaperPortfolioTracker | None,
    portfolio_persistence_status: str = "not_found",
    runner_state: RunnerStateSnapshot | None = None,
) -> list[RuntimeGap]:
    """
    Build the known runtime gaps list.

    These are always shown — they represent the honest current state of the
    project and what is still needed before continuous paper trading works.
    """
    gaps: list[RuntimeGap] = []

    # ── Scheduler / loop ──────────────────────────────────────────────────────
    if runner_state is not None:
        age_str = (
            f" (state age: {runner_state.state_age_seconds:.0f}s)"
            if runner_state.state_age_seconds is not None
            else ""
        )
        gaps.append(RuntimeGap(
            label=f"Runner state: {runner_state.status}{age_str}",
            detail=(
                f"Last heartbeat: {runner_state.last_heartbeat or 'N/A'}. "
                f"Last successful cycle: {runner_state.last_successful_cycle or 'N/A'}. "
                f"Symbols: {', '.join(runner_state.processed_symbols) or 'none'}."
            ),
        ))
    else:
        gaps.append(RuntimeGap(
            label="No scheduler / run loop",
            detail=(
                "pipeline.run(symbol) is never called automatically. "
                "A Runner / async loop must be added to start paper trading continuously."
            ),
        ))

    # ── Portfolio ─────────────────────────────────────────────────────────────
    if portfolio is None:
        gaps.append(RuntimeGap(
            label="Portfolio tracker not injected into dashboard",
            detail=(
                "Dashboard started without a shared PaperPortfolioTracker instance. "
                "Portfolio section shows N/A. Pass the tracker via create_app()."
            ),
        ))
    elif portfolio_persistence_status == "ok":
        gaps.append(RuntimeGap(
            label="Portfolio state persisted (in-memory + disk)",
            detail=(
                f"State is saved to {config.portfolio_state_path} after each fill. "
                "Positions will survive process restart."
            ),
        ))
    elif portfolio_persistence_status == "corrupt":
        gaps.append(RuntimeGap(
            label="Portfolio state file corrupt",
            detail=(
                f"{config.portfolio_state_path} exists but cannot be parsed. "
                "Portfolio state may not survive restart. Inspect the file manually."
            ),
        ))
    else:
        gaps.append(RuntimeGap(
            label="Portfolio state is in-memory only (not yet persisted)",
            detail=(
                "PaperPortfolioTracker is active but no state file exists yet. "
                f"State will be written to {config.portfolio_state_path} after the first fill."
            ),
        ))

    # ── Mark prices ───────────────────────────────────────────────────────────
    gaps.append(RuntimeGap(
        label="No live mark prices",
        detail=(
            "MarketDataService is not running in a continuous loop. "
            "Unrealized PnL on open positions is shown as N/A."
        ),
    ))

    # ── API key ───────────────────────────────────────────────────────────────
    if not config.bybit_api_key:
        gaps.append(RuntimeGap(
            label="Bybit API key not configured",
            detail=(
                "Paper trading uses Bybit public endpoints — no API key required. "
                "Set BYBIT_API_KEY only for authenticated startup validation."
            ),
        ))
    else:
        gaps.append(RuntimeGap(
            label="API key present — not verified against exchange",
            detail=(
                "BYBIT_API_KEY is set in config but has not been validated "
                "with a live Bybit API call. Connection status is unknown."
            ),
        ))

    # ── Docker ────────────────────────────────────────────────────────────────
    if os.path.exists("/.dockerenv"):
        gaps.append(RuntimeGap(
            label="Running inside Docker container",
            detail=(
                "Process supervised by Docker Compose with healthchecks "
                "and restart: unless-stopped."
            ),
        ))
    else:
        gaps.append(RuntimeGap(
            label="Docker not configured",
            detail=(
                "No docker-compose.yml found. Container health monitoring and "
                "process supervision are not available."
            ),
        ))

    return gaps


class DashboardService:
    """
    Assembles DashboardSnapshot from all available data sources.

    Usage:
        service = DashboardService(config, journal, portfolio)
        snapshot = service.build_snapshot()
    """

    def __init__(
        self,
        config: BITConfig,
        journal: JournalLearningStore,
        portfolio: PaperPortfolioTracker | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._config = config
        self._journal = journal
        self._portfolio = portfolio
        self._project_root = project_root or Path(".")
        self._health = HealthChecker()
        self._readiness = ReadinessEvaluator()

    def build_snapshot(self) -> DashboardSnapshot:
        """
        Build a complete dashboard snapshot from all available sources.

        Never raises. Missing data is represented as None or empty lists.
        """
        entries = self._journal.read_all()
        last_entry = entries[-1] if entries else None
        recent_entries = entries[-_MAX_RECENT:]

        # ── Portfolio ──────────────────────────────────────────────────────────
        portfolio_persistence_status = PortfolioStateStore.status(
            self._config.portfolio_state_path
        )

        portfolio_summary: PortfolioSummary | None = None
        positions: list[PositionRow] = []

        if self._portfolio is not None:
            snap = self._portfolio.snapshot()
            portfolio_summary = PortfolioSummary(
                total_equity_usdt=snap.total_equity_usdt,
                available_usdt=snap.available_usdt,
                realized_pnl_usdt=snap.realized_pnl_usdt,
                open_position_count=len(snap.open_positions),
                is_persistent=portfolio_persistence_status == "ok",
            )
            positions = [
                PositionRow(
                    symbol=str(pos.symbol),
                    qty=pos.qty,
                    avg_entry_price=pos.avg_entry_price,
                    mark_price=None,        # no live ticker feed in v1
                    unrealized_pnl=None,    # requires mark price
                )
                for pos in snap.open_positions.values()
            ]

        # ── Runner state ──────────────────────────────────────────────────────
        runner_state_snapshot: RunnerStateSnapshot | None = None
        runner_read = RunnerStateStore.read(self._config.runner_state_path)
        if runner_read.status == "ok" and runner_read.state is not None:
            rs = runner_read.state
            age: float | None = None
            if runner_read.file_mtime is not None:
                age = (datetime.now(tz=timezone.utc) - runner_read.file_mtime).total_seconds()
            runner_state_snapshot = RunnerStateSnapshot(
                status=str(rs.status),
                startup_validated=rs.startup_validated,
                startup_error=rs.startup_error,
                last_heartbeat=rs.last_heartbeat,
                last_cycle_start=rs.last_cycle_start,
                last_cycle_end=rs.last_cycle_end,
                last_successful_cycle=rs.last_successful_cycle,
                last_error=rs.last_error,
                processed_symbols=rs.processed_symbols,
                updated_at=rs.updated_at,
                state_age_seconds=age,
                credential_check=rs.credential_check,
            )

        # loop_running: True only when runner state is fresh and shows RUNNING.
        # Use run_interval_seconds + 60 so the flag stays True during the normal
        # sleep between cycles (avoiding false negatives at 300s interval).
        _stale_seconds = self._config.run_interval_seconds + 60
        loop_running = (
            runner_state_snapshot is not None
            and runner_state_snapshot.status == "running"
            and runner_state_snapshot.state_age_seconds is not None
            and runner_state_snapshot.state_age_seconds < _stale_seconds
        )

        # ── Decisions + fills ─────────────────────────────────────────────────
        recent_decisions = [_entry_to_decision_row(e) for e in reversed(recent_entries)]
        all_fills = _collect_fills(entries)
        recent_fills = list(reversed(all_fills[-_MAX_RECENT:]))

        # ── Risk config ────────────────────────────────────────────────────────
        risk_config = RiskConfig(
            capital_usdt=self._config.capital_usdt,
            max_position_pct=self._config.max_position_pct,
            max_open_positions=self._config.max_open_positions,
            max_drawdown_pct=self._config.max_drawdown_pct,
            enter_threshold=self._config.enter_threshold,
            monitor_threshold=self._config.monitor_threshold,
            paper_fee_rate=self._config.paper_fee_rate,
            paper_slippage_pct=self._config.paper_slippage_pct,
        )

        # ── Health + readiness ─────────────────────────────────────────────────
        health_items = self._health.probe_all(
            config=self._config,
            journal_path=self._journal.path,
            project_root=self._project_root,
        )
        readiness_items = self._readiness.evaluate(
            config=self._config,
            journal_entry_count=len(entries),
            portfolio_available=self._portfolio is not None,
            journal_path=self._journal.path,
            project_root=self._project_root,
            portfolio_persistence_status=portfolio_persistence_status,
            credential_check_status=runner_state_snapshot.credential_check if runner_state_snapshot else None,
            runner_state_status=runner_state_snapshot.status if runner_state_snapshot else None,
        )
        runtime_gaps = _build_runtime_gaps(
            self._config,
            self._portfolio,
            portfolio_persistence_status=portfolio_persistence_status,
            runner_state=runner_state_snapshot,
        )

        return DashboardSnapshot(
            mode="PAPER" if self._config.paper_trading else "LIVE",
            symbols=[str(s) for s in self._config.symbols],
            as_of=datetime.now(tz=timezone.utc),
            last_journal_write=last_entry.cycle_timestamp if last_entry else None,
            last_pipeline_run=last_entry.cycle_timestamp if last_entry else None,
            loop_running=loop_running,
            journal_entry_count=len(entries),
            portfolio=portfolio_summary,
            risk_config=risk_config,
            open_positions=positions,
            recent_decisions=recent_decisions,
            recent_fills=recent_fills,
            health=health_items,
            readiness=readiness_items,
            runtime_gaps=runtime_gaps,
            runner_state=runner_state_snapshot,
            portfolio_persistence=portfolio_persistence_status,
        )
