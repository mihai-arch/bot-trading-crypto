"""
PaperPortfolioTracker

In-memory portfolio state for paper trading.

Tracks:
  - Available cash (USDT)
  - Open positions per symbol — quantity and weighted average entry price
  - Cumulative realized PnL from closed or reduced positions

This is the single source of truth for account state during paper trading.
It is intentionally in-memory only (v1); for persistence wrap with a
serialization layer or migrate to SQLite in a later phase.

Fee treatment:
  BUY  — cash out  = qty × fill_price + fee_usdt
         avg_entry_price = weighted average of fill prices (excluding fees),
         which is the standard cost-basis convention.
  SELL — cash in   = qty × fill_price − fee_usdt
         realized_pnl per trade = (fill_price − avg_entry) × qty − fee_usdt

Not thread-safe. Designed for single-threaded pipeline use.

v1 constraints:
  - Long-only: SELL never exceeds held quantity; no short positions.
  - Base currency USDT only: single unified cash balance.
  - No funding, interest, or borrow fees.
  - Fees are already reflected in the Fill provided by ExecutionEngine.
"""

from dataclasses import dataclass
from decimal import Decimal

from ..domain.enums import OrderSide, Symbol
from ..domain.execution import Fill
from ..domain.market import Position, PortfolioState

_ZERO = Decimal("0")


@dataclass
class _PaperPosition:
    """Mutable internal state for a single open position."""

    qty: Decimal
    avg_entry_price: Decimal  # Weighted average of fill prices, fees excluded.


class PaperPortfolioTracker:
    """
    Paper trading portfolio tracker.

    Usage:
        tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
        tracker.apply_fill(buy_fill)
        state = tracker.snapshot(mark_prices={Symbol.BTCUSDT: Decimal("61000")})
    """

    def __init__(self, starting_cash: Decimal) -> None:
        if starting_cash <= _ZERO:
            raise ValueError(
                f"Starting cash must be positive, got {starting_cash}."
            )
        self._cash: Decimal = starting_cash
        self._positions: dict[Symbol, _PaperPosition] = {}
        self._realized_pnl: Decimal = _ZERO

    # ── Public interface ──────────────────────────────────────────────────────

    def apply_fill(self, fill: Fill) -> None:
        """
        Apply an executed fill to the portfolio state.

        BUY  — decreases cash (notional + fee), creates or adds to position.
        SELL — decreases position, increases cash (notional − fee), records PnL.

        Raises:
            ValueError: If a BUY fill would require more cash than available.
            ValueError: If a SELL fill references a symbol with no open position.
            ValueError: If a SELL fill quantity exceeds the held position quantity.
        """
        if fill.side == OrderSide.BUY:
            self._apply_buy(fill)
        else:
            self._apply_sell(fill)

    def snapshot(
        self,
        mark_prices: dict[Symbol, Decimal] | None = None,
    ) -> PortfolioState:
        """
        Return the current portfolio state as a PortfolioState snapshot.

        Args:
            mark_prices: Optional current market prices per symbol.
                         Used to compute unrealized PnL and total equity.
                         Defaults to avg_entry_price for each position when
                         not provided — this gives unrealized_pnl_usdt = 0
                         and equity = cash + position cost basis.

        Returns:
            PortfolioState with total_equity_usdt, available_usdt,
            open_positions, and realized_pnl_usdt.
        """
        _marks = mark_prices or {}
        open_positions: dict[Symbol, Position] = {}
        total_position_value = _ZERO

        for symbol, pos in self._positions.items():
            mark = _marks.get(symbol, pos.avg_entry_price)
            unrealized_pnl = (mark - pos.avg_entry_price) * pos.qty
            total_position_value += pos.qty * mark

            open_positions[symbol] = Position(
                symbol=symbol,
                qty=pos.qty,
                avg_entry_price=pos.avg_entry_price,
                unrealized_pnl_usdt=unrealized_pnl,
            )

        return PortfolioState(
            total_equity_usdt=self._cash + total_position_value,
            available_usdt=self._cash,
            open_positions=open_positions,
            realized_pnl_usdt=self._realized_pnl,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def cash(self) -> Decimal:
        """Current available cash in USDT."""
        return self._cash

    @property
    def realized_pnl(self) -> Decimal:
        """Cumulative net realized PnL across all closed/reduced positions."""
        return self._realized_pnl

    @property
    def position_count(self) -> int:
        """Number of currently open positions."""
        return len(self._positions)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _apply_buy(self, fill: Fill) -> None:
        """Decrease cash and increase (or open) the position."""
        total_cash_out = fill.filled_qty * fill.avg_fill_price + fill.fee_usdt
        if total_cash_out > self._cash:
            raise ValueError(
                f"Insufficient cash for BUY fill on {fill.symbol}: "
                f"need {total_cash_out:.6f} USDT, "
                f"have {self._cash:.6f} USDT."
            )

        self._cash -= total_cash_out

        if fill.symbol not in self._positions:
            self._positions[fill.symbol] = _PaperPosition(
                qty=_ZERO,
                avg_entry_price=_ZERO,
            )

        pos = self._positions[fill.symbol]
        old_cost_basis = pos.qty * pos.avg_entry_price
        new_qty = pos.qty + fill.filled_qty
        new_cost_basis = old_cost_basis + fill.filled_qty * fill.avg_fill_price
        pos.qty = new_qty
        pos.avg_entry_price = new_cost_basis / new_qty

    def _apply_sell(self, fill: Fill) -> None:
        """Reduce or close position, increase cash, record realized PnL."""
        pos = self._positions.get(fill.symbol)
        if pos is None or pos.qty == _ZERO:
            raise ValueError(
                f"Cannot sell {fill.symbol}: no open position."
            )

        if fill.filled_qty > pos.qty:
            raise ValueError(
                f"Cannot sell {fill.filled_qty} {fill.symbol}: "
                f"only {pos.qty} held (no short selling in v1)."
            )

        # Net realized PnL for this trade: gross price gain minus sell-side fee.
        gross_pnl = (fill.avg_fill_price - pos.avg_entry_price) * fill.filled_qty
        net_pnl = gross_pnl - fill.fee_usdt
        self._realized_pnl += net_pnl

        # Net cash received: proceeds minus sell-side fee.
        self._cash += fill.filled_qty * fill.avg_fill_price - fill.fee_usdt

        # Reduce or close the position.
        pos.qty -= fill.filled_qty
        if pos.qty == _ZERO:
            del self._positions[fill.symbol]
        # avg_entry_price is unchanged for the remaining quantity.
