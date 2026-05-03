"""
Shared test fixtures for BIT.

Fixtures defined here are available to all tests under tests/.
All fixtures use realistic but conservative values matching v1 config defaults.
"""

from decimal import Decimal

import pytest

from bit.config import BITConfig
from bit.domain.enums import Symbol
from bit.domain.market import InstrumentFilter, PortfolioState


@pytest.fixture
def config() -> BITConfig:
    """Default BITConfig with paper trading enabled and conservative thresholds."""
    return BITConfig(
        bybit_api_key="test-key",
        bybit_api_secret="test-secret",
        bybit_testnet=True,
        paper_trading=True,
        capital_usdt=Decimal("500"),
        max_position_pct=Decimal("0.20"),
        max_open_positions=3,
        paper_fee_rate=Decimal("0.001"),
        paper_slippage_pct=Decimal("0.0005"),
        enter_threshold=Decimal("0.65"),
        monitor_threshold=Decimal("0.40"),
    )


@pytest.fixture
def btc_instrument() -> InstrumentFilter:
    """Realistic BTC/USDT spot instrument filter (Bybit approximate values)."""
    return InstrumentFilter(
        symbol=Symbol.BTCUSDT,
        tick_size=Decimal("0.01"),
        qty_step=Decimal("0.000001"),
        min_order_qty=Decimal("0.000048"),
        min_order_usdt=Decimal("1"),
    )


@pytest.fixture
def eth_instrument() -> InstrumentFilter:
    """Realistic ETH/USDT spot instrument filter."""
    return InstrumentFilter(
        symbol=Symbol.ETHUSDT,
        tick_size=Decimal("0.01"),
        qty_step=Decimal("0.0001"),
        min_order_qty=Decimal("0.0005"),
        min_order_usdt=Decimal("1"),
    )


@pytest.fixture
def empty_portfolio() -> PortfolioState:
    """Portfolio with full capital available and no open positions."""
    return PortfolioState(
        total_equity_usdt=Decimal("500"),
        available_usdt=Decimal("500"),
    )


@pytest.fixture
def depleted_portfolio() -> PortfolioState:
    """Portfolio with very little available capital."""
    return PortfolioState(
        total_equity_usdt=Decimal("500"),
        available_usdt=Decimal("0.50"),
    )
