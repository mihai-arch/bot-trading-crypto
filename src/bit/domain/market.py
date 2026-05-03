"""
Raw market data domain models.

These types are produced by MarketDataService and consumed by FeatureEngine.
All prices and quantities use Decimal to avoid floating-point rounding errors.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from .enums import Symbol, Timeframe


class Kline(BaseModel):
    """A single OHLCV candle from the exchange."""

    symbol: Symbol
    timeframe: Timeframe
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool = True  # Only closed candles should be used for indicator computation.


class Ticker(BaseModel):
    """Live best bid/ask snapshot."""

    symbol: Symbol
    last_price: Decimal
    bid: Decimal
    ask: Decimal
    timestamp: datetime


class OrderbookLevel(BaseModel):
    price: Decimal
    qty: Decimal


class OrderbookTop(BaseModel):
    """Top N levels of the orderbook at a point in time."""

    symbol: Symbol
    bids: list[OrderbookLevel]  # Sorted best → worst (descending price)
    asks: list[OrderbookLevel]  # Sorted best → worst (ascending price)
    timestamp: datetime


class RecentTrade(BaseModel):
    """A single public trade from the exchange tape."""

    symbol: Symbol
    price: Decimal
    qty: Decimal
    side: str  # "Buy" | "Sell" — Bybit convention
    timestamp: datetime


class InstrumentFilter(BaseModel):
    """
    Exchange constraints for a trading symbol.

    These must be respected when sizing orders.
    Fetched once at startup and cached — they rarely change.
    """

    symbol: Symbol
    tick_size: Decimal      # Minimum price increment. Prices must be multiples of this.
    qty_step: Decimal       # Minimum quantity increment. Quantities must be multiples of this.
    min_order_qty: Decimal  # Minimum allowed order quantity in base asset.
    min_order_usdt: Decimal # Minimum allowed order value in USDT.


class Position(BaseModel):
    """A currently open position (paper or live)."""

    symbol: Symbol
    qty: Decimal
    avg_entry_price: Decimal
    unrealized_pnl_usdt: Decimal


class PortfolioState(BaseModel):
    """Current portfolio snapshot used by RiskEngine for sizing decisions."""

    total_equity_usdt: Decimal
    """Cash + market value of all open positions at current mark prices."""
    available_usdt: Decimal
    """Cash available to deploy (not tied up in open positions)."""
    open_positions: dict[Symbol, Position] = Field(default_factory=dict)
    realized_pnl_usdt: Decimal = Decimal("0")
    """Cumulative net realized PnL from all closed/reduced positions this session."""
