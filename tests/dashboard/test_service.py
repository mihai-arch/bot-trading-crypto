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
- health items always present (9 items)
- readiness items always present (8 items)
- No exceptions on missing data (all None-safe)
- Decisions limited to last 20
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

_TS_BASE = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)


def _ts(offset_seconds: int = 0) -> datetime:
    from datetime import timedelta
    return _TS_BASE.replace(second=0) + timedelta(seconds=offset_seconds)


def _config(**kwargs) -> BITConfig:
    defaults = dict(bybit_api_key="", bybit_api_secret="", paper_trading=True)
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
    config = _config(paper_trading=paper_trading)
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

    def test_loop_running_always_false(self, tmp_path):
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

    def test_open_position_mark_price_from_tracker(self, tmp_path):
        """When the tracker has a stored mark price, PositionRow shows it."""
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        fill = Fill(
            order_id=str(uuid4()),
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60000"),
            fee_usdt=Decimal("0.06"),
            slippage_usdt=Decimal("0"),
            filled_at=_TS_BASE,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        tracker.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))
        service = _make_service(tmp_path, portfolio=tracker)
        pos = service.build_snapshot().open_positions[0]
        assert pos.mark_price == Decimal("62000")

    def test_open_position_unrealized_pnl_from_tracker(self, tmp_path):
        """When a mark price is stored, unrealized_pnl is computed (not None)."""
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        fill = Fill(
            order_id=str(uuid4()),
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60000"),
            fee_usdt=Decimal("0.06"),
            slippage_usdt=Decimal("0"),
            filled_at=_TS_BASE,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        tracker.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))
        service = _make_service(tmp_path, portfolio=tracker)
        pos = service.build_snapshot().open_positions[0]
        # (62000 - 60000) * 0.001 = $2
        assert pos.unrealized_pnl == Decimal("2")

    def test_total_equity_reflects_mark_price(self, tmp_path):
        """portfolio.total_equity_usdt uses stored mark price once available."""
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        fill = Fill(
            order_id=str(uuid4()),
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60000"),
            fee_usdt=Decimal("0.06"),
            slippage_usdt=Decimal("0"),
            filled_at=_TS_BASE,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        tracker.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))
        service = _make_service(tmp_path, portfolio=tracker)
        snap = service.build_snapshot()
        # cash = 500 - 60.06 = 439.94; position value at mark = 0.001 * 62000 = 62
        expected = Decimal("439.94") + Decimal("0.001") * Decimal("62000")
        assert snap.portfolio.total_equity_usdt == expected


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
    def test_health_has_nine_items(self, tmp_path):
        service = _make_service(tmp_path)
        assert len(service.build_snapshot().health) == 9

    def test_readiness_has_eight_items(self, tmp_path):
        service = _make_service(tmp_path)
        assert len(service.build_snapshot().readiness) == 8

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

    def test_scheduler_health_is_missing(self, tmp_path):
        service = _make_service(tmp_path)
        item = next(i for i in service.build_snapshot().health if i.name == "Scheduler / Loop")
        assert item.status == ServiceStatus.MISSING

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

    def test_in_memory_gap_when_portfolio_injected(self, tmp_path):
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        service = _make_service(tmp_path, portfolio=tracker)
        gaps = service.build_snapshot().runtime_gaps
        assert any("in-memory" in g.label.lower() for g in gaps)

    def test_docker_gap_always_present(self, tmp_path):
        service = _make_service(tmp_path)
        gaps = service.build_snapshot().runtime_gaps
        assert any("docker" in g.label.lower() for g in gaps)

    def test_all_gaps_have_detail(self, tmp_path):
        service = _make_service(tmp_path)
        for gap in service.build_snapshot().runtime_gaps:
            assert gap.detail, f"Gap {gap.label!r} has no detail"
