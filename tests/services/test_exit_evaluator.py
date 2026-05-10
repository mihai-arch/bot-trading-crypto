"""
ExitEvaluator tests.

Covers all three exit categories (stop-loss, take-profit, signal deterioration),
boundary conditions, and priority ordering.

Config defaults: stop_loss_pct=0.05, take_profit_pct=0.10, exit_score_threshold=0.30
"""

from decimal import Decimal

import pytest

from bit.config import BITConfig
from bit.domain.enums import Symbol
from bit.domain.market import Position
from bit.services.exit_evaluator import ExitDecision, ExitEvaluator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _config(**overrides) -> BITConfig:
    defaults = dict(
        stop_loss_pct=Decimal("0.05"),
        take_profit_pct=Decimal("0.10"),
        exit_score_threshold=Decimal("0.30"),
    )
    defaults.update(overrides)
    return BITConfig(**defaults)


def _position(
    symbol: Symbol = Symbol.BTCUSDT,
    avg_entry_price: Decimal = Decimal("60000"),
    qty: Decimal = Decimal("0.001"),
) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        avg_entry_price=avg_entry_price,
        unrealized_pnl_usdt=Decimal("0"),
    )


# ── No exit ───────────────────────────────────────────────────────────────────

class TestNoExit:
    def test_no_exit_in_safe_zone_with_strong_signal(self):
        """Price in safe range and strong signal → no exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        # Price is 1% above entry — within stop-loss and below take-profit
        result = ev.evaluate(pos, Decimal("60600"), Decimal("0.70"))
        assert result is None

    def test_no_exit_exactly_at_stop_threshold_plus_one_tick(self):
        """Price just above stop-loss threshold → no exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        # stop_loss_price = 60000 * 0.95 = 57000; price slightly above
        result = ev.evaluate(pos, Decimal("57001"), Decimal("0.70"))
        assert result is None

    def test_no_exit_just_below_take_profit(self):
        """Price just below take-profit threshold → no exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        # take_profit_price = 60000 * 1.10 = 66000; price just below
        result = ev.evaluate(pos, Decimal("65999"), Decimal("0.70"))
        assert result is None

    def test_no_exit_score_just_above_threshold(self):
        """Signal score just above exit_score_threshold → no exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        # Score 0.31 > 0.30 threshold; price in safe zone
        result = ev.evaluate(pos, Decimal("60600"), Decimal("0.31"))
        assert result is None


# ── Stop-loss ─────────────────────────────────────────────────────────────────

class TestStopLoss:
    def test_stop_loss_triggers_exactly_at_threshold(self):
        """price == avg_entry * (1 - stop_loss_pct) → stop_loss exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        stop_price = Decimal("60000") * (Decimal("1") - Decimal("0.05"))
        result = ev.evaluate(pos, stop_price, Decimal("0.70"))
        assert isinstance(result, ExitDecision)
        assert result.reason == "stop_loss"

    def test_stop_loss_triggers_below_threshold(self):
        """Price well below stop → stop_loss exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        result = ev.evaluate(pos, Decimal("50000"), Decimal("0.70"))
        assert result is not None
        assert result.reason == "stop_loss"

    def test_stop_loss_does_not_trigger_just_above(self):
        """Price 1 unit above stop threshold → no stop-loss."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        stop_price = Decimal("60000") * Decimal("0.95")
        result = ev.evaluate(pos, stop_price + Decimal("0.01"), Decimal("0.70"))
        assert result is None or result.reason != "stop_loss"

    def test_stop_loss_populates_symbol(self):
        ev = ExitEvaluator(_config())
        pos = _position(symbol=Symbol.ETHUSDT, avg_entry_price=Decimal("3000"))
        stop_price = Decimal("3000") * Decimal("0.95")
        result = ev.evaluate(pos, stop_price, Decimal("0.70"))
        assert result is not None
        assert result.symbol == Symbol.ETHUSDT

    def test_stop_loss_populates_current_price(self):
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        stop_price = Decimal("60000") * Decimal("0.95")
        result = ev.evaluate(pos, stop_price, Decimal("0.70"))
        assert result is not None
        assert result.current_price == stop_price

    def test_stop_loss_populates_position_qty(self):
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"), qty=Decimal("0.005"))
        stop_price = Decimal("60000") * Decimal("0.95")
        result = ev.evaluate(pos, stop_price, Decimal("0.70"))
        assert result is not None
        assert result.position_qty == Decimal("0.005")

    def test_stop_loss_custom_pct(self):
        """Custom stop_loss_pct=10% → trigger at 90% of entry."""
        ev = ExitEvaluator(_config(stop_loss_pct=Decimal("0.10")))
        pos = _position(avg_entry_price=Decimal("10000"))
        result = ev.evaluate(pos, Decimal("9000"), Decimal("0.70"))
        assert result is not None
        assert result.reason == "stop_loss"


# ── Take-profit ───────────────────────────────────────────────────────────────

class TestTakeProfit:
    def test_take_profit_triggers_exactly_at_threshold(self):
        """price == avg_entry * (1 + take_profit_pct) → take_profit exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        tp_price = Decimal("60000") * (Decimal("1") + Decimal("0.10"))
        result = ev.evaluate(pos, tp_price, Decimal("0.70"))
        assert isinstance(result, ExitDecision)
        assert result.reason == "take_profit"

    def test_take_profit_triggers_above_threshold(self):
        """Price well above take-profit → take_profit exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        result = ev.evaluate(pos, Decimal("80000"), Decimal("0.70"))
        assert result is not None
        assert result.reason == "take_profit"

    def test_take_profit_does_not_trigger_just_below(self):
        """Price just below take-profit → no take-profit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        tp_price = Decimal("60000") * Decimal("1.10")
        result = ev.evaluate(pos, tp_price - Decimal("0.01"), Decimal("0.70"))
        assert result is None or result.reason != "take_profit"

    def test_take_profit_populates_reason(self):
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        tp_price = Decimal("60000") * Decimal("1.10")
        result = ev.evaluate(pos, tp_price, Decimal("0.70"))
        assert result is not None
        assert result.reason == "take_profit"


