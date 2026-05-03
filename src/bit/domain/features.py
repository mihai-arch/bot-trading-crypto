"""
Feature models — computed indicators from raw kline data.

Produced by FeatureEngine. Consumed by SignalEngine (via strategies).
All fields are optional: strategies must handle None values gracefully
when insufficient history is available.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from .enums import Symbol, Timeframe


class KlineFeatures(BaseModel):
    """Computed indicators for a single timeframe."""

    timeframe: Timeframe
    ema_fast: Decimal | None = None    # EMA(9) of close
    ema_slow: Decimal | None = None    # EMA(21) of close
    rsi: Decimal | None = None         # RSI(14) of close; range 0–100
    atr: Decimal | None = None         # ATR(14); absolute price units
    volume_ma: Decimal | None = None   # SMA(20) of volume
    current_volume: Decimal | None = None
    high_20: Decimal | None = None     # Highest high over last 20 bars
    low_20: Decimal | None = None      # Lowest low over last 20 bars
    current_close: Decimal | None = None
    # Derived values — pre-computed for strategy convenience
    relative_volume: Decimal | None = None   # current_volume / volume_ma; >1.0 = above-average activity
    ema_distance_pct: Decimal | None = None  # (close - ema_fast) / ema_fast * 100; + = above EMA
    recent_return_pct: Decimal | None = None # (close[-1] - close[-6]) / close[-6] * 100; 5-bar return


class FeatureSet(BaseModel):
    """
    Complete feature snapshot for one symbol at one evaluation moment.

    Contains KlineFeatures for each timeframe plus cross-timeframe context.
    """

    symbol: Symbol
    timestamp: datetime
    m5: KlineFeatures
    m15: KlineFeatures
    h1: KlineFeatures
    last_price: Decimal
    spread_pct: Decimal | None = None  # (ask - bid) / mid_price; proxy for liquidity cost
