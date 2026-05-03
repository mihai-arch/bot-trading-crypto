"""
JournalEntry — one complete record of a pipeline cycle outcome.

Every evaluation cycle produces exactly one JournalEntry regardless of
the decision state. This is the primary audit trail for the system.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from .enums import DecisionState, StrategyId, Symbol


class JournalEntry(BaseModel):
    """Persistent record of one pipeline cycle."""

    entry_id: str
    """UUID. Unique per cycle."""
    symbol: Symbol
    cycle_timestamp: datetime
    decision_state: DecisionState
    contributing_strategies: list[StrategyId]
    composite_score: Decimal
    rationale: str
    """Full rationale string from DecisionEngine."""
    fill_price: Decimal | None = None
    """Populated if execution occurred (ENTER + approved + filled)."""
    fill_qty: Decimal | None = None
    fee_usdt: Decimal | None = None
    is_paper: bool = True
    raw_signal_scores: dict[str, float] = {}
    """strategy_id → score float for quick analysis."""
