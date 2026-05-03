"""
PaperPortfolioTracker unit tests.

All expected values are computed from first principles in comments so that
any reader can verify the arithmetic independently.

Test organisation:
  TestInitialState          — tracker starts with correct cash / no positions
  TestBuyFill               — single buy decreases cash, opens position
  TestSecondBuyAvgEntry     — second buy computes weighted average entry
  TestPartialSell           — partial sell reduces qty and records realized PnL
  TestFullExit              — full sell removes position cleanly
  TestSellErrors            — sell without position, oversell
  TestInsufficientCash      — buy beyond available cash raises
  TestMarkPrices            — unrealized PnL and equity respond to mark prices
  TestSnapshot              — snapshot fields are correct and complete
  TestMultipleSymbols       — independent position tracking per symbol
  TestRealizedPnL           — profitable and losing sell both computed correctly
  TestProperties            — cash, realized_pnl, position_count properties
  TestConstructor           — rejects non-positive starting cash
  TestApplyFillDispatch     — apply_fill dispatches on fill.side correctly
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.enums import OrderSide, Symbol
from bit.domain.execution import Fill
from bit.domain.market import PortfolioState
from bit.services.paper_portfolio import PaperPortfolioTracker


# ── Test constants ────────────────────────────────────────────────────────────

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_START = Decimal("500")  # Starting cash


# ── Fill helpers ──────────────────────────────────────────────────────────────

def _buy(
    symbol: Symbol = Symbol.BTCUSDT,
    qty: str = "0.001",
    price: str = "60000",
    fee: str = "0.06",
) -> Fill:
    return Fill(
        order_id="buy-test",
        symbol=symbol,
        side=OrderSide.BUY,
        filled_qty=Decimal(qty),
        avg_fill_price=Decimal(price),
        fee_usdt=Decimal(fee),
        slippage_usdt=Decimal("0"),
        filled_at=_NOW,
        is_paper=True,
    )


def _sell(
    symbol: Symbol = Symbol.BTCUSDT,
    qty: str = "0.001",
    price: str = "65000",
    fee: str = "0.065",
) -> Fill:
    return Fill(
        order_id="sell-test",
        symbol=symbol,
        side=OrderSide.SELL,
        filled_qty=Decimal(qty),
        avg_fill_price=Decimal(price),
        fee_usdt=Decimal(fee),
        slippage_usdt=Decimal("0"),
        filled_at=_NOW,
        is_paper=True,
    )


def _tracker() -> PaperPortfolioTracker:
    return PaperPortfolioTracker(starting_cash=_START)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestInitialState:
    def test_cash_equals_starting_amount(self):
        t = _tracker()
        assert t.cash == _START

    def test_no_open_positions(self):
        t = _tracker()
        assert t.position_count == 0

    def test_realized_pnl_is_zero(self):
        t = _tracker()
        assert t.realized_pnl == Decimal("0")

    def test_snapshot_equity_equals_cash(self):
        t = _tracker()
        s = t.snapshot()
        assert s.total_equity_usdt == _START

    def test_snapshot_available_equals_cash(self):
        t = _tracker()
        s = t.snapshot()
        assert s.available_usdt == _START

    def test_snapshot_no_positions(self):
        t = _tracker()
        s = t.snapshot()
        assert s.open_positions == {}


class TestBuyFill:
    """
    Buy 0.001 BTC @ $60,000, fee $0.06
      cash_out = 0.001 × 60000 + 0.06 = 60.06
      remaining_cash = 500 − 60.06 = 439.94
      position = 0.001 BTC @ avg_entry $60,000
    """

    def setup_method(self):
        self.t = _tracker()
        self.t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))

    def test_cash_reduced_by_notional_plus_fee(self):
        expected = _START - Decimal("60.06")  # 500 - (60 + 0.06)
        assert self.t.cash == expected

    def test_position_opened(self):
        assert self.t.position_count == 1

    def test_position_qty_correct(self):
        s = self.t.snapshot()
        pos = s.open_positions[Symbol.BTCUSDT]
        assert pos.qty == Decimal("0.001")

    def test_avg_entry_price_correct(self):
        s = self.t.snapshot()
        pos = s.open_positions[Symbol.BTCUSDT]
        assert pos.avg_entry_price == Decimal("60000")

    def test_available_usdt_reflects_cash(self):
        s = self.t.snapshot()
        assert s.available_usdt == self.t.cash

    def test_equity_at_cost_equals_cash_plus_position_value(self):
        # Mark = avg_entry (no mark_prices provided) → unrealized PnL = 0
        s = self.t.snapshot()
        expected_equity = self.t.cash + Decimal("0.001") * Decimal("60000")
        assert s.total_equity_usdt == expected_equity

    def test_realized_pnl_still_zero(self):
        assert self.t.realized_pnl == Decimal("0")


class TestSecondBuyAvgEntry:
    """
    Buy 1: 0.001 BTC @ $60,000
    Buy 2: 0.001 BTC @ $62,000, fee $0.062

    weighted avg = (0.001×60000 + 0.001×62000) / 0.002
                 = (60 + 62) / 0.002 = 122 / 0.002 = 61,000

    cash after both = 500 − 60.06 − 62.062 = 377.878
    """

    def setup_method(self):
        self.t = _tracker()
        self.t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        self.t.apply_fill(_buy(qty="0.001", price="62000", fee="0.062"))

    def test_cumulative_qty(self):
        s = self.t.snapshot()
        assert s.open_positions[Symbol.BTCUSDT].qty == Decimal("0.002")

    def test_weighted_average_entry_price(self):
        s = self.t.snapshot()
        pos = s.open_positions[Symbol.BTCUSDT]
        assert pos.avg_entry_price == Decimal("61000")

    def test_cash_after_both_buys(self):
        # 500 - 60.06 - 62.062 = 377.878
        expected = _START - Decimal("60.06") - Decimal("62.062")
        assert self.t.cash == expected

    def test_still_one_position(self):
        assert self.t.position_count == 1


class TestPartialSell:
    """
    After buying 0.001 BTC @ $60,000 (fee $0.06):
      avg_entry = $60,000, cash = $439.94

    Sell 0.0005 BTC @ $65,000, fee $0.0325:
      gross_pnl  = (65000 − 60000) × 0.0005 = 5000 × 0.0005 = $2.50
      net_pnl    = 2.50 − 0.0325 = $2.4675
      cash_in    = 0.0005 × 65000 − 0.0325 = 32.5 − 0.0325 = $32.4675
      new_cash   = 439.94 + 32.4675 = $472.4075
      remaining  = 0.0005 BTC @ $60,000 (avg unchanged)
    """

    def setup_method(self):
        self.t = _tracker()
        self.t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        self.t.apply_fill(
            _sell(qty="0.0005", price="65000", fee="0.0325")
        )

    def test_position_qty_reduced(self):
        s = self.t.snapshot()
        assert s.open_positions[Symbol.BTCUSDT].qty == Decimal("0.0005")

    def test_avg_entry_price_unchanged(self):
        s = self.t.snapshot()
        assert s.open_positions[Symbol.BTCUSDT].avg_entry_price == Decimal("60000")

    def test_cash_after_partial_sell(self):
        expected = Decimal("439.94") + Decimal("32.4675")
        assert self.t.cash == expected

    def test_realized_pnl_recorded(self):
        assert self.t.realized_pnl == Decimal("2.4675")

    def test_position_still_open(self):
        assert self.t.position_count == 1


class TestFullExit:
    """
    After buying 0.001 BTC @ $60,000 (fee $0.06):
      cash = $439.94

    Sell all 0.001 BTC @ $65,000, fee $0.065:
      gross_pnl = (65000 − 60000) × 0.001 = $5.00
      net_pnl   = 5.00 − 0.065 = $4.935
      cash_in   = 0.001 × 65000 − 0.065 = 65 − 0.065 = $64.935
      new_cash  = 439.94 + 64.935 = $504.875
    """

    def setup_method(self):
        self.t = _tracker()
        self.t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        self.t.apply_fill(_sell(qty="0.001", price="65000", fee="0.065"))

    def test_position_removed(self):
        assert self.t.position_count == 0

    def test_no_position_in_snapshot(self):
        s = self.t.snapshot()
        assert Symbol.BTCUSDT not in s.open_positions

    def test_cash_after_full_exit(self):
        expected = Decimal("439.94") + Decimal("64.935")  # = 504.875
        assert self.t.cash == expected

    def test_realized_pnl_after_full_exit(self):
        assert self.t.realized_pnl == Decimal("4.935")

    def test_equity_equals_cash_when_no_positions(self):
        s = self.t.snapshot()
        assert s.total_equity_usdt == self.t.cash


class TestSellErrors:
    def test_sell_without_any_position_raises(self):
        t = _tracker()
        with pytest.raises(ValueError, match="no open position"):
            t.apply_fill(_sell())

    def test_sell_more_than_held_raises(self):
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        with pytest.raises(ValueError, match="no short selling"):
            t.apply_fill(_sell(qty="0.002"))

    def test_sell_after_full_exit_raises(self):
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        t.apply_fill(_sell(qty="0.001", price="65000", fee="0.065"))
        with pytest.raises(ValueError, match="no open position"):
            t.apply_fill(_sell(qty="0.001"))

    def test_sell_wrong_symbol_raises(self):
        t = _tracker()
        t.apply_fill(_buy(symbol=Symbol.BTCUSDT))
        with pytest.raises(ValueError, match="no open position"):
            t.apply_fill(_sell(symbol=Symbol.ETHUSDT))


class TestInsufficientCash:
    def test_buy_exceeding_cash_raises(self):
        t = _tracker()  # $500 cash
        # Try to buy 0.1 BTC @ $60000 = $6000 notional — way over $500
        with pytest.raises(ValueError, match="Insufficient cash"):
            t.apply_fill(_buy(qty="0.1", price="60000", fee="6"))

    def test_error_message_shows_amounts(self):
        t = _tracker()
        with pytest.raises(ValueError, match="have"):
            t.apply_fill(_buy(qty="0.1", price="60000", fee="6"))

    def test_cash_unchanged_after_failed_buy(self):
        t = _tracker()
        try:
            t.apply_fill(_buy(qty="0.1", price="60000", fee="6"))
        except ValueError:
            pass
        assert t.cash == _START  # unchanged


class TestMarkPrices:
    """Mark prices drive unrealized PnL and total equity."""

    def setup_method(self):
        self.t = _tracker()
        # Buy 0.001 BTC @ $60,000 (fee $0.06); cash = $439.94
        self.t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))

    def test_mark_above_entry_positive_unrealized_pnl(self):
        s = self.t.snapshot({Symbol.BTCUSDT: Decimal("62000")})
        pos = s.open_positions[Symbol.BTCUSDT]
        # (62000 - 60000) * 0.001 = 2000 * 0.001 = $2
        assert pos.unrealized_pnl_usdt == Decimal("2")

    def test_mark_below_entry_negative_unrealized_pnl(self):
        s = self.t.snapshot({Symbol.BTCUSDT: Decimal("58000")})
        pos = s.open_positions[Symbol.BTCUSDT]
        # (58000 - 60000) * 0.001 = -2000 * 0.001 = -$2
        assert pos.unrealized_pnl_usdt == Decimal("-2")

    def test_mark_at_entry_zero_unrealized_pnl(self):
        s = self.t.snapshot({Symbol.BTCUSDT: Decimal("60000")})
        assert s.open_positions[Symbol.BTCUSDT].unrealized_pnl_usdt == Decimal("0")

    def test_equity_reflects_mark_price(self):
        # cash = 439.94; position 0.001 BTC @ mark $62000 = $62 market value
        s = self.t.snapshot({Symbol.BTCUSDT: Decimal("62000")})
        expected = Decimal("439.94") + Decimal("0.001") * Decimal("62000")
        assert s.total_equity_usdt == expected

    def test_no_mark_prices_uses_avg_entry(self):
        # Without mark prices, unrealized PnL is always 0.
        s = self.t.snapshot()
        assert s.open_positions[Symbol.BTCUSDT].unrealized_pnl_usdt == Decimal("0")

    def test_no_mark_prices_equity_is_cash_plus_cost_basis(self):
        s = self.t.snapshot()
        # equity = cash + 0.001 * 60000 (cost basis)
        expected = self.t.cash + Decimal("0.001") * Decimal("60000")
        assert s.total_equity_usdt == expected


class TestSnapshot:
    def test_snapshot_is_portfolio_state(self):
        t = _tracker()
        assert isinstance(t.snapshot(), PortfolioState)

    def test_snapshot_realized_pnl_in_result(self):
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        t.apply_fill(_sell(qty="0.001", price="65000", fee="0.065"))
        s = t.snapshot()
        assert s.realized_pnl_usdt == Decimal("4.935")

    def test_snapshot_zero_realized_pnl_initially(self):
        t = _tracker()
        assert t.snapshot().realized_pnl_usdt == Decimal("0")

    def test_snapshot_open_positions_count(self):
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        s = t.snapshot()
        assert len(s.open_positions) == 1

    def test_snapshot_does_not_mutate_tracker(self):
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        cash_before = t.cash
        t.snapshot({Symbol.BTCUSDT: Decimal("99000")})
        assert t.cash == cash_before


class TestMultipleSymbols:
    """Positions in different symbols are tracked independently."""

    def setup_method(self):
        self.t = _tracker()
        self.t.apply_fill(_buy(symbol=Symbol.BTCUSDT, qty="0.001",
                                price="60000", fee="0.06"))
        self.t.apply_fill(_buy(symbol=Symbol.ETHUSDT, qty="0.02",
                                price="3000", fee="0.06"))

    def test_both_positions_open(self):
        assert self.t.position_count == 2

    def test_btc_position_correct(self):
        s = self.t.snapshot()
        pos = s.open_positions[Symbol.BTCUSDT]
        assert pos.qty == Decimal("0.001")
        assert pos.avg_entry_price == Decimal("60000")

    def test_eth_position_correct(self):
        s = self.t.snapshot()
        pos = s.open_positions[Symbol.ETHUSDT]
        assert pos.qty == Decimal("0.02")
        assert pos.avg_entry_price == Decimal("3000")

    def test_closing_one_leaves_other_intact(self):
        self.t.apply_fill(_sell(symbol=Symbol.BTCUSDT, qty="0.001",
                                 price="61000", fee="0.061"))
        assert Symbol.ETHUSDT in self.t.snapshot().open_positions
        assert Symbol.BTCUSDT not in self.t.snapshot().open_positions

    def test_mark_prices_applied_per_symbol(self):
        s = self.t.snapshot({
            Symbol.BTCUSDT: Decimal("61000"),
            Symbol.ETHUSDT: Decimal("3100"),
        })
        # BTC unrealized = (61000 - 60000) * 0.001 = 1.0
        assert s.open_positions[Symbol.BTCUSDT].unrealized_pnl_usdt == Decimal("1")
        # ETH unrealized = (3100 - 3000) * 0.02 = 2.0
        assert s.open_positions[Symbol.ETHUSDT].unrealized_pnl_usdt == Decimal("2")


class TestRealizedPnL:
    def test_profitable_sell(self):
        """Sell above avg_entry → positive PnL."""
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        t.apply_fill(_sell(qty="0.001", price="65000", fee="0.065"))
        # gross = (65000 - 60000) * 0.001 = 5.0; net = 5.0 - 0.065 = 4.935
        assert t.realized_pnl == Decimal("4.935")

    def test_losing_sell(self):
        """Sell below avg_entry → negative PnL."""
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        t.apply_fill(_sell(qty="0.001", price="55000", fee="0.055"))
        # gross = (55000 - 60000) * 0.001 = -5.0; net = -5.0 - 0.055 = -5.055
        assert t.realized_pnl == Decimal("-5.055")

    def test_break_even_sell(self):
        """Sell exactly at avg_entry → PnL equals negative of sell fee."""
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0"))
        t.apply_fill(_sell(qty="0.001", price="60000", fee="0.06"))
        # gross = 0; net = 0 - 0.06 = -0.06
        assert t.realized_pnl == Decimal("-0.06")

    def test_cumulative_realized_pnl_across_multiple_trades(self):
        """PnL accumulates across multiple sells."""
        t = _tracker()
        # Trade 1: buy 0.001 @ 60000, sell 0.001 @ 65000 (fee 0 for simplicity)
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0"))
        t.apply_fill(_sell(qty="0.001", price="65000", fee="0"))
        # Trade 2: buy 0.001 @ 62000, sell 0.001 @ 64000 (fee 0)
        t.apply_fill(_buy(qty="0.001", price="62000", fee="0"))
        t.apply_fill(_sell(qty="0.001", price="64000", fee="0"))
        # Total PnL = 5 + 2 = 7.0
        assert t.realized_pnl == Decimal("7")


class TestProperties:
    def test_cash_property(self):
        t = _tracker()
        assert t.cash == _START

    def test_realized_pnl_property_initial(self):
        t = _tracker()
        assert t.realized_pnl == Decimal("0")

    def test_position_count_property_after_buy(self):
        t = _tracker()
        t.apply_fill(_buy())
        assert t.position_count == 1

    def test_position_count_property_after_close(self):
        t = _tracker()
        t.apply_fill(_buy())
        t.apply_fill(_sell())
        assert t.position_count == 0


class TestConstructor:
    def test_rejects_zero_starting_cash(self):
        with pytest.raises(ValueError):
            PaperPortfolioTracker(starting_cash=Decimal("0"))

    def test_rejects_negative_starting_cash(self):
        with pytest.raises(ValueError):
            PaperPortfolioTracker(starting_cash=Decimal("-100"))

    def test_accepts_positive_starting_cash(self):
        t = PaperPortfolioTracker(starting_cash=Decimal("1000"))
        assert t.cash == Decimal("1000")


class TestApplyFillDispatch:
    def test_buy_fill_dispatched_to_buy_path(self):
        t = _tracker()
        t.apply_fill(_buy())
        assert t.position_count == 1

    def test_sell_fill_dispatched_to_sell_path(self):
        t = _tracker()
        t.apply_fill(_buy())
        t.apply_fill(_sell())
        assert t.position_count == 0


class TestMarkPriceStorage:
    """update_mark_price / get_last_mark_price / snapshot() merge behaviour."""

    def test_get_last_mark_price_returns_none_when_not_set(self):
        t = _tracker()
        assert t.get_last_mark_price(Symbol.BTCUSDT) is None

    def test_update_mark_price_stores_price(self):
        t = _tracker()
        t.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))
        assert t.get_last_mark_price(Symbol.BTCUSDT) == Decimal("62000")

    def test_snapshot_uses_stored_mark_price_for_unrealized_pnl(self):
        """After update_mark_price, snapshot() computes unrealized PnL without
        any passed-in mark_prices argument."""
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        t.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))
        s = t.snapshot()
        # (62000 - 60000) * 0.001 = $2
        assert s.open_positions[Symbol.BTCUSDT].unrealized_pnl_usdt == Decimal("2")

    def test_snapshot_passed_arg_overrides_stored(self):
        """Explicitly passed mark_prices take precedence over stored prices."""
        t = _tracker()
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        t.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))  # stored
        s = t.snapshot({Symbol.BTCUSDT: Decimal("61000")})     # override
        # (61000 - 60000) * 0.001 = $1 — override wins
        assert s.open_positions[Symbol.BTCUSDT].unrealized_pnl_usdt == Decimal("1")

    def test_stored_price_does_not_affect_other_symbols(self):
        """Mark price for BTC must not change ETH position behaviour."""
        t = _tracker()
        t.apply_fill(_buy(symbol=Symbol.ETHUSDT, qty="0.01", price="3000", fee="0.03"))
        t.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))
        s = t.snapshot()
        # ETH has no stored price — falls back to avg_entry, unrealized = 0
        assert s.open_positions[Symbol.ETHUSDT].unrealized_pnl_usdt == Decimal("0")
        assert t.get_last_mark_price(Symbol.ETHUSDT) is None

    def test_equity_reflects_stored_mark_price(self):
        """total_equity_usdt uses current mark price when available."""
        t = _tracker()
        # cash after buy: 500 - (60 + 0.06) = 439.94
        t.apply_fill(_buy(qty="0.001", price="60000", fee="0.06"))
        t.update_mark_price(Symbol.BTCUSDT, Decimal("62000"))
        s = t.snapshot()
        # equity = cash + 0.001 * 62000 = 439.94 + 62 = 501.94
        expected = Decimal("439.94") + Decimal("0.001") * Decimal("62000")
        assert s.total_equity_usdt == expected
