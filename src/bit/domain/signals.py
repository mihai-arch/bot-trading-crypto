"""
Signal models — output from individual strategies and the SignalEngine aggregate.

Signal        — per-strategy evaluation result (produced by each BaseStrategy).
AggregatedSignal — SignalEngine output: all per-strategy evaluations plus the
                   selected best candidate for the current cycle.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from .enums import Symbol, StrategyId


class Signal(BaseModel):
    """Raw output from one strategy's evaluation of a FeatureSet."""

    strategy_id: StrategyId
    symbol: Symbol
    timestamp: datetime
    score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    """Normalized confidence score. 0 = no signal, 1 = maximum confidence."""
    rationale: str
    """Human-readable explanation of which conditions were met and their weights."""
    metadata: dict = Field(default_factory=dict)
    """Optional per-strategy debug data for inspection."""


class AggregatedSignal(BaseModel):
    """
    Output from SignalEngine for one symbol at one evaluation moment.

    Contains every strategy's evaluation (for full observability) plus the
    selected best candidate that DecisionEngine should act on.

    A signal is considered "valid" (a candidate) if its score > 0, meaning
    at least one strategy condition was met. A score of exactly 0 indicates
    that no conditions fired — the strategy itself considers this a non-event.

    Selection rule: highest score among candidates wins. On a score tie the
    strategy earlier in SignalEngine's priority order takes precedence.
    """

    symbol: Symbol
    timestamp: datetime
    all_signals: list[Signal]
    """All strategy evaluations — always populated, regardless of individual scores."""
    selected: Signal | None
    """Best valid signal (score > 0). None when every strategy scored 0."""
    candidate_count: int
    """Number of strategies that produced a viable signal (score > 0)."""
    rationale: str
    """Human-readable summary: which strategy was selected and why."""
