"""
Tests for PortfolioStateStore — paper portfolio state persistence.

Verifies:
- save() creates a readable JSON file
- save() persists cash, realized PnL, positions, mark prices
- save() creates parent directories
- save() overwrites existing file cleanly
- save() leaves no .tmp file on success
- load() returns not_found when file is absent
- load() returns ok with correct tracker state on success
- load() returns corrupt on invalid JSON
- load() returns corrupt on wrong version
- load() returns corrupt without raising
- load() restores mark prices
- Round-trip: save then load gives identical state
- Round-trip: portfolio with open positions survives restart
- Round-trip: empty portfolio (no positions) survives restart
- status() returns not_found / ok / corrupt correctly
"""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from bit.domain.enums import OrderSide, Symbol
from bit.domain.execution import Fill
from bit.services.paper_portfolio import PaperPortfolioTracker
from bit.services.portfolio_store import PortfolioStateStore


def _make_tracker(cash: str = "500") -> PaperPortfolioTracker:
    return PaperPortfolioTracker(starting_cash=Decimal(cash))


def _make_fill(
    symbol: Symbol = Symbol.BTCUSDT,
    side: OrderSide = OrderSide.BUY,
    qty: str = "0.001",
    price: str = "60000",
    fee: str = "0.06",
) -> Fill:
    from datetime import datetime, timezone
    return Fill(
        order_id=str(uuid4()),
        symbol=symbol,
        side=side,
        filled_qty=Decimal(qty),
        avg_fill_price=Decimal(price),
        fee_usdt=Decimal(fee),
        slippage_usdt=Decimal("0"),
        filled_at=datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc),
        is_paper=True,
    )


# ── save() ─────────────────────────────────────────────────────────────────────

class TestSave:
    def test_creates_json_file(self, tmp_path):
        tracker = _make_tracker()
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        assert path.exists()

    def test_file_is_valid_json(self, tmp_path):
        tracker = _make_tracker()
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_saves_cash(self, tmp_path):
        tracker = _make_tracker("350")
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        data = json.loads(path.read_text())
        assert Decimal(data["cash"]) == Decimal("350")

    def test_saves_realized_pnl(self, tmp_path):
        tracker = _make_tracker()
        tracker.apply_fill(_make_fill(side=OrderSide.BUY, qty="0.001", price="60000", fee="0.06"))
        tracker.apply_fill(_make_fill(side=OrderSide.SELL, qty="0.001", price="62000", fee="0.062"))
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        data = json.loads(path.read_text())
        assert Decimal(data["realized_pnl"]) == tracker.realized_pnl

    def test_saves_positions(self, tmp_path):
        tracker = _make_tracker()
        tracker.apply_fill(_make_fill(qty="0.001", price="60000", fee="0.06"))
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        data = json.loads(path.read_text())
        assert "BTCUSDT" in data["positions"]
        assert Decimal(data["positions"]["BTCUSDT"]["qty"]) == Decimal("0.001")
        assert Decimal(data["positions"]["BTCUSDT"]["avg_entry_price"]) == Decimal("60000")

    def test_saves_mark_prices(self, tmp_path):
        tracker = _make_tracker()
        mark_prices = {Symbol.BTCUSDT: Decimal("61000")}
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path, mark_prices=mark_prices)
        data = json.loads(path.read_text())
        assert Decimal(data["mark_prices"]["BTCUSDT"]) == Decimal("61000")

    def test_no_mark_prices_saves_empty_dict(self, tmp_path):
        tracker = _make_tracker()
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        data = json.loads(path.read_text())
        assert data["mark_prices"] == {}

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        assert path.exists()

    def test_overwrites_existing_file(self, tmp_path):
        tracker = _make_tracker("500")
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)

        tracker2 = _make_tracker("300")
        PortfolioStateStore.save(tracker2, path)
        data = json.loads(path.read_text())
        assert Decimal(data["cash"]) == Decimal("300")

    def test_no_tmp_file_after_success(self, tmp_path):
        path = tmp_path / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        assert not path.with_suffix(".tmp").exists()

    def test_includes_version(self, tmp_path):
        path = tmp_path / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        data = json.loads(path.read_text())
        assert data["version"] == 1

    def test_includes_saved_at(self, tmp_path):
        path = tmp_path / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        data = json.loads(path.read_text())
        assert "saved_at" in data and data["saved_at"]


