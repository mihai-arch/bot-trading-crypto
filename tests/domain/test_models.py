"""
Domain model validation tests.

These tests verify that Pydantic models accept valid data,
reject invalid data, and enforce field constraints correctly.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.decisions import Decision
from bit.domain.enums import DecisionState, OrderSide, OrderStatus, StrategyId, Symbol, Timeframe
from bit.domain.execution import Fill, Order
from bit.domain.features import FeatureSet, KlineFeatures
from bit.domain.journal import JournalEntry
from bit.domain.market import InstrumentFilter, Kline, OrderbookLevel, OrderbookTop, PortfolioState
from bit.domain.risk import SizingResult
from bit.domain.signals import Signal


_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestKline:
    def test_basic_construction(self):
        k = Kline(
            symbol=Symbol.BTCUSDT,
            timeframe=Timeframe.M5,
            open_time=_NOW,
            open=Decimal("60000"),
            high=Decimal("60500"),
            low=Decimal("59800"),
            close=Decimal("60200"),
            volume=Decimal("12.5"),
        )
        assert k.high >= k.close
        assert k.low <= k.open
        assert k.is_closed is True

    def test_open_candle(self):
        k = Kline(
            symbol=Symbol.ETHUSDT,
            timeframe=Timeframe.H1,
            open_time=_NOW,
            open=Decimal("3000"),
            high=Decimal("3050"),
            low=Decimal("2990"),
            close=Decimal("3020"),
            volume=Decimal("100"),
            is_closed=False,
        )
        assert k.is_closed is False


class TestSignal:
    def test_valid_signal(self):
        s = Signal(
            strategy_id=StrategyId.TREND_CONTINUATION,
            symbol=Symbol.BTCUSDT,
            timestamp=_NOW,
            score=Decimal("0.75"),
            rationale="EMA aligned, RSI above 50",
        )
        assert s.score == Decimal("0.75")

    def test_score_zero_is_valid(self):
        s = Signal(
            strategy_id=StrategyId.BREAKOUT_CONFIRMATION,
            symbol=Symbol.SOLUSDT,
            timestamp=_NOW,
            score=Decimal("0"),
            rationale="No conditions met",
        )
        assert s.score == Decimal("0")

    def test_score_one_is_valid(self):
        s = Signal(
            strategy_id=StrategyId.TREND_CONTINUATION,
            symbol=Symbol.BTCUSDT,
            timestamp=_NOW,
            score=Decimal("1"),
            rationale="All conditions met",
        )
        assert s.score == Decimal("1")

    def test_score_above_one_is_rejected(self):
        with pytest.raises(Exception):
            Signal(
                strategy_id=StrategyId.TREND_CONTINUATION,
                symbol=Symbol.BTCUSDT,
                timestamp=_NOW,
                score=Decimal("1.1"),
                rationale="invalid",
            )

    def test_score_below_zero_is_rejected(self):
        with pytest.raises(Exception):
            Signal(
                strategy_id=StrategyId.TREND_CONTINUATION,
                symbol=Symbol.BTCUSDT,
                timestamp=_NOW,
                score=Decimal("-0.01"),
                rationale="invalid",
            )


class TestDecision:
    def test_enter_decision(self):
        d = Decision(
            symbol=Symbol.BTCUSDT,
            timestamp=_NOW,
            state=DecisionState.ENTER,
            composite_score=Decimal("0.75"),
            contributing_strategies=[StrategyId.TREND_CONTINUATION],
            rationale="test",
            suggested_entry_price=Decimal("60000"),
        )
        assert d.state == DecisionState.ENTER
        assert d.suggested_entry_price is not None


class TestInstrumentFilter:
    def test_instrument_filter(self, btc_instrument):
        assert btc_instrument.tick_size > 0
        assert btc_instrument.qty_step > 0
        assert btc_instrument.min_order_qty > 0
        assert btc_instrument.min_order_usdt >= 1


class TestSizingResult:
    def test_approved_result(self):
        r = SizingResult(
            symbol=Symbol.BTCUSDT,
            approved=True,
            qty=Decimal("0.001"),
            notional_usdt=Decimal("60"),
            entry_price=Decimal("60000"),
        )
        assert r.approved
        assert r.rejection_reason is None

    def test_rejected_result(self):
        r = SizingResult(
            symbol=Symbol.BTCUSDT,
            approved=False,
            qty=Decimal("0"),
            notional_usdt=Decimal("0"),
            entry_price=Decimal("60000"),
            rejection_reason="Insufficient capital",
        )
        assert not r.approved
        assert r.rejection_reason is not None


class TestFill:
    def test_paper_fill(self):
        f = Fill(
            order_id="test-uuid",
            symbol=Symbol.BTCUSDT,
            side=OrderSide.BUY,
            filled_qty=Decimal("0.001"),
            avg_fill_price=Decimal("60030"),
            fee_usdt=Decimal("0.06003"),
            slippage_usdt=Decimal("0.03"),
            filled_at=_NOW,
            is_paper=True,
        )
        assert f.is_paper
        assert f.fee_usdt > 0
