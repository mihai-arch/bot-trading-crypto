"""
ExecutionEngine

Responsibility: Submit orders and return Fill results.

Paper mode (default):
  Simulates a market fill locally using configured fee and slippage rates.
  No network calls. Deterministic given the same SizingResult and config.

Live mode (paper_trading=False):
  Submits a market order to Bybit REST API.
  NOT IMPLEMENTED in v1. Attempting live execution raises NotImplementedError.

The paper/live branch is transparent to callers — both modes accept the same
inputs and return the same Fill type.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from ..config import BITConfig
from ..domain.decisions import Decision
from ..domain.enums import OrderSide, Symbol
from ..domain.execution import Fill
from ..domain.risk import SizingResult


class ExecutionEngine:
    def __init__(self, config: BITConfig) -> None:
        self._config = config

    async def execute(self, sizing: SizingResult, decision: Decision) -> Fill:
        """
        Execute an approved sizing result.

        Raises:
            ValueError: If sizing.approved is False.
            NotImplementedError: If paper_trading=False (live mode not in v1).
        """
        if not sizing.approved:
            raise ValueError(
                f"Cannot execute a rejected SizingResult for {sizing.symbol}: "
                f"{sizing.rejection_reason}"
            )

        if not self._config.paper_trading:
            return await self._live_execute(sizing, decision)

        return self._paper_execute(sizing)

    def _paper_execute(self, sizing: SizingResult) -> Fill:
        """
        Simulate a market fill.

        Slippage is modeled as adverse price movement (buy fills above quoted price).
        Fee is applied to the filled notional value.
        """
        order_id = str(uuid4())

        slippage_factor = Decimal("1") + self._config.paper_slippage_pct
        fill_price = sizing.entry_price * slippage_factor

        filled_notional = sizing.qty * fill_price
        fee = filled_notional * self._config.paper_fee_rate
        slippage_cost = sizing.qty * (fill_price - sizing.entry_price)

        return Fill(
            order_id=order_id,
            symbol=sizing.symbol,
            side=OrderSide.BUY,
            filled_qty=sizing.qty,
            avg_fill_price=fill_price,
            fee_usdt=fee,
            slippage_usdt=slippage_cost,
            filled_at=datetime.now(tz=timezone.utc),
            is_paper=True,
        )

    def execute_exit_paper(
        self, symbol: Symbol, qty: Decimal, current_price: Decimal
    ) -> Fill:
        """
        Simulate a SELL market fill for exiting an open position.

        Slippage is adverse for a sell (fill price below current price).
        Fee is applied to the filled sell notional.

        Raises:
            NotImplementedError: If paper_trading=False (live exit not in v1).
        """
        if not self._config.paper_trading:
            raise NotImplementedError(
                "Live exit execution is not implemented in v1. "
                "Set paper_trading=True in your configuration."
            )
        slippage_factor = Decimal("1") - self._config.paper_slippage_pct
        fill_price = current_price * slippage_factor
        fee = qty * fill_price * self._config.paper_fee_rate
        slippage_cost = qty * (current_price - fill_price)
        return Fill(
            order_id=str(uuid4()),
            symbol=symbol,
            side=OrderSide.SELL,
            filled_qty=qty,
            avg_fill_price=fill_price,
            fee_usdt=fee,
            slippage_usdt=slippage_cost,
            filled_at=datetime.now(tz=timezone.utc),
            is_paper=True,
        )

    async def _live_execute(self, sizing: SizingResult, decision: Decision) -> Fill:
        """Submit a real order to Bybit. Not implemented in v1."""
        raise NotImplementedError(
            "Live execution is not implemented in v1. "
            "Set paper_trading=True in your configuration."
        )
