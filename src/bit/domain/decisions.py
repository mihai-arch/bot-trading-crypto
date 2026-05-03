"""
Decision — output from the DecisionEngine.

Represents the aggregated conclusion for one symbol at one evaluation moment.
Every Decision is logged to the journal regardless of its state.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from .enums import DecisionState, StrategyId, Symbol


class Decision(BaseModel):
    """Aggregated decision for one symbol produced by DecisionEngine."""

    symbol: Symbol
    timestamp: datetime
    state: DecisionState
    composite_score: Decimal
    """Equal-weight average of all contributing signal scores (v1)."""
    contributing_strategies: list[StrategyId]
    rationale: str
    """Concatenated rationale from all strategies. Logged verbatim."""
    suggested_entry_price: Decimal | None = None
    """Set by DecisionEngine when state is ENTER. Based on last_price at decision time."""
