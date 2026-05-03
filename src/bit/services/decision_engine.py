"""
DecisionEngine

Responsibility: Translate the SignalEngine's selected candidate into a
ENTER / MONITOR / REJECT decision for one symbol.

v1 scoring:
  composite_score = selected_signal.score
  (the best-ranked viable strategy is used directly — no averaging)

If SignalEngine found no viable setup (selected is None) the engine emits
REJECT immediately without applying thresholds.

All strategy evaluations from AggregatedSignal are threaded through into the
rationale so the journal has a complete picture of every cycle.

This service is fully deterministic — no I/O, no state, unit-testable.
"""

from datetime import datetime, timezone
from decimal import Decimal

from ..config import BITConfig
from ..domain.decisions import Decision
from ..domain.enums import DecisionState
from ..domain.signals import AggregatedSignal


class DecisionEngine:
    def __init__(self, config: BITConfig) -> None:
        self._config = config

    def decide(self, agg: AggregatedSignal) -> Decision:
        """
        Convert an AggregatedSignal into a Decision for one symbol.

        If no strategy produced a viable signal (agg.selected is None) the
        decision is always REJECT with composite_score 0.  Otherwise the
        composite_score equals the selected signal's score and thresholds
        from BITConfig determine the state.

        All strategy IDs are listed in contributing_strategies regardless of
        whether they scored above zero, enabling full journal traceability.
        """
        timestamp = datetime.now(tz=timezone.utc)
        all_ids = [s.strategy_id for s in agg.all_signals]

        # No viable setup — reject immediately.
        if agg.selected is None:
            return Decision(
                symbol=agg.symbol,
                timestamp=timestamp,
                state=DecisionState.REJECT,
                composite_score=Decimal("0"),
                contributing_strategies=all_ids,
                rationale=f"REJECT: {agg.rationale}",
            )

        composite = agg.selected.score

        # Full evaluation detail for the journal rationale.
        evaluation_detail = "; ".join(
            f"{s.strategy_id}={float(s.score):.2f}: {s.rationale}"
            for s in agg.all_signals
        )
        rationale = f"{agg.rationale} | {evaluation_detail}"

        if composite >= self._config.enter_threshold:
            state = DecisionState.ENTER
        elif composite >= self._config.monitor_threshold:
            state = DecisionState.MONITOR
        else:
            state = DecisionState.REJECT

        return Decision(
            symbol=agg.symbol,
            timestamp=timestamp,
            state=state,
            composite_score=composite,
            contributing_strategies=all_ids,
            rationale=rationale,
            suggested_entry_price=None,
        )
