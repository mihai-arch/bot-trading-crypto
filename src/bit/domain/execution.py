"""
Execution domain models — Order and Fill.

Order represents the intent to trade.
Fill represents what actually happened (paper or live).
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from .enums import OrderSide, OrderStatus, Symbol


class Order(BaseModel):
    """An order submitted to the exchange (or paper engine)."""

    order_id: str
    symbol: Symbol
    side: OrderSide
    qty: Decimal
    price: Decimal | None  # None = market order
    status: OrderStatus
    created_at: datetime
    is_paper: bool = True


class Fill(BaseModel):
    """
    The result of an executed order.

    In paper mode: fee_usdt and slippage_usdt are computed from BITConfig rates.
    In live mode: fee_usdt comes from the exchange response; slippage_usdt is estimated.
    """

    order_id: str
    symbol: Symbol
    side: OrderSide
    filled_qty: Decimal
    avg_fill_price: Decimal
    fee_usdt: Decimal
    slippage_usdt: Decimal
    filled_at: datetime
    is_paper: bool = True
