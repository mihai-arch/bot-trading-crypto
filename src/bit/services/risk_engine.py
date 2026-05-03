"""
RiskEngine

Responsibility: Enforce capital allocation and position sizing constraints.
Given an ENTER decision, determine whether to approve it and at what size.

Rules enforced in v1:
1. Maximum open positions (config.max_open_positions)
2. Maximum capital per position (config.max_position_pct * available_usdt)
3. Minimum notional value (instrument.min_order_usdt)
4. Minimum order quantity (instrument.min_order_qty)
5. Quantity snapped to qty_step (floor — never exceed budget)

This service is fully deterministic — no I/O, no state, unit-testable.
"""

from decimal import ROUND_DOWN, Decimal

from ..config import BITConfig
from ..domain.decisions import Decision
from ..domain.market import InstrumentFilter, PortfolioState
from ..domain.risk import SizingResult


class RiskEngine:
    def __init__(self, config: BITConfig) -> None:
        self._config = config

    def approve(
        self,
        decision: Decision,
        portfolio: PortfolioState,
        instrument: InstrumentFilter,
    ) -> SizingResult:
        """
        Validate and size a position for an ENTER decision.

        Returns SizingResult with approved=True and calculated qty/notional,
        or approved=False with a rejection_reason explaining which rule failed.
        """
        # Guard: entry price must be set before calling approve.
        if decision.suggested_entry_price is None:
            return self._reject(
                decision, instrument, "No entry price set on decision."
            )

        # Rule 1: Max open positions.
        open_count = len(portfolio.open_positions)
        if open_count >= self._config.max_open_positions:
            return self._reject(
                decision,
                instrument,
                f"Max open positions reached ({open_count}/{self._config.max_open_positions}).",
            )

        # Rule 2: Max capital per position.
        max_notional = portfolio.available_usdt * self._config.max_position_pct

        # Rule 3: Minimum notional check.
        if max_notional < instrument.min_order_usdt:
            return self._reject(
                decision,
                instrument,
                f"Max allocatable capital ({max_notional:.2f} USDT) is below "
                f"min order value ({instrument.min_order_usdt} USDT).",
            )

        # Compute raw quantity and snap down to qty_step.
        raw_qty = max_notional / decision.suggested_entry_price
        steps = (raw_qty / instrument.qty_step).to_integral_value(rounding=ROUND_DOWN)
        qty = steps * instrument.qty_step

        # Rule 4: Minimum order quantity.
        if qty < instrument.min_order_qty:
            return self._reject(
                decision,
                instrument,
                f"Computed qty ({qty}) is below min order qty ({instrument.min_order_qty}).",
            )

        notional = qty * decision.suggested_entry_price

        return SizingResult(
            symbol=decision.symbol,
            approved=True,
            qty=qty,
            notional_usdt=notional,
            entry_price=decision.suggested_entry_price,
        )

    @staticmethod
    def _reject(
        decision: Decision,
        instrument: InstrumentFilter,
        reason: str,
    ) -> SizingResult:
        return SizingResult(
            symbol=decision.symbol,
            approved=False,
            qty=Decimal("0"),
            notional_usdt=Decimal("0"),
            entry_price=decision.suggested_entry_price or Decimal("0"),
            rejection_reason=reason,
        )
