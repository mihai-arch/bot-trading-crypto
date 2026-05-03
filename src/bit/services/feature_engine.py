"""
FeatureEngine

Responsibility: Compute technical indicators and structured features from
normalized kline data. Pure computation — no I/O, no state, no side effects.

All public methods are deterministic: same inputs always produce same outputs.
This makes the FeatureEngine fully unit-testable without mocking.

Indicator conventions:
- EMA uses standard multiplier k = 2/(period+1), seeded with SMA of first
  `period` values, then applied forward through all remaining values.
- RSI uses Wilder's smoothing (k = 1/period). A flat or all-gain series
  returns 100 (no losses = maximum relative strength, Wilder convention).
- ATR uses Wilder's smoothing. Requires period+1 bars for period TRs.
- SMA always operates on the most recent `period` values.

All indicator helpers return None when there is insufficient history, letting
strategies handle the absence of data explicitly rather than silently.
"""

from datetime import datetime, timezone
from decimal import Decimal

from ..domain.enums import Symbol, Timeframe
from ..domain.features import FeatureSet, KlineFeatures
from ..domain.market import Kline, Ticker

# Number of recent bars used for the 5-bar simple return calculation.
_RETURN_LOOKBACK = 5


class FeatureEngine:
    """
    Computes KlineFeatures for each timeframe and assembles a FeatureSet.

    Minimum kline history required for all indicators:
    - EMA(9):  9 bars
    - EMA(21): 21 bars
    - RSI(14): 15 bars (14 periods of price changes)
    - ATR(14): 15 bars (14 true ranges, each needing a prior close)
    - Volume SMA(20): 20 bars
    - 20-bar high/low: 20 bars
    Recommended: pass at least 60 closed klines per timeframe.
    """

    def compute(
        self,
        symbol: Symbol,
        klines_5m: list[Kline],
        klines_15m: list[Kline],
        klines_1h: list[Kline],
        ticker: Ticker,
    ) -> FeatureSet:
        """
        Compute the full feature set for one symbol at one moment.

        Args:
            symbol: The trading symbol.
            klines_5m: 5-minute klines, oldest → newest. Only closed klines
                        should be passed; any open kline at the tail is dropped.
            klines_15m: 15-minute klines, same convention.
            klines_1h: 1-hour klines, same convention.
            ticker: Live ticker snapshot for last_price and spread.

        Returns:
            FeatureSet with KlineFeatures for all three timeframes.
            Any indicator that cannot be computed (insufficient history) is None.
        """
        m5 = self._compute_timeframe_features(klines_5m, Timeframe.M5)
        m15 = self._compute_timeframe_features(klines_15m, Timeframe.M15)
        h1 = self._compute_timeframe_features(klines_1h, Timeframe.H1)

        spread_pct: Decimal | None = None
        if ticker.bid > 0 and ticker.ask > 0:
            mid = (ticker.bid + ticker.ask) / 2
            spread_pct = (ticker.ask - ticker.bid) / mid * Decimal(100)

        return FeatureSet(
            symbol=symbol,
            timestamp=ticker.timestamp,
            m5=m5,
            m15=m15,
            h1=h1,
            last_price=ticker.last_price,
            spread_pct=spread_pct,
        )

    def _compute_timeframe_features(
        self,
        klines: list[Kline],
        timeframe: Timeframe,
    ) -> KlineFeatures:
        """
        Compute all indicators for one timeframe's kline sequence.

        Only closed klines (is_closed=True) are used. Any open/forming candle
        at the tail of the list is excluded before computation.

        Returns a KlineFeatures with None for any indicator that cannot be
        computed due to insufficient closed history.
        """
        closed = [k for k in klines if k.is_closed]

        if not closed:
            return KlineFeatures(timeframe=timeframe)

        closes = [k.close for k in closed]
        highs = [k.high for k in closed]
        lows = [k.low for k in closed]
        volumes = [k.volume for k in closed]

        ema_fast = self._ema(closes, 9)
        ema_slow = self._ema(closes, 21)
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)
        volume_ma = self._sma(volumes, 20)

        current_close = closes[-1]
        current_volume = volumes[-1]

        high_20 = max(highs[-20:]) if len(highs) >= 20 else None
        low_20 = min(lows[-20:]) if len(lows) >= 20 else None

        # Derived: relative volume (how current bar compares to average).
        relative_volume: Decimal | None = None
        if volume_ma is not None and volume_ma > 0:
            relative_volume = current_volume / volume_ma

        # Derived: distance of close from fast EMA as a percentage.
        ema_distance_pct: Decimal | None = None
        if ema_fast is not None and ema_fast > 0:
            ema_distance_pct = (current_close - ema_fast) / ema_fast * Decimal(100)

        # Derived: simple 5-bar return.
        recent_return_pct: Decimal | None = None
        if len(closes) >= _RETURN_LOOKBACK + 1:
            prev = closes[-(  _RETURN_LOOKBACK + 1)]
            if prev > 0:
                recent_return_pct = (closes[-1] - prev) / prev * Decimal(100)

        return KlineFeatures(
            timeframe=timeframe,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi,
            atr=atr,
            volume_ma=volume_ma,
            current_volume=current_volume,
            high_20=high_20,
            low_20=low_20,
            current_close=current_close,
            relative_volume=relative_volume,
            ema_distance_pct=ema_distance_pct,
            recent_return_pct=recent_return_pct,
        )

    # ── Indicator helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _sma(values: list[Decimal], period: int) -> Decimal | None:
        """
        Simple moving average of the most recent `period` values.

        Returns None if len(values) < period.
        """
        if len(values) < period:
            return None
        window = values[-period:]
        return sum(window, Decimal(0)) / period

    @staticmethod
    def _ema(values: list[Decimal], period: int) -> Decimal | None:
        """
        Exponential moving average.

        Uses multiplier k = 2/(period+1). Seeded with SMA of the first
        `period` values, then applied forward through all remaining values.
        Returns None if len(values) < period.
        """
        if len(values) < period:
            return None

        k = Decimal(2) / Decimal(period + 1)
        # Seed: SMA of first `period` values.
        ema = sum(values[:period], Decimal(0)) / period
        # Apply forward.
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    @staticmethod
    def _rsi(closes: list[Decimal], period: int = 14) -> Decimal | None:
        """
        Relative Strength Index using Wilder's smoothing.

        Requires at least period+1 closes (to produce period price changes).
        Returns None if insufficient data.

        Edge cases:
        - All gains (avg_loss == 0): returns Decimal(100). Wilder convention.
        - All losses (avg_gain == 0): returns Decimal(0).
        - Flat series (avg_gain == avg_loss == 0): returns Decimal(100).
        """
        if len(closes) < period + 1:
            return None

        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, Decimal(0)) for d in changes]
        losses = [abs(min(d, Decimal(0))) for d in changes]

        # Wilder seed: simple average of first `period` gains/losses.
        avg_gain = sum(gains[:period], Decimal(0)) / period
        avg_loss = sum(losses[:period], Decimal(0)) / period

        # Wilder smoothing for all subsequent values.
        for i in range(period, len(changes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return Decimal(100)

        rs = avg_gain / avg_loss
        return Decimal(100) - Decimal(100) / (1 + rs)

    @staticmethod
    def _atr(
        highs: list[Decimal],
        lows: list[Decimal],
        closes: list[Decimal],
        period: int = 14,
    ) -> Decimal | None:
        """
        Average True Range using Wilder's smoothing.

        True Range = max(high - low, |high - prev_close|, |low - prev_close|).
        Requires at least period+1 bars to produce period true ranges.
        Returns None if insufficient data.
        """
        if len(closes) < period + 1:
            return None

        trs: list[Decimal] = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)

        # Wilder seed: simple average of first `period` true ranges.
        atr = sum(trs[:period], Decimal(0)) / period

        # Wilder smoothing for all subsequent true ranges.
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period

        return atr
