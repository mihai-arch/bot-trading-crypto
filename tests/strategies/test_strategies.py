"""
Strategy protocol and identity tests.

Verifies that all strategies:
1. Satisfy the BaseStrategy protocol (isinstance check)
2. Expose the correct strategy_id

Per-strategy logic tests live in dedicated files:
  - tests/strategies/test_trend_continuation.py
  - tests/strategies/test_breakout_confirmation.py
"""

from datetime import datetime, timezone
from decimal import Decimal

from bit.domain.enums import StrategyId, Symbol, Timeframe
from bit.domain.features import FeatureSet, KlineFeatures
from bit.strategies.base import BaseStrategy
from bit.strategies.breakout_confirmation import BreakoutConfirmationStrategy
from bit.strategies.trend_continuation import TrendContinuationStrategy


_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _empty_kline_features(timeframe: Timeframe) -> KlineFeatures:
    """KlineFeatures with no indicators computed — simulates insufficient history."""
    return KlineFeatures(timeframe=timeframe)


def _minimal_feature_set(symbol: Symbol = Symbol.BTCUSDT) -> FeatureSet:
    return FeatureSet(
        symbol=symbol,
        timestamp=_NOW,
        m5=_empty_kline_features(Timeframe.M5),
        m15=_empty_kline_features(Timeframe.M15),
        h1=_empty_kline_features(Timeframe.H1),
        last_price=Decimal("60000"),
    )


class TestStrategyProtocol:
    def test_trend_continuation_satisfies_protocol(self):
        strategy = TrendContinuationStrategy()
        assert isinstance(strategy, BaseStrategy)

    def test_breakout_confirmation_satisfies_protocol(self):
        strategy = BreakoutConfirmationStrategy()
        assert isinstance(strategy, BaseStrategy)


class TestStrategyIds:
    def test_trend_continuation_id(self):
        strategy = TrendContinuationStrategy()
        assert strategy.strategy_id == StrategyId.TREND_CONTINUATION

    def test_breakout_confirmation_id(self):
        strategy = BreakoutConfirmationStrategy()
        assert strategy.strategy_id == StrategyId.BREAKOUT_CONFIRMATION


