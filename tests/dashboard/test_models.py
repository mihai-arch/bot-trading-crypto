"""
Tests for dashboard DTO models and mapping helpers.

Verifies:
- Model construction and field defaults
- _entry_to_decision_row mapping
- _entry_to_fill_row mapping (fill present / absent)
- DashboardSnapshot JSON serialisation round-trip
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from bit.dashboard.models import (
    DashboardSnapshot,
    DecisionRow,
    FillRow,
    HealthItem,
    PersistenceStatus,
    PortfolioSummary,
    PositionRow,
    ReadinessItem,
    ReadinessStatus,
    RiskConfig,
    RuntimeGap,
    RunnerStateSnapshot,
    ServiceStatus,
)
from bit.dashboard.service import _entry_to_decision_row, _entry_to_fill_row
from bit.domain.enums import DecisionState, StrategyId, Symbol
from bit.domain.journal import JournalEntry

_TS = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_entry(
    state: DecisionState = DecisionState.MONITOR,
    score: str = "0.45",
    fill_price: str | None = None,
    fill_qty: str | None = None,
    fee: str | None = None,
    raw_scores: dict | None = None,
) -> JournalEntry:
    return JournalEntry(
        entry_id=str(uuid4()),
        symbol=Symbol.BTCUSDT,
        cycle_timestamp=_TS,
        decision_state=state,
        contributing_strategies=[StrategyId.TREND_CONTINUATION],
        composite_score=Decimal(score),
        rationale="test rationale",
        fill_price=Decimal(fill_price) if fill_price else None,
        fill_qty=Decimal(fill_qty) if fill_qty else None,
        fee_usdt=Decimal(fee) if fee else None,
        is_paper=True,
        raw_signal_scores=raw_scores if raw_scores is not None else {"trend_continuation": float(score)},
    )


# ── HealthItem ─────────────────────────────────────────────────────────────

class TestHealthItem:
    def test_implemented_status(self):
        item = HealthItem(name="FeatureEngine", status=ServiceStatus.IMPLEMENTED)
        assert item.status == ServiceStatus.IMPLEMENTED
        assert item.detail is None

    def test_missing_status_with_detail(self):
        item = HealthItem(
            name="Scheduler",
            status=ServiceStatus.MISSING,
            detail="No loop exists.",
        )
        assert item.status == ServiceStatus.MISSING
        assert "loop" in item.detail

    def test_partial_status(self):
        item = HealthItem(name="MarketDataService", status=ServiceStatus.PARTIAL)
        assert item.status == ServiceStatus.PARTIAL

    def test_degraded_status(self):
        item = HealthItem(name="Journal", status=ServiceStatus.DEGRADED, detail="Not writable.")
        assert item.status == ServiceStatus.DEGRADED


# ── ReadinessItem ──────────────────────────────────────────────────────────

class TestReadinessItem:
    def test_ready_item(self):
        item = ReadinessItem(key="config", label="Config loaded", status=ReadinessStatus.READY)
        assert item.status == ReadinessStatus.READY

    def test_missing_item_has_key(self):
        item = ReadinessItem(
            key="scheduler",
            label="No loop",
            status=ReadinessStatus.MISSING,
            detail="Add runner.",
        )
        assert item.key == "scheduler"
        assert item.detail == "Add runner."

    def test_warning_item(self):
        item = ReadinessItem(
            key="portfolio",
            label="In-memory only",
            status=ReadinessStatus.WARNING,
        )
        assert item.status == ReadinessStatus.WARNING


# ── _entry_to_decision_row ─────────────────────────────────────────────────

class TestEntryToDecisionRow:
    def test_basic_mapping(self):
        entry = _make_entry(state=DecisionState.MONITOR, score="0.45")
        row = _entry_to_decision_row(entry)
        assert row.symbol == "BTCUSDT"
        assert row.state == "MONITOR"
        assert row.composite_score == Decimal("0.45")
        assert row.timestamp == _TS
        assert row.is_paper is True

    def test_strategy_selected_from_raw_scores(self):
        entry = _make_entry(
            state=DecisionState.ENTER,
            score="0.70",
            raw_scores={"trend_continuation": 0.70, "breakout_confirmation": 0.0},
        )
        row = _entry_to_decision_row(entry)
        assert row.strategy_selected == "trend_continuation"

    def test_strategy_selected_picks_highest(self):
        entry = _make_entry(
            raw_scores={"trend_continuation": 0.40, "breakout_confirmation": 0.75},
        )
        row = _entry_to_decision_row(entry)
        assert row.strategy_selected == "breakout_confirmation"

    def test_strategy_selected_none_when_all_zero(self):
        entry = _make_entry(
            raw_scores={"trend_continuation": 0.0, "breakout_confirmation": 0.0},
        )
        row = _entry_to_decision_row(entry)
        assert row.strategy_selected is None

    def test_strategy_selected_none_when_no_scores(self):
        entry = _make_entry(raw_scores={})
        row = _entry_to_decision_row(entry)
        assert row.strategy_selected is None

    def test_fill_fields_none_for_non_fill(self):
        entry = _make_entry(state=DecisionState.REJECT)
        row = _entry_to_decision_row(entry)
        assert row.fill_price is None
        assert row.fill_qty is None
        assert row.fee_usdt is None

    def test_fill_fields_populated_for_enter(self):
        entry = _make_entry(
            state=DecisionState.ENTER,
            score="0.75",
            fill_price="60000",
            fill_qty="0.001",
            fee="0.06",
        )
        row = _entry_to_decision_row(entry)
        assert row.fill_price == Decimal("60000")
        assert row.fill_qty == Decimal("0.001")
        assert row.fee_usdt == Decimal("0.06")


# ── _entry_to_fill_row ─────────────────────────────────────────────────────

class TestEntryToFillRow:
    def test_returns_none_for_no_fill(self):
        entry = _make_entry(state=DecisionState.MONITOR)
        assert _entry_to_fill_row(entry) is None

    def test_returns_none_when_only_price_set(self):
        # fill_qty missing → not a complete fill
        entry = _make_entry(fill_price="60000")
        assert _entry_to_fill_row(entry) is None

    def test_returns_fill_row_for_complete_fill(self):
        entry = _make_entry(
            state=DecisionState.ENTER,
            score="0.75",
            fill_price="60000",
            fill_qty="0.001",
            fee="0.06",
        )
        row = _entry_to_fill_row(entry)
        assert row is not None
        assert isinstance(row, FillRow)
        assert row.fill_price == Decimal("60000")
        assert row.qty == Decimal("0.001")
        assert row.fee_usdt == Decimal("0.06")
        assert row.symbol == "BTCUSDT"
        assert row.side == "BUY"
        assert row.is_paper is True

    def test_fee_defaults_to_zero_when_missing(self):
        entry = _make_entry(fill_price="60000", fill_qty="0.001", fee=None)
        row = _entry_to_fill_row(entry)
        assert row is not None
        assert row.fee_usdt == Decimal("0")

    def test_fill_row_timestamp_matches_entry(self):
        entry = _make_entry(fill_price="60000", fill_qty="0.001")
        row = _entry_to_fill_row(entry)
        assert row.timestamp == _TS


# ── DashboardSnapshot ──────────────────────────────────────────────────────

class TestDashboardSnapshot:
    def _minimal_risk_config(self) -> RiskConfig:
        return RiskConfig(
            capital_usdt=Decimal("500"),
            max_position_pct=Decimal("0.20"),
            max_open_positions=3,
            max_drawdown_pct=Decimal("0.10"),
            enter_threshold=Decimal("0.65"),
            monitor_threshold=Decimal("0.40"),
            paper_fee_rate=Decimal("0.001"),
            paper_slippage_pct=Decimal("0.0005"),
        )

    def test_snapshot_construction(self):
        snap = DashboardSnapshot(
            mode="PAPER",
            symbols=["BTCUSDT"],
            as_of=_TS,
            last_journal_write=None,
            last_pipeline_run=None,
            loop_running=False,
            journal_entry_count=0,
            portfolio=None,
            risk_config=self._minimal_risk_config(),
            open_positions=[],
            recent_decisions=[],
            recent_fills=[],
            health=[],
            readiness=[],
            runtime_gaps=[],
        )
        assert snap.mode == "PAPER"
        assert snap.loop_running is False
        assert snap.portfolio is None

    def test_snapshot_json_round_trip(self):
        snap = DashboardSnapshot(
            mode="PAPER",
            symbols=["BTCUSDT", "ETHUSDT"],
            as_of=_TS,
            last_journal_write=None,
            last_pipeline_run=None,
            loop_running=False,
            journal_entry_count=5,
            portfolio=PortfolioSummary(
                total_equity_usdt=Decimal("500"),
                available_usdt=Decimal("440"),
                realized_pnl_usdt=Decimal("2.50"),
                open_position_count=1,
            ),
            risk_config=self._minimal_risk_config(),
            open_positions=[],
            recent_decisions=[],
            recent_fills=[],
            health=[HealthItem(name="FeatureEngine", status=ServiceStatus.IMPLEMENTED)],
            readiness=[ReadinessItem(key="config", label="Loaded", status=ReadinessStatus.READY)],
            runtime_gaps=[RuntimeGap(label="No loop", detail="Add runner.")],
        )
        data = snap.model_dump(mode="json")
        assert data["mode"] == "PAPER"
        assert data["journal_entry_count"] == 5
        assert data["portfolio"]["open_position_count"] == 1
        assert data["health"][0]["status"] == "IMPLEMENTED"
        assert data["runtime_gaps"][0]["label"] == "No loop"

    def test_position_row_none_fields(self):
        pos = PositionRow(
            symbol="BTCUSDT",
            qty=Decimal("0.001"),
            avg_entry_price=Decimal("60000"),
        )
        assert pos.mark_price is None
        assert pos.unrealized_pnl is None

    def test_snapshot_has_persistence_defaults(self):
        snap = DashboardSnapshot(
            mode="PAPER",
            symbols=[],
            as_of=_TS,
            last_journal_write=None,
            last_pipeline_run=None,
            loop_running=False,
            journal_entry_count=0,
            portfolio=None,
            risk_config=self._minimal_risk_config(),
            open_positions=[],
            recent_decisions=[],
            recent_fills=[],
            health=[],
            readiness=[],
            runtime_gaps=[],
        )
        assert snap.runner_state is None
        assert snap.portfolio_persistence == PersistenceStatus.NOT_FOUND


# ── PortfolioSummary data_source / saved_at ────────────────────────────────────

class TestPortfolioSummaryDataSource:
    def test_data_source_defaults_to_none(self):
        ps = PortfolioSummary(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("500"),
            realized_pnl_usdt=Decimal("0"),
            open_position_count=0,
        )
        assert ps.data_source is None

    def test_saved_at_defaults_to_none(self):
        ps = PortfolioSummary(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("500"),
            realized_pnl_usdt=Decimal("0"),
            open_position_count=0,
        )
        assert ps.saved_at is None

    def test_data_source_live(self):
        ps = PortfolioSummary(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("500"),
            realized_pnl_usdt=Decimal("0"),
            open_position_count=0,
            data_source="live",
        )
        assert ps.data_source == "live"

    def test_data_source_persisted_with_saved_at(self):
        ps = PortfolioSummary(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("500"),
            realized_pnl_usdt=Decimal("0"),
            open_position_count=0,
            data_source="persisted",
            saved_at=_TS,
        )
        assert ps.data_source == "persisted"
        assert ps.saved_at == _TS

    def test_json_serialises_data_source_and_saved_at(self):
        ps = PortfolioSummary(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("500"),
            realized_pnl_usdt=Decimal("0"),
            open_position_count=0,
            data_source="persisted",
            saved_at=_TS,
        )
        data = ps.model_dump(mode="json")
        assert data["data_source"] == "persisted"
        assert data["saved_at"] is not None


# ── PersistenceStatus ──────────────────────────────────────────────────────────

class TestPersistenceStatus:
    def test_ok_value(self):
        assert PersistenceStatus.OK == "ok"

    def test_not_found_value(self):
        assert PersistenceStatus.NOT_FOUND == "not_found"

    def test_corrupt_value(self):
        assert PersistenceStatus.CORRUPT == "corrupt"


# ── RunnerStateSnapshot ────────────────────────────────────────────────────────

class TestRunnerStateSnapshot:
    def test_minimal_construction(self):
        snap = RunnerStateSnapshot(
            status="running",
            startup_validated=True,
            startup_error=None,
            last_heartbeat=None,
            last_cycle_start=None,
            last_cycle_end=None,
            last_successful_cycle=None,
            last_error=None,
            processed_symbols=[],
            updated_at=_TS,
        )
        assert snap.status == "running"
        assert snap.startup_validated is True
        assert snap.state_age_seconds is None

    def test_credential_check_defaults_to_none(self):
        snap = RunnerStateSnapshot(
            status="running",
            startup_validated=True,
            startup_error=None,
            last_heartbeat=None,
            last_cycle_start=None,
            last_cycle_end=None,
            last_successful_cycle=None,
            last_error=None,
            processed_symbols=[],
            updated_at=_TS,
        )
        assert snap.credential_check is None

    def test_credential_check_ok(self):
        snap = RunnerStateSnapshot(
            status="running",
            startup_validated=True,
            startup_error=None,
            last_heartbeat=None,
            last_cycle_start=None,
            last_cycle_end=None,
            last_successful_cycle=None,
            last_error=None,
            processed_symbols=[],
            updated_at=_TS,
            credential_check="ok",
        )
        assert snap.credential_check == "ok"

    def test_credential_check_skipped(self):
        snap = RunnerStateSnapshot(
            status="running",
            startup_validated=True,
            startup_error=None,
            last_heartbeat=None,
            last_cycle_start=None,
            last_cycle_end=None,
            last_successful_cycle=None,
            last_error=None,
            processed_symbols=[],
            updated_at=_TS,
            credential_check="skipped",
        )
        assert snap.credential_check == "skipped"

    def test_credential_check_serialises_in_json(self):
        snap = RunnerStateSnapshot(
            status="running",
            startup_validated=True,
            startup_error=None,
            last_heartbeat=None,
            last_cycle_start=None,
            last_cycle_end=None,
            last_successful_cycle=None,
            last_error=None,
            processed_symbols=[],
            updated_at=_TS,
            credential_check="ok",
        )
        data = snap.model_dump(mode="json")
        assert data["credential_check"] == "ok"

    def test_with_state_age(self):
        snap = RunnerStateSnapshot(
            status="stopped",
            startup_validated=False,
            startup_error="timeout",
            last_heartbeat=_TS,
            last_cycle_start=_TS,
            last_cycle_end=_TS,
            last_successful_cycle=_TS,
            last_error="timeout",
            processed_symbols=["BTCUSDT"],
            updated_at=_TS,
            state_age_seconds=45.5,
        )
        assert snap.state_age_seconds == 45.5
        assert snap.processed_symbols == ["BTCUSDT"]

    def test_json_serialises(self):
        snap = RunnerStateSnapshot(
            status="running",
            startup_validated=True,
            startup_error=None,
            last_heartbeat=_TS,
            last_cycle_start=None,
            last_cycle_end=None,
            last_successful_cycle=None,
            last_error=None,
            processed_symbols=["ETHUSDT"],
            updated_at=_TS,
        )
        data = snap.model_dump(mode="json")
        assert data["status"] == "running"
        assert data["processed_symbols"] == ["ETHUSDT"]
