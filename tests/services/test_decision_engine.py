"""
DecisionEngine tests.

The DecisionEngine is fully deterministic — no I/O, no state.
Tests verify threshold logic, composite scoring, and the no-candidate path.

Since DecisionEngine v1 now accepts AggregatedSignal (from SignalEngine) and
uses selected_signal.score as the composite, these tests build AggregatedSignal
fixtures directly rather than raw Signal lists.

composite_score = selected_signal.score  (not an average in v1)
If selected is None → always REJECT with composite_score = 0.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.enums import DecisionState, StrategyId, Symbol
from bit.domain.signals import AggregatedSignal, Signal
from bit.services.decision_engine import DecisionEngine


_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _signal(
    score: float,
    strategy_id: StrategyId = StrategyId.TREND_CONTINUATION,
    symbol: Symbol = Symbol.BTCUSDT,
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        timestamp=_NOW,
        score=Decimal(str(score)),
        rationale=f"test score={score}",
    )


def _agg(
    *,
    selected_score: float | None,
    symbol: Symbol = Symbol.BTCUSDT,
    selected_id: StrategyId = StrategyId.TREND_CONTINUATION,
    all_scores: dict[StrategyId, float] | None = None,
) -> AggregatedSignal:
    """
    Build an AggregatedSignal with minimal ceremony.

    selected_score=None  → no viable candidate (selected field is None).
    all_scores           → optional dict of all strategy scores for all_signals;
                           defaults to just the selected strategy (if any).
    """
    if all_scores is None:
        if selected_score is not None:
            all_scores = {selected_id: selected_score}
        else:
            all_scores = {StrategyId.TREND_CONTINUATION: 0.0}

    all_signals = [
        _signal(score, strategy_id=sid, symbol=symbol)
        for sid, score in all_scores.items()
    ]

    selected: Signal | None = None
    if selected_score is not None:
        selected = _signal(selected_score, strategy_id=selected_id, symbol=symbol)

    return AggregatedSignal(
        symbol=symbol,
        timestamp=_NOW,
        all_signals=all_signals,
        selected=selected,
        candidate_count=sum(1 for s in all_signals if s.score > 0),
        rationale=f"test agg: selected={'none' if selected is None else str(selected_score)}",
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestDecisionEngineThresholds:
    def test_enter_when_score_at_threshold(self, config):
        """Exactly at enter_threshold (0.65) → ENTER."""
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.65))
        assert decision.state == DecisionState.ENTER

    def test_enter_when_score_above_threshold(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.80))
        assert decision.state == DecisionState.ENTER

    def test_monitor_between_thresholds(self, config):
        """Score in [monitor_threshold, enter_threshold) → MONITOR."""
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.55))
        assert decision.state == DecisionState.MONITOR

    def test_monitor_exactly_at_monitor_threshold(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.40))
        assert decision.state == DecisionState.MONITOR

    def test_reject_below_monitor_threshold(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.20))
        assert decision.state == DecisionState.REJECT

    def test_reject_when_selected_is_none(self, config):
        """No viable candidate always produces REJECT."""
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=None))
        assert decision.state == DecisionState.REJECT


class TestDecisionEngineCompositeScore:
    def test_composite_equals_selected_score(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.80))
        assert decision.composite_score == Decimal("0.80")

    def test_composite_is_zero_when_no_candidate(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=None))
        assert decision.composite_score == Decimal("0")

    def test_composite_uses_selected_not_average(self, config):
        """When breakout scores higher, composite = breakout score (not average)."""
        engine = DecisionEngine(config)
        agg = _agg(
            selected_score=0.90,
            selected_id=StrategyId.BREAKOUT_CONFIRMATION,
            all_scores={
                StrategyId.TREND_CONTINUATION: 0.40,
                StrategyId.BREAKOUT_CONFIRMATION: 0.90,
            },
        )
        decision = engine.decide(agg)
        assert decision.composite_score == Decimal("0.90")


class TestDecisionEngineContributing:
    def test_all_strategy_ids_listed(self, config):
        engine = DecisionEngine(config)
        agg = _agg(
            selected_score=0.70,
            all_scores={
                StrategyId.TREND_CONTINUATION: 0.70,
                StrategyId.BREAKOUT_CONFIRMATION: 0.0,
            },
        )
        decision = engine.decide(agg)
        assert StrategyId.TREND_CONTINUATION in decision.contributing_strategies
        assert StrategyId.BREAKOUT_CONFIRMATION in decision.contributing_strategies

    def test_contributing_includes_zero_score_strategies(self, config):
        """Even strategies that scored 0 appear in contributing_strategies."""
        engine = DecisionEngine(config)
        agg = _agg(
            selected_score=0.80,
            all_scores={
                StrategyId.TREND_CONTINUATION: 0.80,
                StrategyId.BREAKOUT_CONFIRMATION: 0.0,
            },
        )
        decision = engine.decide(agg)
        assert StrategyId.BREAKOUT_CONFIRMATION in decision.contributing_strategies

    def test_symbol_propagated(self, config):
        engine = DecisionEngine(config)
        agg = _agg(selected_score=0.70, symbol=Symbol.ETHUSDT)
        decision = engine.decide(agg)
        assert decision.symbol == Symbol.ETHUSDT


class TestDecisionEngineRationale:
    def test_rationale_non_empty(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.70))
        assert decision.rationale

    def test_rationale_includes_strategy_id(self, config):
        engine = DecisionEngine(config)
        agg = _agg(selected_score=0.70)
        decision = engine.decide(agg)
        assert StrategyId.TREND_CONTINUATION in decision.rationale

    def test_no_candidate_rationale_has_reject_label(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=None))
        assert "REJECT" in decision.rationale

    def test_rationale_contains_score(self, config):
        engine = DecisionEngine(config)
        decision = engine.decide(_agg(selected_score=0.75))
        assert "0.75" in decision.rationale
