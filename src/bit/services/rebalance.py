"""
Trade a paper book toward target weights.

Mirrors the research fill model: sells first, then buys; fee and adverse
slippage on each side; buy fee is included in the cost basis by
PaperPortfolioTracker. A target of 0 liquidates. Increases are skipped when
``allow_buys`` is false so a daily-loss or loss-streak halt can still reduce risk.

Weights are fractions of this book's own equity. The rotation book and each
conservative sub-account are separate books, so they do not rebalance into
each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from ..domain.enums import OrderSide, Symbol
from ..domain.execution import Fill
from ..domain.market import InstrumentFilter
from .paper_portfolio import PaperPortfolioTracker

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TINY = Decimal("0.00000001")


@dataclass(frozen=True)
class TradeLeg:
    fill: Fill
    closed_symbol: bool
    realized_delta: Decimal


@dataclass(frozen=True)
class RebalanceResult:
    legs: tuple[TradeLeg, ...]
    held: bool
    """True when the book was already inside the band and no order was sent."""
    blocked_buys: tuple[Symbol, ...]
    """Symbols whose target was an increase and buys were not allowed."""


def _snap(qty: Decimal, step: Decimal) -> Decimal:
    if step <= _ZERO:
        return qty
    steps = (qty / step).to_integral_value(rounding=ROUND_DOWN)
    return steps * step


def _positions(tracker: PaperPortfolioTracker) -> dict[Symbol, tuple[Decimal, Decimal]]:
    """qty, avg entry. Reads the tracker's internal book; the store does the same."""
    return {
        symbol: (pos.qty, pos.avg_entry_price)
        for symbol, pos in tracker._positions.items()
    }


def rebalance_to_targets(
    tracker: PaperPortfolioTracker,
    targets: dict[Symbol, Decimal],
    prices: dict[Symbol, Decimal],
    *,
    fee_rate: Decimal,
    slippage: Decimal,
    band: Decimal,
    allow_buys: bool,
    instruments: dict[Symbol, InstrumentFilter],
    now: datetime | None = None,
    ignore_band: bool = False,
) -> RebalanceResult:
    """
    Move ``tracker`` toward ``targets`` at ``prices``.

    ``ignore_band`` forces the trade (used when flattening on a drawdown halt).
    """
    moment = now or datetime.now(tz=timezone.utc)
    book = _positions(tracker)
    symbols = list(dict.fromkeys([*targets.keys(), *book.keys()]))
    for symbol in symbols:
        if symbol not in prices or prices[symbol] <= _ZERO:
            raise ValueError(f"Missing a positive price for {symbol}.")

    equity = tracker.cash
    current_value: dict[Symbol, Decimal] = {}
    for symbol in symbols:
        qty = book.get(symbol, (_ZERO, _ZERO))[0]
        value = qty * prices[symbol]
        current_value[symbol] = value
        equity += value

    if equity <= _ZERO:
        return RebalanceResult(legs=(), held=True, blocked_buys=())

    needs_trade = ignore_band
    blocked: list[Symbol] = []
    effective: dict[Symbol, Decimal] = {}
    for symbol in symbols:
        target = targets.get(symbol, _ZERO)
        if target < _ZERO:
            target = _ZERO
        if target > _ONE:
            target = _ONE
        qty = book.get(symbol, (_ZERO, _ZERO))[0]
        current_w = current_value[symbol] / equity
        if not allow_buys and target > current_w:
            blocked.append(symbol)
            target = current_w
        effective[symbol] = target
        crosses_zero = (target == _ZERO and qty > _ZERO) or (target > _ZERO and qty == _ZERO)
        if crosses_zero or (target > _ZERO and abs(target - current_w) > band):
            needs_trade = True

    if not needs_trade:
        return RebalanceResult(legs=(), held=True, blocked_buys=tuple(blocked))

    legs: list[TradeLeg] = []

    # Sells first, sized off the pre-trade equity, matching the research engine.
    for symbol in symbols:
        qty = book.get(symbol, (_ZERO, _ZERO))[0]
        if qty <= _ZERO:
            continue
        target_value = effective[symbol] * equity
        held_value = current_value[symbol]
        if target_value >= held_value - _TINY:
            continue
        price = prices[symbol]
        sell_value = held_value if effective[symbol] == _ZERO else held_value - target_value
        raw_qty = min(qty, sell_value / price)
        instrument = instruments[symbol]
        closing = effective[symbol] == _ZERO
        snapped = qty if closing else _snap(raw_qty, instrument.qty_step)
        if snapped <= _ZERO:
            continue
        if not closing and (
            snapped < instrument.min_order_qty or snapped * price < instrument.min_order_usdt
        ):
            continue
        sell_px = price * (_ONE - slippage)
        fee = snapped * sell_px * fee_rate
        before = tracker.realized_pnl
        fill = Fill(
            order_id=str(uuid4()),
            symbol=symbol,
            side=OrderSide.SELL,
            filled_qty=snapped,
            avg_fill_price=sell_px,
            fee_usdt=fee,
            slippage_usdt=snapped * (price - sell_px),
            filled_at=moment,
            is_paper=True,
        )
        tracker.apply_fill(fill)
        remaining = tracker._positions.get(symbol)
        closed = remaining is None or remaining.qty == _ZERO
        legs.append(
            TradeLeg(
                fill=fill,
                closed_symbol=closed,
                realized_delta=tracker.realized_pnl - before,
            )
        )

    if not allow_buys:
        return RebalanceResult(legs=tuple(legs), held=False, blocked_buys=tuple(blocked))

    for symbol in symbols:
        target = effective[symbol]
        if target <= _ZERO:
            continue
        target_value = target * equity
        held_value = current_value[symbol]
        if target_value <= held_value + _TINY:
            continue
        price = prices[symbol]
        instrument = instruments[symbol]
        room = target_value - held_value
        budget = min(room, tracker.cash / (_ONE + fee_rate))
        buy_px = price * (_ONE + slippage)
        raw_qty = budget / buy_px if buy_px > _ZERO else _ZERO
        snapped = _snap(raw_qty, instrument.qty_step)
        if snapped < instrument.min_order_qty:
            continue
        notional = snapped * buy_px
        if notional < instrument.min_order_usdt:
            continue
        fee = notional * fee_rate
        if notional + fee > tracker.cash:
            continue
        fill = Fill(
            order_id=str(uuid4()),
            symbol=symbol,
            side=OrderSide.BUY,
            filled_qty=snapped,
            avg_fill_price=buy_px,
            fee_usdt=fee,
            slippage_usdt=snapped * (buy_px - price),
            filled_at=moment,
            is_paper=True,
        )
        before = tracker.realized_pnl
        tracker.apply_fill(fill)
        legs.append(
            TradeLeg(fill=fill, closed_symbol=False, realized_delta=tracker.realized_pnl - before)
        )

    return RebalanceResult(legs=tuple(legs), held=False, blocked_buys=tuple(blocked))
