"""
ExitEvaluator

Determines whether an open position should be exited this cycle.

Three exit categories (evaluated in priority order):
  1. Stop-loss       — price ≤ avg_entry × (1 − stop_loss_pct)
  2. Take-profit     — price ≥ avg_entry × (1 + take_profit_pct)
  3. Signal deterioration — signal_score ≤ exit_score_threshold

Returns the first matching ExitDecision, or None if no exit condition is met.

The score passed in is the composite score of the best signal this cycle.
When no signal is selected (AggregatedSignal.selected is None) the caller
should pass Decimal("0"), which is ≤ the default threshold of 0.30 — so a
complete signal collapse while a position is open will correctly trigger a
signal-deterioration exit.
"""

from dataclasses import dataclass
from decimal import Decimal

from ..config import BITConfig
from ..domain.enums import Symbol
from ..domain.market import Position


@dataclass
class ExitDecision:
    """Instruction to close an open position this cycle."""

    symbol: Symbol
    reason: str
    """One of: 'stop_loss', 'take_profit', 'signal_deterioration'."""
    current_price: Decimal
    position_qty: Decimal


class ExitEvaluator:
    """
    Stateless evaluator: call evaluate() each pipeline cycle for each open
    position.

    Instantiate once at startup with the shared BITConfig.
    """

    def __init__(self, config: BITConfig) -> None:
        self._config = config

    def evaluate(
        self,
        position: Position,
        current_price: Decimal,
        signal_score: Decimal,
    ) -> ExitDecision | None:
        """
        Evaluate exit conditions for a single open position.

        Args:
            position:      The open Position (contains avg_entry_price and qty).
            current_price: The current market price for this symbol.
            signal_score:  Composite score from SignalEngine this cycle
                           (0 when no signal was selected).

        Returns:
            ExitDecision if any exit condition is met, None otherwise.
        """
        avg_entry = position.avg_entry_price
        qty = position.qty
        symbol = position.symbol

        # Priority 1: Stop-loss
        stop_loss_price = avg_entry * (Decimal("1") - self._config.stop_loss_pct)
        if current_price <= stop_loss_price:
            return ExitDecision(
                symbol=symbol,
                reason="stop_loss",
                current_price=current_price,
                position_qty=qty,
            )

        # Priority 2: Take-profit
        take_profit_price = avg_entry * (Decimal("1") + self._config.take_profit_pct)
        if current_price >= take_profit_price:
            return ExitDecision(
                symbol=symbol,
                reason="take_profit",
                current_price=current_price,
                position_qty=qty,
            )

        # Priority 3: Signal deterioration
        if signal_score <= self._config.exit_score_threshold:
            return ExitDecision(
                symbol=symbol,
                reason="signal_deterioration",
                current_price=current_price,
                position_qty=qty,
            )

        return None
