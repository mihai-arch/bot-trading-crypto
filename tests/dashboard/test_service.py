"""
Tests for DashboardService.build_snapshot().

Verifies:
- Returns a valid DashboardSnapshot in all cases
- Empty journal → empty decisions and fills lists
- Journal with entries → decisions populated newest-first
- Journal with fills → fills extracted correctly
- No portfolio tracker → portfolio is None, positions empty
- Portfolio tracker injected → portfolio reflects tracker state
- mode = "PAPER" when paper_trading=True; "LIVE" when False
- last_journal_write = most recent entry timestamp; None when empty
- runtime_gaps always present
- health items always present (10 items)
- readiness items always present (10 items)
- No exceptions on missing data (all None-safe)
- Decisions limited to last 20
- Runner state read from persisted file when available
- Portfolio persistence status surfaced in snapshot
- loop_running derived from runner state file
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from bit.config import BITConfig
from bit.dashboard.models import ReadinessStatus, ServiceStatus
from bit.dashboard.service import DashboardService
from bit.domain.enums import DecisionState, OrderSide, OrderStatus, StrategyId, Symbol
from bit.domain.execution import Fill
from bit.domain.journal import JournalEntry
from bit.services.journal import JournalLearningStore
from bit.services.paper_portfolio import PaperPortfolioTracker
from bit.services.runner_state import RunnerState, RunnerStateStore, RunnerStatus

_TS_BASE = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)


def _ts(offset_seconds: int = 0) -> datetime:
    from datetime import timedelta
    return _TS_BASE.replace(second=0) + timedelta(seconds=offset_seconds)


def _config(tmp_path: Path | None = None, **kwargs) -> BITConfig:
    defaults = dict(bybit_api_key="", bybit_api_secret="", paper_trading=True)
    if tmp_path is not None:
        defaults["portfolio_state_path"] = tmp_path / "portfolio_state.json"
        defaults["runner_state_path"] = tmp_path / "runner_state.json"
    defaults.update(kwargs)
    return BITConfig(**defaults)


def _make_entry(
    state: DecisionState = DecisionState.MONITOR,
    score: str = "0.45",
    ts: datetime | None = None,
    fill_price: str | None = None,
    fill_qty: str | None = None,
    fee: str | None = None,
) -> JournalEntry:
    return JournalEntry(
        entry_id=str(uuid4()),
        symbol=Symbol.BTCUSDT,
        cycle_timestamp=ts or _TS_BASE,
        decision_state=state,
        contributing_strategies=[StrategyId.TREND_CONTINUATION],
        composite_score=Decimal(score),
        rationale="test",
        fill_price=Decimal(fill_price) if fill_price else None,
        fill_qty=Decimal(fill_qty) if fill_qty else None,
        fee_usdt=Decimal(fee) if fee else None,
        is_paper=True,
        raw_signal_scores={"trend_continuation": float(score)},
    )


def _make_journal(tmp_path: Path, entries: list[JournalEntry]) -> JournalLearningStore:
    config = _config()
    journal = JournalLearningStore(config, journal_path=tmp_path / "journal.jsonl")
    for entry in entries:
        journal.record(entry)
    return journal


def _make_service(
    tmp_path: Path,
    entries: list[JournalEntry] | None = None,
    portfolio: PaperPortfolioTracker | None = None,
    paper_trading: bool = True,
) -> DashboardService:
    config = _config(tmp_path=tmp_path, paper_trading=paper_trading)
    journal = _make_journal(tmp_path, entries or [])
    return DashboardService(
        config=config,
        journal=journal,
        portfolio=portfolio,
        project_root=tmp_path,
    )


# ── Basic construction ─────────────────────────────────────────────────────

class TestBuildSnapshotBasic:
    def test_returns_dashboard_snapshot(self, tmp_path):
        from bit.dashboard.models import DashboardSnapshot
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert isinstance(snap, DashboardSnapshot)

    def test_mode_paper(self, tmp_path):
        service = _make_service(tmp_path, paper_trading=True)
        assert service.build_snapshot().mode == "PAPER"

    def test_mode_live(self, tmp_path):
        service = _make_service(tmp_path, paper_trading=False)
        assert service.build_snapshot().mode == "LIVE"

    def test_loop_running_false_when_no_state_file(self, tmp_path):
        service = _make_service(tmp_path)
        assert service.build_snapshot().loop_running is False

    def test_symbols_from_config(self, tmp_path):
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert "BTCUSDT" in snap.symbols
        assert "ETHUSDT" in snap.symbols

    def test_as_of_is_recent_utc(self, tmp_path):
        from datetime import timedelta
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        now = datetime.now(tz=timezone.utc)
        assert abs((now - snap.as_of).total_seconds()) < 5


# ── Journal / decisions ────────────────────────────────────────────────────

class TestDecisions:
    def test_empty_journal_gives_zero_decisions(self, tmp_path):
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.recent_decisions == []
        assert snap.journal_entry_count == 0

    def test_last_journal_write_none_when_empty(self, tmp_path):
        service = _make_service(tmp_path)
        assert service.build_snapshot().last_journal_write is None

    def test_last_journal_write_matches_most_recent(self, tmp_path):
        entries = [
            _make_entry(ts=_ts(0)),
            _make_entry(ts=_ts(60)),
            _make_entry(ts=_ts(120)),
        ]
        service = _make_service(tmp_path, entries=entries)
        snap = service.build_snapshot()
        assert snap.last_journal_write == _ts(120)

    def test_journal_entry_count(self, tmp_path):
        entries = [_make_entry() for _ in range(5)]
        service = _make_service(tmp_path, entries=entries)
        assert service.build_snapshot().journal_entry_count == 5

    def test_decisions_are_newest_first(self, tmp_path):
        entries = [_make_entry(ts=_ts(i * 60)) for i in range(3)]
        service = _make_service(tmp_path, entries=entries)
        snap = service.build_snapshot()
        timestamps = [d.timestamp for d in snap.recent_decisions]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_decisions_capped_at_twenty(self, tmp_path):
        entries = [_make_entry(ts=_ts(i * 60)) for i in range(25)]
        service = _make_service(tmp_path, entries=entries)
        snap = service.build_snapshot()
        assert len(snap.recent_decisions) == 20

    def test_decision_state_preserved(self, tmp_path):
        entries = [
            _make_entry(state=DecisionState.ENTER, score="0.80"),
            _make_entry(state=DecisionState.REJECT, score="0.20"),
        ]
        service = _make_service(tmp_path, entries=entries)
        states = {d.state for d in service.build_snapshot().recent_decisions}
        assert "ENTER" in states
        assert "REJECT" in states

    def test_decision_symbol_is_string(self, tmp_path):
        service = _make_service(tmp_path, entries=[_make_entry()])
        row = service.build_snapshot().recent_decisions[0]
        assert isinstance(row.symbol, str)


# ── Fills ──────────────────────────────────────────────────────────────────

class TestFills:
    def test_no_fills_when_no_fill_entries(self, tmp_path):
        entries = [_make_entry(state=DecisionState.MONITOR) for _ in range(5)]
        service = _make_service(tmp_path, entries=entries)
        assert service.build_snapshot().recent_fills == []

    def test_fills_extracted_from_enter_entries(self, tmp_path):
        entries = [
            _make_entry(state=DecisionState.ENTER, fill_price="60000", fill_qty="0.001", fee="0.06"),
            _make_entry(state=DecisionState.MONITOR),
        ]
        service = _make_service(tmp_path, entries=entries)
        snap = service.build_snapshot()
        assert len(snap.recent_fills) == 1
        assert snap.recent_fills[0].fill_price == Decimal("60000")

    def test_fills_are_newest_first(self, tmp_path):
        entries = [
            _make_entry(fill_price="60000", fill_qty="0.001", fee="0.06", ts=_ts(0)),
            _make_entry(fill_price="61000", fill_qty="0.001", fee="0.061", ts=_ts(60)),
        ]
        service = _make_service(tmp_path, entries=entries)
        fills = service.build_snapshot().recent_fills
        assert fills[0].fill_price == Decimal("61000")

    def test_fills_capped_at_twenty(self, tmp_path):
        entries = [
            _make_entry(fill_price="60000", fill_qty="0.001", fee="0.06", ts=_ts(i * 60))
            for i in range(25)
        ]
        service = _make_service(tmp_path, entries=entries)
        assert len(service.build_snapshot().recent_fills) == 20

    def test_fill_side_is_buy(self, tmp_path):
        entries = [_make_entry(fill_price="60000", fill_qty="0.001")]
        service = _make_service(tmp_path, entries=entries)
        fill = service.build_snapshot().recent_fills[0]
        assert fill.side == "BUY"


# ── Portfolio ──────────────────────────────────────────────────────────────

class TestPortfolio:
    def test_portfolio_none_when_not_injected(self, tmp_path):
        service = _make_service(tmp_path, portfolio=None)
        snap = service.build_snapshot()
        assert snap.portfolio is None
        assert snap.open_positions == []

    def test_portfolio_summary_from_tracker(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert snap.portfolio is not None
        assert snap.portfolio.available_usdt == Decimal("500")
        assert snap.portfolio.total_equity_usdt == Decimal("500")
        assert snap.portfolio.realized_pnl_usdt == Decimal("0")
        assert snap.portfolio.open_position_count == 0

    def test_portfolio_is_not_persistent(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        service = _make_service(tmp_path, portfolio=tracker)
        assert service.build_snapshot().portfolio.is_persistent is False

    def test_open_positions_from_tracker(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        fill = Fill(
            order_id=str(uuid4()),
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60000"),
            fee_usdt=Decimal("0.06"),
            slippage_usdt=Decimal("0.03"),
            filled_at=_TS_BASE,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert len(snap.open_positions) == 1
        pos = snap.open_positions[0]
        assert pos.symbol == "BTCUSDT"
        assert pos.qty == Decimal("0.001")
        assert pos.avg_entry_price == Decimal("60000")

    def test_open_position_mark_price_is_none(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        fill = Fill(
            order_id=str(uuid4()),
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60000"),
            fee_usdt=Decimal("0.06"),
            slippage_usdt=Decimal("0.03"),
            filled_at=_TS_BASE,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        service = _make_service(tmp_path, portfolio=tracker)
        pos = service.build_snapshot().open_positions[0]
        assert pos.mark_price is None
        assert pos.unrealized_pnl is None

    def test_portfolio_count_matches_positions(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        fill = Fill(
            order_id=str(uuid4()),
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60000"),
            fee_usdt=Decimal("0.06"),
            slippage_usdt=Decimal("0.03"),
            filled_at=_TS_BASE,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert snap.portfolio.open_position_count == len(snap.open_positions)


# ── Risk config ────────────────────────────────────────────────────────────

class TestRiskConfig:
    def test_risk_config_from_bit_config(self, tmp_path):
        service = _make_service(tmp_path)
        rc = service.build_snapshot().risk_config
        assert rc.capital_usdt == Decimal("500")
        assert rc.max_position_pct == Decimal("0.20")
        assert rc.max_open_positions == 3
        assert rc.enter_threshold == Decimal("0.65")
        assert rc.monitor_threshold == Decimal("0.40")


# ── Health and readiness ───────────────────────────────────────────────────

class TestHealthAndReadiness:
    def test_health_has_ten_items(self, tmp_path):
        service = _make_service(tmp_path)
        assert len(service.build_snapshot().health) == 10

    def test_readiness_has_ten_items(self, tmp_path):
        service = _make_service(tmp_path)
        assert len(service.build_snapshot().readiness) == 10

    def test_health_items_are_health_item_type(self, tmp_path):
        from bit.dashboard.models import HealthItem
        service = _make_service(tmp_path)
        for item in service.build_snapshot().health:
            assert isinstance(item, HealthItem)

    def test_readiness_items_are_readiness_item_type(self, tmp_path):
        from bit.dashboard.models import ReadinessItem
        service = _make_service(tmp_path)
        for item in service.build_snapshot().readiness:
            assert isinstance(item, ReadinessItem)

    def test_scheduler_health_is_implemented(self, tmp_path):
        service = _make_service(tmp_path)
        item = next(i for i in service.build_snapshot().health if i.name == "Scheduler / Loop")
        assert item.status == ServiceStatus.IMPLEMENTED

    def test_scheduler_readiness_is_missing(self, tmp_path):
        service = _make_service(tmp_path)
        item = next(
            i for i in service.build_snapshot().readiness if i.key == "scheduler"
        )
        assert item.status == ReadinessStatus.MISSING

    def test_config_readiness_always_ready(self, tmp_path):
        service = _make_service(tmp_path)
        item = next(i for i in service.build_snapshot().readiness if i.key == "config")
        assert item.status == ReadinessStatus.READY


# ── Runtime gaps ───────────────────────────────────────────────────────────

class TestRuntimeGaps:
    def test_runtime_gaps_present(self, tmp_path):
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert len(snap.runtime_gaps) > 0

    def test_no_loop_gap_always_present(self, tmp_path):
        service = _make_service(tmp_path)
        gaps = service.build_snapshot().runtime_gaps
        labels = [g.label for g in gaps]
        assert any("loop" in label.lower() or "scheduler" in label.lower() for label in labels)

    def test_no_portfolio_gap_when_not_injected(self, tmp_path):
        service = _make_service(tmp_path, portfolio=None)
        gaps = service.build_snapshot().runtime_gaps
        assert any("not injected" in g.label.lower() for g in gaps)

    def test_in_memory_gap_when_portfolio_injected_no_persistence(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        service = _make_service(tmp_path, portfolio=tracker)
        gaps = service.build_snapshot().runtime_gaps
        # No state file yet → "in-memory only (not yet persisted)"
        assert any("in-memory" in g.label.lower() for g in gaps)

    def test_docker_gap_always_present(self, tmp_path):
        service = _make_service(tmp_path)
        gaps = service.build_snapshot().runtime_gaps
        assert any("docker" in g.label.lower() for g in gaps)

    def test_all_gaps_have_detail(self, tmp_path):
        service = _make_service(tmp_path)
        for gap in service.build_snapshot().runtime_gaps:
            assert gap.detail, f"Gap {gap.label!r} has no detail"


# ── Runner state from persisted file ──────────────────────────────────────────

class TestRunnerStatePersistence:
    def test_runner_state_none_when_no_file(self, tmp_path):
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.runner_state is None

    def test_runner_state_populated_from_file(self, tmp_path):
        state = RunnerState(
            updated_at=_TS_BASE,
            status=RunnerStatus.RUNNING,
            startup_validated=True,
            processed_symbols=["BTCUSDT"],
        )
        RunnerStateStore.write(state, tmp_path / "runner_state.json")
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.runner_state is not None
        assert snap.runner_state.status == "running"
        assert snap.runner_state.startup_validated is True
        assert snap.runner_state.processed_symbols == ["BTCUSDT"]

    def test_loop_running_true_when_state_is_recent_running(self, tmp_path):
        from datetime import timedelta
        recent_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        state = RunnerState(
            updated_at=recent_ts,
            status=RunnerStatus.RUNNING,
        )
        RunnerStateStore.write(state, tmp_path / "runner_state.json")
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.loop_running is True

    def test_loop_running_true_within_interval_threshold(self, tmp_path):
        """loop_running stays True for files up to run_interval_seconds + 60 seconds old."""
        import os
        import time
        from datetime import timedelta
        cfg = _config(tmp_path=tmp_path, run_interval_seconds=300)
        state = RunnerState(updated_at=_TS_BASE, status=RunnerStatus.RUNNING)
        state_path = tmp_path / "runner_state.json"
        RunnerStateStore.write(state, state_path)
        # Backdate file mtime to 350s ago — inside the 360s window (300 + 60)
        target_mtime = time.time() - 350
        os.utime(state_path, (target_mtime, target_mtime))
        journal = _make_journal(tmp_path, [])
        service = DashboardService(config=cfg, journal=journal, project_root=tmp_path)
        assert service.build_snapshot().loop_running is True

    def test_loop_running_false_when_beyond_threshold(self, tmp_path):
        """loop_running is False when file is older than run_interval_seconds + 60."""
        import os
        import time
        cfg = _config(tmp_path=tmp_path, run_interval_seconds=300)
        state = RunnerState(updated_at=_TS_BASE, status=RunnerStatus.RUNNING)
        state_path = tmp_path / "runner_state.json"
        RunnerStateStore.write(state, state_path)
        # Backdate file mtime to 370s ago — beyond the 360s window (300 + 60)
        target_mtime = time.time() - 370
        os.utime(state_path, (target_mtime, target_mtime))
        journal = _make_journal(tmp_path, [])
        service = DashboardService(config=cfg, journal=journal, project_root=tmp_path)
        assert service.build_snapshot().loop_running is False

    def test_loop_running_false_when_state_is_stopped(self, tmp_path):
        from datetime import timedelta
        recent_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        state = RunnerState(
            updated_at=recent_ts,
            status=RunnerStatus.STOPPED,
        )
        RunnerStateStore.write(state, tmp_path / "runner_state.json")
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.loop_running is False

    def test_loop_running_false_when_no_file(self, tmp_path):
        service = _make_service(tmp_path)
        assert service.build_snapshot().loop_running is False

    def test_runner_state_includes_state_age(self, tmp_path):
        state = RunnerState(updated_at=_TS_BASE, status=RunnerStatus.RUNNING)
        RunnerStateStore.write(state, tmp_path / "runner_state.json")
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.runner_state.state_age_seconds is not None
        assert snap.runner_state.state_age_seconds >= 0

    def test_runner_state_shows_in_runtime_gaps(self, tmp_path):
        from datetime import timedelta
        recent_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=5)
        state = RunnerState(
            updated_at=recent_ts,
            status=RunnerStatus.RUNNING,
            processed_symbols=["BTCUSDT"],
        )
        RunnerStateStore.write(state, tmp_path / "runner_state.json")
        service = _make_service(tmp_path)
        gaps = service.build_snapshot().runtime_gaps
        # Runner state gap should mention status
        assert any("runner" in g.label.lower() or "running" in g.label.lower() for g in gaps)

    def test_corrupt_runner_file_gives_none_state(self, tmp_path):
        (tmp_path / "runner_state.json").write_text("garbage", encoding="utf-8")
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.runner_state is None


# ── Portfolio persistence status ──────────────────────────────────────────────

class TestPortfolioPersistenceStatus:
    def test_not_found_when_no_file(self, tmp_path):
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.portfolio_persistence == "not_found"

    def test_ok_when_file_present(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.portfolio_persistence == "ok"

    def test_corrupt_when_file_bad(self, tmp_path):
        (tmp_path / "portfolio_state.json").write_text("garbage", encoding="utf-8")
        service = _make_service(tmp_path)
        snap = service.build_snapshot()
        assert snap.portfolio_persistence == "corrupt"

    def test_is_persistent_true_when_state_file_ok(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert snap.portfolio is not None
        assert snap.portfolio.is_persistent is True

    def test_is_persistent_false_when_no_state_file(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert snap.portfolio is not None
        assert snap.portfolio.is_persistent is False


# ── Portfolio fallback from persisted file ─────────────────────────────────

class TestPortfolioFallback:
    def test_portfolio_none_when_no_tracker_no_file(self, tmp_path):
        service = _make_service(tmp_path, portfolio=None)
        snap = service.build_snapshot()
        assert snap.portfolio is None
        assert snap.open_positions == []

    def test_portfolio_data_source_persisted_when_file_exists(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path, portfolio=None)
        snap = service.build_snapshot()
        assert snap.portfolio is not None
        assert snap.portfolio.data_source == "persisted"

    def test_portfolio_data_source_live_when_tracker_injected(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert snap.portfolio is not None
        assert snap.portfolio.data_source == "live"

    def test_portfolio_saved_at_set_from_file(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path, portfolio=None)
        snap = service.build_snapshot()
        assert snap.portfolio is not None
        assert snap.portfolio.saved_at is not None

    def test_portfolio_saved_at_none_for_live_tracker(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert snap.portfolio.saved_at is None

    def test_open_positions_from_persisted_file(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        fill = Fill(
            order_id=str(uuid4()),
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60000"),
            fee_usdt=Decimal("0.06"),
            slippage_usdt=Decimal("0.03"),
            filled_at=_TS_BASE,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path, portfolio=None)
        snap = service.build_snapshot()
        assert len(snap.open_positions) == 1
        assert snap.open_positions[0].symbol == "BTCUSDT"

    def test_portfolio_none_when_file_corrupt(self, tmp_path):
        (tmp_path / "portfolio_state.json").write_text("garbage", encoding="utf-8")
        service = _make_service(tmp_path, portfolio=None)
        snap = service.build_snapshot()
        assert snap.portfolio is None

    def test_runtime_gap_persisted_label_when_file_ok(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path, portfolio=None)
        gaps = service.build_snapshot().runtime_gaps
        assert any("persisted snapshot" in g.label.lower() for g in gaps)

    def test_live_tracker_preferred_over_file(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        assert snap.portfolio.data_source == "live"

    def test_persisted_equity_matches_saved_state(self, tmp_path):
        from bit.services.portfolio_store import PortfolioStateStore
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        PortfolioStateStore.save(tracker, tmp_path / "portfolio_state.json")
        service = _make_service(tmp_path, portfolio=None)
        snap = service.build_snapshot()
        assert snap.portfolio.total_equity_usdt == Decimal("500")
        assert snap.portfolio.available_usdt == Decimal("500")