# ── load() ─────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_not_found_when_file_absent(self, tmp_path):
        result = PortfolioStateStore.load(tmp_path / "missing.json", starting_cash=Decimal("500"))
        assert result.status == "not_found"
        assert result.tracker is None

    def test_returns_ok_on_valid_file(self, tmp_path):
        path = tmp_path / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "ok"
        assert result.tracker is not None

    def test_restores_cash(self, tmp_path):
        tracker = _make_tracker("350")
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.tracker.cash == Decimal("350")

    def test_restores_realized_pnl(self, tmp_path):
        tracker = _make_tracker()
        tracker.apply_fill(_make_fill(side=OrderSide.BUY, qty="0.001", price="60000", fee="0.06"))
        tracker.apply_fill(_make_fill(side=OrderSide.SELL, qty="0.001", price="62000", fee="0.062"))
        expected_pnl = tracker.realized_pnl
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.tracker.realized_pnl == expected_pnl

    def test_restores_positions(self, tmp_path):
        tracker = _make_tracker()
        tracker.apply_fill(_make_fill(qty="0.001", price="60000", fee="0.06"))
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.tracker.position_count == 1
        snap = result.tracker.snapshot()
        pos = snap.open_positions[Symbol.BTCUSDT]
        assert pos.qty == Decimal("0.001")
        assert pos.avg_entry_price == Decimal("60000")

    def test_restores_mark_prices(self, tmp_path):
        tracker = _make_tracker()
        mark_prices = {Symbol.BTCUSDT: Decimal("61000"), Symbol.ETHUSDT: Decimal("3000")}
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path, mark_prices=mark_prices)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.mark_prices[Symbol.BTCUSDT] == Decimal("61000")
        assert result.mark_prices[Symbol.ETHUSDT] == Decimal("3000")

    def test_empty_mark_prices_returns_empty_dict(self, tmp_path):
        path = tmp_path / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.mark_prices == {}

    def test_saved_at_preserved(self, tmp_path):
        path = tmp_path / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.saved_at is not None

    def test_corrupt_json_returns_corrupt(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not valid json", encoding="utf-8")
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "corrupt"
        assert result.tracker is None

    def test_corrupt_has_error_message(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{bad json", encoding="utf-8")
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.error is not None and len(result.error) > 0

    def test_wrong_version_returns_corrupt(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps({"version": 99, "cash": "500", "realized_pnl": "0",
                        "saved_at": "2026-01-01T00:00:00+00:00",
                        "positions": {}, "mark_prices": {}}),
            encoding="utf-8",
        )
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "corrupt"
        assert "version" in result.error.lower() or "99" in result.error

    def test_corrupt_does_not_raise(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("garbage", encoding="utf-8")
        # Should return a result, never raise
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "corrupt"

    def test_missing_required_key_returns_corrupt(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps({"version": 1, "saved_at": "2026-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "corrupt"


# ── status() ───────────────────────────────────────────────────────────────────

class TestStatus:
    def test_not_found_when_absent(self, tmp_path):
        assert PortfolioStateStore.status(tmp_path / "missing.json") == "not_found"

    def test_ok_after_save(self, tmp_path):
        path = tmp_path / "state.json"
        PortfolioStateStore.save(_make_tracker(), path)
        assert PortfolioStateStore.status(path) == "ok"

    def test_corrupt_on_bad_json(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("bad json", encoding="utf-8")
        assert PortfolioStateStore.status(path) == "corrupt"

    def test_corrupt_on_wrong_version(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"version": 99, "cash": "500", "realized_pnl": "0",
                                    "saved_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
        assert PortfolioStateStore.status(path) == "corrupt"

    def test_corrupt_on_missing_required_field(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"version": 1, "saved_at": "2026-01-01T00:00:00+00:00"}),
                        encoding="utf-8")
        assert PortfolioStateStore.status(path) == "corrupt"


# ── Round-trip ─────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_round_trip_empty_portfolio(self, tmp_path):
        tracker = _make_tracker("500")
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "ok"
        assert result.tracker.cash == Decimal("500")
        assert result.tracker.realized_pnl == Decimal("0")
        assert result.tracker.position_count == 0

    def test_round_trip_with_positions(self, tmp_path):
        tracker = _make_tracker()
        tracker.apply_fill(_make_fill(qty="0.002", price="60000", fee="0.12"))
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)

        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "ok"
        restored = result.tracker
        assert restored.cash == tracker.cash
        assert restored.realized_pnl == tracker.realized_pnl
        assert restored.position_count == 1

    def test_round_trip_with_mark_prices(self, tmp_path):
        tracker = _make_tracker()
        tracker.apply_fill(_make_fill(qty="0.001", price="60000", fee="0.06"))
        marks = {Symbol.BTCUSDT: Decimal("62000")}
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path, mark_prices=marks)

        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.mark_prices[Symbol.BTCUSDT] == Decimal("62000")

    def test_restart_continuity(self, tmp_path):
        """Simulate: bot runs, takes a fill, saves, restarts, restores, takes another fill."""
        path = tmp_path / "portfolio_state.json"

        # First run
        tracker1 = _make_tracker("500")
        tracker1.apply_fill(_make_fill(qty="0.001", price="60000", fee="0.06"))
        cash_after_buy = tracker1.cash
        PortfolioStateStore.save(tracker1, path)

        # Restart — restore from file
        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "ok"
        tracker2 = result.tracker

        # Verify continuity
        assert tracker2.cash == cash_after_buy
        assert tracker2.position_count == 1

        # Sell the position
        tracker2.apply_fill(_make_fill(side=OrderSide.SELL, qty="0.001", price="62000", fee="0.062"))
        assert tracker2.position_count == 0
        assert tracker2.realized_pnl > Decimal("0")

    def test_multiple_positions_round_trip(self, tmp_path):
        tracker = _make_tracker()
        tracker.apply_fill(_make_fill(symbol=Symbol.BTCUSDT, qty="0.001", price="60000", fee="0.06"))
        tracker.apply_fill(_make_fill(symbol=Symbol.ETHUSDT, qty="0.01", price="3000", fee="0.03"))
        path = tmp_path / "state.json"
        PortfolioStateStore.save(tracker, path)

        result = PortfolioStateStore.load(path, starting_cash=Decimal("500"))
        assert result.status == "ok"
        assert result.tracker.position_count == 2