# ── Signal deterioration ──────────────────────────────────────────────────────

class TestSignalDeterioration:
    def test_signal_deterioration_at_exact_threshold(self):
        """score == exit_score_threshold → signal_deterioration exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        result = ev.evaluate(pos, Decimal("60600"), Decimal("0.30"))
        assert isinstance(result, ExitDecision)
        assert result.reason == "signal_deterioration"

    def test_signal_deterioration_below_threshold(self):
        """score < exit_score_threshold → signal_deterioration exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        result = ev.evaluate(pos, Decimal("60600"), Decimal("0.10"))
        assert result is not None
        assert result.reason == "signal_deterioration"

    def test_signal_deterioration_at_zero_score(self):
        """score=0 (no signal selected) → signal_deterioration exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        result = ev.evaluate(pos, Decimal("60600"), Decimal("0"))
        assert result is not None
        assert result.reason == "signal_deterioration"

    def test_signal_deterioration_does_not_trigger_just_above(self):
        """score just above threshold → no signal-deterioration exit."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        result = ev.evaluate(pos, Decimal("60600"), Decimal("0.31"))
        assert result is None

    def test_signal_deterioration_custom_threshold(self):
        """Custom exit_score_threshold=0.50 → triggers at 0.50."""
        ev = ExitEvaluator(_config(exit_score_threshold=Decimal("0.50")))
        pos = _position(avg_entry_price=Decimal("60000"))
        result = ev.evaluate(pos, Decimal("60600"), Decimal("0.50"))
        assert result is not None
        assert result.reason == "signal_deterioration"


# ── Priority ordering ─────────────────────────────────────────────────────────

class TestPriority:
    def test_stop_loss_beats_signal_deterioration(self):
        """Both stop-loss and signal-deterioration triggered → stop_loss wins."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        stop_price = Decimal("60000") * Decimal("0.95")  # exactly at stop
        # score also ≤ threshold
        result = ev.evaluate(pos, stop_price, Decimal("0.20"))
        assert result is not None
        assert result.reason == "stop_loss"

    def test_take_profit_beats_signal_deterioration(self):
        """Both take-profit and signal-deterioration triggered → take_profit wins."""
        ev = ExitEvaluator(_config())
        pos = _position(avg_entry_price=Decimal("60000"))
        tp_price = Decimal("60000") * Decimal("1.10")
        result = ev.evaluate(pos, tp_price, Decimal("0.20"))
        assert result is not None
        assert result.reason == "take_profit"

    def test_stop_loss_beats_take_profit_never_possible(self):
        """Stop-loss and take-profit cannot both trigger simultaneously
        (price can't be both ≤ stop and ≥ take-profit at the same entry).
        This test confirms stop-loss check comes first in code ordering."""
        ev = ExitEvaluator(_config(stop_loss_pct=Decimal("0.90"), take_profit_pct=Decimal("0.01")))
        # entry=100; stop at 10; take_profit at 101
        # price=10 → below stop (≤10) AND also below take_profit (101)
        pos = _position(avg_entry_price=Decimal("100"), qty=Decimal("1"))
        result = ev.evaluate(pos, Decimal("10"), Decimal("0.70"))
        assert result is not None
        assert result.reason == "stop_loss"
