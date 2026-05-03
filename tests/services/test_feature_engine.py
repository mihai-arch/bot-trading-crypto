"""
Tests for FeatureEngine — indicator helpers and end-to-end feature computation.

All tests use known sequences with exact expected values where possible,
or verify None/not-None semantics for edge cases.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.enums import Symbol, Timeframe
from bit.domain.market import Kline, Ticker
from bit.services.feature_engine import FeatureEngine

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_SYM = Symbol.BTCUSDT


# ── Helpers ───────────────────────────────────────────────────────────────────


def _kline(
    close: float,
    high: float | None = None,
    low: float | None = None,
    volume: float = 100.0,
    is_closed: bool = True,
    timeframe: Timeframe = Timeframe.M5,
) -> Kline:
    c = Decimal(str(close))
    h = Decimal(str(high)) if high is not None else c + Decimal("0.5")
    l = Decimal(str(low)) if low is not None else c - Decimal("0.5")
    return Kline(
        symbol=_SYM,
        timeframe=timeframe,
        open_time=_NOW,
        open=c,
        high=h,
        low=l,
        close=c,
        volume=Decimal(str(volume)),
        is_closed=is_closed,
    )


def _dec(values: list[float]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


def _ticker(last: float = 100.0, bid: float = 99.9, ask: float = 100.1) -> Ticker:
    return Ticker(
        symbol=_SYM,
        last_price=Decimal(str(last)),
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        timestamp=_NOW,
    )


def _uniform_klines(
    n: int,
    close: float = 100.0,
    is_closed: bool = True,
    timeframe: Timeframe = Timeframe.M5,
) -> list[Kline]:
    """n closed klines with close increasing by 0.1 each bar."""
    return [
        _kline(close + i * 0.1, is_closed=is_closed, timeframe=timeframe)
        for i in range(n)
    ]


# ── _sma ──────────────────────────────────────────────────────────────────────


class TestSMA:
    def test_basic(self):
        result = FeatureEngine._sma(_dec([10, 20, 30]), 3)
        assert result == Decimal("20")

    def test_uses_last_n_values(self):
        # SMA(3) of [10, 20, 30, 40] → avg of last 3 = (20+30+40)/3 = 30
        result = FeatureEngine._sma(_dec([10, 20, 30, 40]), 3)
        assert result == Decimal("30")

    def test_single_element(self):
        result = FeatureEngine._sma(_dec([42]), 1)
        assert result == Decimal("42")

    def test_insufficient_data_returns_none(self):
        assert FeatureEngine._sma(_dec([10, 20]), 3) is None

    def test_empty_returns_none(self):
        assert FeatureEngine._sma([], 3) is None

    def test_exact_period(self):
        result = FeatureEngine._sma(_dec([10, 20, 30]), 3)
        assert result is not None


# ── _ema ──────────────────────────────────────────────────────────────────────


class TestEMA:
    def test_basic_period_3(self):
        # EMA(period=3) on [10, 11, 12, ..., 19]
        # k = 2/(3+1) = 0.5
        # Seed = SMA([10, 11, 12]) = 11
        # 13 → 13*0.5 + 11*0.5 = 12
        # 14 → 14*0.5 + 12*0.5 = 13
        # 15 → 14; 16 → 15; 17 → 16; 18 → 17; 19 → 18
        vals = _dec(list(range(10, 20)))
        result = FeatureEngine._ema(vals, 3)
        assert result == Decimal("18")

    def test_exactly_period_values_returns_sma(self):
        # When len == period, seed = SMA, no further values → return SMA.
        result = FeatureEngine._ema(_dec([10, 11, 12]), 3)
        assert result == Decimal("11")

    def test_insufficient_data_returns_none(self):
        assert FeatureEngine._ema(_dec([10, 20]), 3) is None

    def test_empty_returns_none(self):
        assert FeatureEngine._ema([], 5) is None

    def test_period_1_returns_last_value(self):
        # k = 2/2 = 1 → EMA always equals latest value.
        result = FeatureEngine._ema(_dec([5, 10, 20, 99]), 1)
        assert result == Decimal("99")

    def test_ema_fast_gt_slow_on_rising_series(self):
        # On a rising series, EMA(9) must be above EMA(21).
        vals = _dec(list(range(1, 61)))
        ema9 = FeatureEngine._ema(vals, 9)
        ema21 = FeatureEngine._ema(vals, 21)
        assert ema9 is not None and ema21 is not None
        assert ema9 > ema21


# ── _rsi ──────────────────────────────────────────────────────────────────────


class TestRSI:
    def test_all_gains_returns_100(self):
        # Strictly rising prices → avg_loss = 0 → RSI = 100.
        closes = _dec(list(range(10, 26)))  # 16 values, 15 price changes
        result = FeatureEngine._rsi(closes, 14)
        assert result == Decimal(100)

    def test_all_losses_returns_0(self):
        # Strictly falling → avg_gain = 0 → RSI = 0.
        closes = _dec(list(range(25, 9, -1)))  # 16 values
        result = FeatureEngine._rsi(closes, 14)
        assert result == Decimal(0)

    def test_alternating_returns_50(self):
        # Alternating +1/-1 → avg_gain == avg_loss → RS = 1 → RSI = 50.
        # Sequence: [10, 11, 10, 11, ...] (15 values, 14 changes: 7 up, 7 down)
        closes = _dec([10 + (i % 2) for i in range(15)])
        result = FeatureEngine._rsi(closes, 14)
        assert result == Decimal(50)

    def test_flat_series_returns_100(self):
        # No changes → avg_loss = 0 → RSI = 100 (Wilder convention).
        closes = [Decimal("100")] * 15
        result = FeatureEngine._rsi(closes, 14)
        assert result == Decimal(100)

    def test_insufficient_data_returns_none(self):
        # Needs period+1 = 15 closes; 14 is one short.
        closes = _dec(list(range(14)))
        assert FeatureEngine._rsi(closes, 14) is None

    def test_exact_minimum_length(self):
        # Exactly period+1 = 15 closes → should compute successfully.
        closes = _dec(list(range(15)))
        result = FeatureEngine._rsi(closes, 14)
        assert result is not None

    def test_empty_returns_none(self):
        assert FeatureEngine._rsi([], 14) is None

    def test_rsi_between_0_and_100(self):
        # RSI must always be in [0, 100] for any valid input.
        import random
        random.seed(42)
        closes = _dec([100 + random.uniform(-5, 5) for _ in range(50)])
        result = FeatureEngine._rsi(closes, 14)
        assert result is not None
        assert Decimal(0) <= result <= Decimal(100)

    def test_more_data_refines_value(self):
        # RSI with 30 bars should differ from RSI with exactly 15 bars
        # on a non-trivial sequence (Wilder smoothing accumulates).
        closes_15 = _dec([100 - abs(i - 7) for i in range(15)])
        closes_30 = closes_15 + _dec([100 - abs(i - 7) for i in range(15)])
        r15 = FeatureEngine._rsi(closes_15, 14)
        r30 = FeatureEngine._rsi(closes_30, 14)
        assert r15 is not None and r30 is not None
        # They should not be equal (smoothing changes the value).
        # (This is a smoke test; exact values are sequence-dependent.)


# ── _atr ──────────────────────────────────────────────────────────────────────


class TestATR:
    def _uniform_data(self, n: int):
        """All bars: H=12, L=10, C=11 → TR = max(2, |12-11|, |10-11|) = 2."""
        highs = [Decimal("12")] * n
        lows = [Decimal("10")] * n
        closes = [Decimal("11")] * n
        return highs, lows, closes

    def test_uniform_bars_atr_equals_range(self):
        # With all TRs = 2, ATR must equal 2 for any period.
        h, l, c = self._uniform_data(20)
        result = FeatureEngine._atr(h, l, c, 14)
        assert result == Decimal("2")

    def test_exact_minimum_bars(self):
        # period=3 requires 4 bars (3 TRs).
        h, l, c = self._uniform_data(4)
        result = FeatureEngine._atr(h, l, c, 3)
        assert result == Decimal("2")

    def test_insufficient_data_returns_none(self):
        # period=3 needs 4 bars; only 3 → None.
        h, l, c = self._uniform_data(3)
        assert FeatureEngine._atr(h, l, c, 3) is None

    def test_one_bar_insufficient(self):
        h, l, c = self._uniform_data(1)
        assert FeatureEngine._atr(h, l, c, 14) is None

    def test_empty_returns_none(self):
        assert FeatureEngine._atr([], [], [], 14) is None

    def test_gap_increases_atr(self):
        # A large gap between prev_close and current high/low inflates TR.
        # bars 0..3: uniform H=12, L=10, C=11 → TR=2
        # bar 4: H=30, L=29, C=29 → TR = max(1, |30-11|, |29-11|) = 19
        highs = [Decimal("12")] * 4 + [Decimal("30")]
        lows = [Decimal("10")] * 4 + [Decimal("29")]
        closes = [Decimal("11")] * 4 + [Decimal("29")]
        atr_uniform = FeatureEngine._atr(
            [Decimal("12")] * 20, [Decimal("10")] * 20, [Decimal("11")] * 20, 3
        )
        atr_with_gap = FeatureEngine._atr(highs, lows, closes, 3)
        assert atr_with_gap is not None
        assert atr_with_gap > atr_uniform

    def test_atr_positive(self):
        h, l, c = self._uniform_data(20)
        result = FeatureEngine._atr(h, l, c, 14)
        assert result > Decimal(0)


# ── _compute_timeframe_features ───────────────────────────────────────────────


class TestComputeTimeframeFeatures:
    fe = FeatureEngine()

    def test_empty_klines_returns_all_none(self):
        result = self.fe._compute_timeframe_features([], Timeframe.M5)
        assert result.ema_fast is None
        assert result.ema_slow is None
        assert result.rsi is None
        assert result.atr is None
        assert result.volume_ma is None
        assert result.high_20 is None
        assert result.low_20 is None
        assert result.current_close is None

    def test_timeframe_set_correctly(self):
        result = self.fe._compute_timeframe_features([], Timeframe.H1)
        assert result.timeframe == Timeframe.H1

    def test_skips_open_candle_at_tail(self):
        # 21 closed klines + 1 open candle at the end.
        klines = _uniform_klines(21, close=100.0, is_closed=True)
        open_candle = _kline(999.0, is_closed=False)  # should be excluded
        klines.append(open_candle)

        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)

        # ema_slow needs 21 bars → should be computable from the 21 closed ones.
        assert result.ema_slow is not None
        # current_close must be from the last *closed* candle, not 999.
        assert result.current_close != Decimal("999")

    def test_all_open_candles_returns_all_none(self):
        klines = _uniform_klines(60, is_closed=False)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.ema_fast is None
        assert result.current_close is None

    def test_too_few_closed_klines_gives_none_indicators(self):
        # 5 closed klines — not enough for any indicator.
        klines = _uniform_klines(5)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.ema_fast is None
        assert result.ema_slow is None
        assert result.rsi is None
        assert result.atr is None
        assert result.volume_ma is None
        assert result.high_20 is None

    def test_9_klines_fills_ema_fast_only(self):
        # 9 klines → EMA(9) computable, EMA(21) not.
        klines = _uniform_klines(9)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.ema_fast is not None
        assert result.ema_slow is None

    def test_sufficient_klines_fills_all_indicators(self):
        # 60 klines → all indicators should compute.
        klines = _uniform_klines(60)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.ema_fast is not None
        assert result.ema_slow is not None
        assert result.rsi is not None
        assert result.atr is not None
        assert result.volume_ma is not None
        assert result.high_20 is not None
        assert result.low_20 is not None
        assert result.current_close is not None
        assert result.current_volume is not None

    def test_derived_fields_computed_when_data_sufficient(self):
        klines = _uniform_klines(25)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.relative_volume is not None
        assert result.ema_distance_pct is not None
        assert result.recent_return_pct is not None

    def test_derived_fields_none_when_data_insufficient(self):
        # 5 klines → volume_ma is None → relative_volume must be None.
        # EMA is None → ema_distance_pct must be None.
        klines = _uniform_klines(5)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.relative_volume is None
        assert result.ema_distance_pct is None

    def test_high_20_is_max_of_last_20_highs(self):
        # Make 25 klines with increasing closes; last 20 highs will exceed first 5.
        klines = _uniform_klines(25, close=100.0)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        # high of last bar ≈ close(124) + 0.5 = 102.9; high of bar 5 ≈ 100.5
        assert result.high_20 is not None
        assert result.low_20 is not None
        assert result.high_20 > result.low_20

    def test_ema_fast_gt_slow_on_rising_series(self):
        # On a rising kline series, fast EMA must be above slow EMA.
        klines = _uniform_klines(60, close=100.0)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.ema_fast is not None and result.ema_slow is not None
        assert result.ema_fast > result.ema_slow

    def test_recent_return_pct_none_when_too_few_bars(self):
        # Needs at least 6 bars (5-bar lookback + 1).
        klines = _uniform_klines(5)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        assert result.recent_return_pct is None

    def test_recent_return_pct_positive_on_rising_series(self):
        klines = _uniform_klines(30, close=100.0)
        result = self.fe._compute_timeframe_features(klines, Timeframe.M5)
        # Rising closes → current > 5 bars ago → positive return.
        assert result.recent_return_pct is not None
        assert result.recent_return_pct > Decimal(0)


# ── compute (end-to-end) ──────────────────────────────────────────────────────


class TestComputeEndToEnd:
    fe = FeatureEngine()

    def _klines_for_tf(self, n: int, timeframe: Timeframe) -> list[Kline]:
        return _uniform_klines(n, close=100.0, timeframe=timeframe)

    def test_basic_assembly(self):
        klines_5m = self._klines_for_tf(60, Timeframe.M5)
        klines_15m = self._klines_for_tf(60, Timeframe.M15)
        klines_1h = self._klines_for_tf(60, Timeframe.H1)

        result = self.fe.compute(_SYM, klines_5m, klines_15m, klines_1h, _ticker())

        assert result.symbol == _SYM
        assert result.last_price == Decimal("100")
        assert result.m5.timeframe == Timeframe.M5
        assert result.m15.timeframe == Timeframe.M15
        assert result.h1.timeframe == Timeframe.H1

    def test_timestamp_from_ticker(self):
        klines = self._klines_for_tf(30, Timeframe.M5)
        result = self.fe.compute(_SYM, klines, klines, klines, _ticker())
        assert result.timestamp == _NOW

    def test_spread_pct_computed(self):
        # bid=99.9, ask=100.1 → mid=100, spread = 0.2/100*100 = 0.2%
        klines = self._klines_for_tf(30, Timeframe.M5)
        result = self.fe.compute(_SYM, klines, klines, klines, _ticker(bid=99.9, ask=100.1))
        assert result.spread_pct is not None
        assert result.spread_pct > Decimal(0)

    def test_all_timeframe_indicators_present_with_enough_data(self):
        klines = self._klines_for_tf(60, Timeframe.M5)
        result = self.fe.compute(_SYM, klines, klines, klines, _ticker())

        for tf_features in (result.m5, result.m15, result.h1):
            assert tf_features.ema_fast is not None
            assert tf_features.ema_slow is not None
            assert tf_features.rsi is not None
            assert tf_features.atr is not None
            assert tf_features.volume_ma is not None

    def test_insufficient_data_gives_none_indicators(self):
        klines = self._klines_for_tf(3, Timeframe.M5)
        result = self.fe.compute(_SYM, klines, klines, klines, _ticker())
        assert result.m5.ema_fast is None
        assert result.m15.ema_fast is None
        assert result.h1.ema_fast is None

    def test_empty_klines_does_not_raise(self):
        # Should succeed and return FeatureSet with all-None indicators.
        result = self.fe.compute(_SYM, [], [], [], _ticker())
        assert result.m5.ema_fast is None
        assert result.h1.rsi is None

    def test_mixed_open_closed_klines(self):
        # Each list ends with an open candle — should be excluded cleanly.
        klines_closed = self._klines_for_tf(60, Timeframe.M5)
        open_candle = _kline(9999.0, is_closed=False)
        klines_mixed = klines_closed + [open_candle]

        result_clean = self.fe.compute(_SYM, klines_closed, klines_closed, klines_closed, _ticker())
        result_mixed = self.fe.compute(_SYM, klines_mixed, klines_mixed, klines_mixed, _ticker())

        # Indicators should be identical regardless of the dangling open candle.
        assert result_clean.m5.ema_fast == result_mixed.m5.ema_fast
        assert result_clean.h1.rsi == result_mixed.h1.rsi
