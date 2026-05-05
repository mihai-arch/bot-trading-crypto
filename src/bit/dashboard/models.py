"""
Dashboard DTOs

Presentation-layer models assembled by DashboardService.
These are separate from core domain models — they are summaries for display,
not trading logic contracts.

No fake data. Every field that cannot be populated is explicitly None
or an empty list. Callers must handle None.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class ServiceStatus(StrEnum):
    """Structural implementation status of a service."""

    IMPLEMENTED = "IMPLEMENTED"
    PARTIAL = "PARTIAL"       # Some methods implemented; others raise NotImplementedError
    STUB = "STUB"             # Class exists but core method raises NotImplementedError
    MISSING = "MISSING"       # Service does not yet exist in the codebase
    DEGRADED = "DEGRADED"     # Exists but a runtime check failed (e.g. path not writable)


class ReadinessStatus(StrEnum):
    """Operational readiness status for continuous paper trading."""

    READY = "READY"
    WARNING = "WARNING"   # Present but with a known limitation
    MISSING = "MISSING"   # Absent and blocks continuous paper trading


class HealthItem(BaseModel):
    """Structural availability probe for one service."""

    name: str
    status: ServiceStatus
    detail: str | None = None


class ReadinessItem(BaseModel):
    """One item in the paper trading readiness checklist."""

    key: str                    # Machine-readable ID (unique per item)
    label: str                  # Human-readable description
    status: ReadinessStatus
    detail: str | None = None   # Optional explanation or caveat


class PositionRow(BaseModel):
    """One open position for display."""

    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    mark_price: Decimal | None = None       # None = no live price feed
    unrealized_pnl: Decimal | None = None   # None when mark_price is unavailable


class DecisionRow(BaseModel):
    """One pipeline cycle decision mapped from a JournalEntry."""

    timestamp: datetime
    symbol: str
    state: str                      # "ENTER" | "MONITOR" | "REJECT"
    composite_score: Decimal
    strategy_selected: str | None   # strategy_id with highest score; None if all scored 0
    fill_price: Decimal | None
    fill_qty: Decimal | None
    fee_usdt: Decimal | None
    is_paper: bool


class FillRow(BaseModel):
    """One executed fill mapped from a JournalEntry that has fill data."""

    timestamp: datetime
    symbol: str
    side: str           # "BUY" — v1 long-only; all pipeline fills are BUY entries
    qty: Decimal
    fill_price: Decimal
    fee_usdt: Decimal
    is_paper: bool


class PortfolioSummary(BaseModel):
    """Snapshot of the paper portfolio — from live tracker or persisted file."""

    total_equity_usdt: Decimal
    available_usdt: Decimal
    realized_pnl_usdt: Decimal
    open_position_count: int
    is_persistent: bool = False     # False = in-memory only; state resets on restart
    data_source: Literal["live", "persisted"] | None = None
    """
    "live"      — data from an injected in-memory PaperPortfolioTracker (real-time).
    "persisted" — data loaded from portfolio_state.json written by the bot process.
    None        — no data available (no tracker and no valid state file).
    """
    saved_at: datetime | None = None
    """Set only when data_source == "persisted"; the timestamp of the last save."""


class RiskConfig(BaseModel):
    """Risk and simulation parameters from BITConfig."""

    capital_usdt: Decimal
    max_position_pct: Decimal
    max_open_positions: int
    max_drawdown_pct: Decimal
    enter_threshold: Decimal
    monitor_threshold: Decimal
    paper_fee_rate: Decimal
    paper_slippage_pct: Decimal


class RuntimeGap(BaseModel):
    """A known runtime gap or blocker that prevents continuous paper trading."""

    label: str    # Short description (shown as warning header)
    detail: str   # Explanation and suggested action


class PersistenceStatus(StrEnum):
    """Status of a persisted state file on disk."""

    OK = "ok"           # File exists and parsed successfully
    NOT_FOUND = "not_found"   # No file yet — first run or persistence not set up
    CORRUPT = "corrupt"       # File exists but cannot be parsed


class RunnerStateSnapshot(BaseModel):
    """
    Persisted runner state as read by the dashboard.

    Populated from the runner_state.json file on disk.
    None if the file does not exist or is corrupt.
    """

    status: str                             # RunnerStatus value
    startup_validated: bool
    startup_error: str | None
    last_heartbeat: datetime | None
    last_cycle_start: datetime | None
    last_cycle_end: datetime | None
    last_successful_cycle: datetime | None
    last_error: str | None
    processed_symbols: list[str]
    updated_at: datetime
    state_age_seconds: float | None = None  # Seconds since file was last written
    credential_check: str | None = None     # "ok" / "skipped" / "failed: ..." / None


class DashboardSnapshot(BaseModel):
    """
    Complete dashboard state snapshot.

    Built by DashboardService from all available sources.
    Serialises to JSON via /api/snapshot. Rendered to HTML via Jinja2.

    Fields that cannot be populated are None or empty lists — never fake values.
    """

    # ── Header ────────────────────────────────────────────────────────────────
    mode: str                               # "PAPER" or "LIVE"
    symbols: list[str]                      # Configured trading symbols
    as_of: datetime                         # When this snapshot was built (UTC)
    last_journal_write: datetime | None     # Most recent JournalEntry.cycle_timestamp
    last_pipeline_run: datetime | None      # Same as last_journal_write in v1
    loop_running: bool = False              # True only if runner state file is recent+running
    journal_entry_count: int

    # ── Sections ──────────────────────────────────────────────────────────────
    portfolio: PortfolioSummary | None      # None if tracker not injected into dashboard
    risk_config: RiskConfig
    open_positions: list[PositionRow]       # Empty if tracker not injected
    recent_decisions: list[DecisionRow]     # Last 20 entries, newest first
    recent_fills: list[FillRow]             # Last 20 fills, newest first
    health: list[HealthItem]                # One per service; structural probes only
    readiness: list[ReadinessItem]          # Paper trading readiness checklist
    runtime_gaps: list[RuntimeGap]          # Known blockers and limitations

    # ── Persistence ────────────────────────────────────────────────────────────
    runner_state: RunnerStateSnapshot | None = None
    """Persisted runner state from disk. None if file absent or unreadable."""
    portfolio_persistence: str = PersistenceStatus.NOT_FOUND
    """Status of the portfolio state file: ok / not_found / corrupt."""
