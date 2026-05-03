"""
ExecutionEngine tests — paper mode only.

Paper execution is synchronous and deterministic given fixed config values.
Tests verify fee calculation, slippage application, and live guard.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.config import BITConfig
from bit.domain.decisions import Decision
from bit.domain.enums import DecisionState, OrderSide, StrategyId, Symbol
from bit.domain.risk import SizingResult
from bit.services.execution_engine import ExecutionEngine


_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _sizing(
    symbol: Symbol = Symbol.BTCUSDT,
    price: Decimal = Decimal("60000"),
    qty: Decimal = Decimal("0.001"),
) -> SizingResult:
    return SizingResult(
        symbol=symbol,
        approved=True,
        qty=qty,
        notional_usdt=qty * price,
        entry_price=price,
    )


def _decision(symbol: Symbol = Symbol.BTCUSDT) -> Decision:
    return Decision(
        symbol=symbol,
        timestamp=_NOW,
        state=DecisionState.ENTER,
        composite_score=Decimal("0.75"),
        contributing_strategies=[StrategyId.TREND_CONTINUATION],
        rationale="test",
        suggested_entry_price=Decimal("60000"),
    )


class TestPaperExecution:
    async def test_fill_is_paper(self, config):
        engine = ExecutionEngine(config)
        fill = await engine.execute(_sizing(), _decision())
        assert fill.is_paper is True

    async def test_fill_side_is_buy(self, config):
        engine = ExecutionEngine(config)
        fill = await engine.execute(_sizing(), _decision())
        assert fill.side == OrderSide.BUY

    async def test_fill_qty_matches_sizing(self, config):
        engine = ExecutionEngine(config)
        sizing = _sizing(qty=Decimal("0.002"))
        fill = await engine.execute(sizing, _decision())
        assert fill.filled_qty == Decimal("0.002")

    async def test_fill_price_includes_slippage(self, config):
        """Fill price must be higher than entry price (adverse slippage for a buy)."""
        engine = ExecutionEngine(config)
        sizing = _sizing(price=Decimal("60000"))
        fill = await engine.execute(sizing, _decision())
        assert fill.avg_fill_price > sizing.entry_price

    async def test_fill_price_slippage_matches_config(self, config):
        """Fill price = entry_price * (1 + paper_slippage_pct)."""
        engine = ExecutionEngine(config)
        sizing = _sizing(price=Decimal("60000"))
        fill = await engine.execute(sizing, _decision())
        expected_price = Decimal("60000") * (Decimal("1") + config.paper_slippage_pct)
        assert fill.avg_fill_price == expected_price

    async def test_fee_is_positive(self, config):
        engine = ExecutionEngine(config)
        fill = await engine.execute(_sizing(), _decision())
        assert fill.fee_usdt > 0

    async def test_fee_matches_config_rate(self, config):
        """fee = filled_notional * paper_fee_rate."""
        engine = ExecutionEngine(config)
        sizing = _sizing(price=Decimal("60000"), qty=Decimal("0.001"))
        fill = await engine.execute(sizing, _decision())
        expected_fee = sizing.qty * fill.avg_fill_price * config.paper_fee_rate
        assert abs(fill.fee_usdt - expected_fee) < Decimal("0.000001")

    async def test_slippage_cost_is_non_negative(self, config):
        engine = ExecutionEngine(config)
        fill = await engine.execute(_sizing(), _decision())
        assert fill.slippage_usdt >= 0

    async def test_order_id_is_populated(self, config):
        engine = ExecutionEngine(config)
        fill = await engine.execute(_sizing(), _decision())
        assert fill.order_id
        assert len(fill.order_id) > 0


class TestLiveExecutionGuard:
    async def test_live_raises_not_implemented(self):
        """Live execution must raise NotImplementedError in v1."""
        live_config = BITConfig(paper_trading=False)
        engine = ExecutionEngine(live_config)
        with pytest.raises(NotImplementedError):
            await engine.execute(_sizing(), _decision())

    async def test_rejected_sizing_raises_value_error(self, config):
        """Executing a rejected SizingResult must raise ValueError."""
        engine = ExecutionEngine(config)
        rejected = SizingResult(
            symbol=Symbol.BTCUSDT,
            approved=False,
            qty=Decimal("0"),
            notional_usdt=Decimal("0"),
            entry_price=Decimal("60000"),
            rejection_reason="test rejection",
        )
        with pytest.raises(ValueError):
            await engine.execute(rejected, _decision())
