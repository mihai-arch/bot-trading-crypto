"""
Core enumerations for BIT.

All string values match Bybit API conventions where applicable.
"""

from enum import StrEnum


class Symbol(StrEnum):
    BTCUSDT = "BTCUSDT"
    ETHUSDT = "ETHUSDT"
    SOLUSDT = "SOLUSDT"


class Timeframe(StrEnum):
    """Bybit kline interval values."""

    M5 = "5"
    M15 = "15"
    H1 = "60"


class DecisionState(StrEnum):
    """Explicit output states from the DecisionEngine."""

    ENTER = "ENTER"      # All conditions met; RiskEngine approval required before execution.
    MONITOR = "MONITOR"  # Setup forming; no action this cycle.
    REJECT = "REJECT"    # Conditions not met or risk denied; log reason and skip.


class OrderSide(StrEnum):
    """
    Bybit order side values.

    v1 executes BUY only (long-only). SELL is defined here so that the Fill
    model and PaperPortfolioTracker can represent exit fills without a separate
    enum, and to allow future sell execution support without a domain change.
    """

    BUY = "Buy"
    SELL = "Sell"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class StrategyId(StrEnum):
    TREND_CONTINUATION = "trend_continuation"
    BREAKOUT_CONFIRMATION = "breakout_confirmation"
