"""
SignalEngine

Responsibility: Run all registered strategies against a FeatureSet, then
select the best viable candidate for DecisionEngine to act on.

Selection model:
  1. Fan out — call evaluate() on every registered strategy.
  2. Filter  — keep only signals with score > 0 (at least one condition met).
  3. Rank    — sort valid signals by score descending.
  4. Tie-break — when scores are equal, the strategy that appears earlier in
                 _STRATEGY_PRIORITY wins.  This is explicit and deterministic.
  5. Select  — take the top-ranked signal as the candidate.
  6. Package — return AggregatedSignal with ALL evaluations preserved alongside
               the selection decision and a human-readable rationale.

If no strategy scores above 0 the selected field is None and DecisionEngine
will REJECT immediately, but all zero-score evaluations are still available
for journal inspection.

Adding a new strategy: create a module in bit/strategies/, implement
BaseStrategy, add its StrategyId to _STRATEGY_PRIORITY (position determines
tie-break preference), and register the instance with SignalEngine at startup.
"""

from decimal import Decimal

from ..domain.enums import StrategyId
from ..domain.features import FeatureSet
from ..domain.signals import AggregatedSignal, Signal
from ..strategies.base import BaseStrategy

# Deterministic tie-break order. When two signals share an equal score the
# strategy whose StrategyId appears earlier in this list is preferred.
# Update this list whenever a new strategy is added.
_STRATEGY_PRIORITY: list[StrategyId] = [
    StrategyId.TREND_CONTINUATION,
    StrategyId.BREAKOUT_CONFIRMATION,
]

_ZERO = Decimal("0")


class SignalEngine:
    """
    Orchestrates strategy evaluation and candidate selection for one symbol.

    Strategies are injected at construction time (dependency injection).
    SignalEngine itself has no knowledge of market conditions or thresholds —
    that logic lives in the strategies and DecisionEngine respectively.
    """

    def __init__(self, strategies: list[BaseStrategy]) -> None:
        if not strategies:
            raise ValueError("SignalEngine requires at least one strategy.")
        self._strategies = list(strategies)

    def evaluate(self, features: FeatureSet) -> AggregatedSignal:
        """
        Evaluate all registered strategies and select the best viable candidate.

        A signal is "viable" if its score > 0, meaning the strategy itself
        judged that at least one condition was met.  Strategies that scored
        exactly 0 are preserved in all_signals for observability but excluded
        from selection.

        Returns:
            AggregatedSignal with:
            - all_signals: every strategy's full evaluation
            - selected: the highest-scoring viable signal, or None
            - candidate_count: number of strategies with score > 0
            - rationale: plain-English summary of the selection decision
        """
        all_signals: list[Signal] = [
            strategy.evaluate(features) for strategy in self._strategies
        ]

        valid = [s for s in all_signals if s.score > _ZERO]
        candidate_count = len(valid)

        selected: Signal | None = None
        if valid:
            # Primary sort: score descending.  Tie-break: priority index ascending.
            selected = max(
                valid,
                key=lambda s: (s.score, -self._priority_index(s.strategy_id)),
            )

        return AggregatedSignal(
            symbol=features.symbol,
            timestamp=features.timestamp,
            all_signals=all_signals,
            selected=selected,
            candidate_count=candidate_count,
            rationale=self._build_rationale(all_signals, selected),
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _priority_index(self, strategy_id: StrategyId) -> int:
        """
        Return the tie-break priority for a strategy (lower = higher priority).

        Strategies not listed in _STRATEGY_PRIORITY are ranked after all known
        strategies so they never silently displace a known one.
        """
        try:
            return _STRATEGY_PRIORITY.index(strategy_id)
        except ValueError:
            return len(_STRATEGY_PRIORITY)

    def _build_rationale(
        self,
        all_signals: list[Signal],
        selected: Signal | None,
    ) -> str:
        if selected is None:
            summary = " | ".join(
                f"{s.strategy_id}={float(s.score):.2f}" for s in all_signals
            )
            return f"No viable setup — all strategies scored 0 ({summary})"

        parts = [f"Selected {selected.strategy_id} (score={float(selected.score):.2f})"]
        for s in all_signals:
            if s.strategy_id == selected.strategy_id:
                continue
            if s.score > _ZERO:
                parts.append(
                    f"{s.strategy_id} also viable (score={float(s.score):.2f})"
                )
            else:
                parts.append(f"{s.strategy_id} no signal (score=0.00)")
        return " | ".join(parts)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def strategy_ids(self) -> list[str]:
        """Names of all registered strategies, for logging."""
        return [s.strategy_id for s in self._strategies]
