"""
Risk domain models — output from RiskEngine.
"""

from decimal import Decimal

from pydantic import BaseModel

from .enums import Symbol


class SizingResult(BaseModel):
    """
    Position sizing decision from RiskEngine.

    If approved=False, qty and notional_usdt are zero and rejection_reason explains why.
    If approved=True, qty and notional_usdt reflect the actual order that should be placed.
    """

    symbol: Symbol
    approved: bool
    qty: Decimal
    """Quantity in base asset, snapped to qty_step (floor)."""
    notional_usdt: Decimal
    """Expected order value: qty * entry_price."""
    entry_price: Decimal
    stop_price: Decimal | None = None
    """Optional stop-loss price. Not used in v1 — reserved for future use."""
    rejection_reason: str | None = None
    """Populated when approved=False. Always log this."""
