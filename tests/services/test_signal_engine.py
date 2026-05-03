"""
SignalEngine unit tests.

Strategies are replaced with controllable stubs so this test file exercises
only SignalEngine's orchestration and selection logic — not strategy internals.

Test organisation:
  TestNoValidSignals       — all strategies score 0; no candidate selected
  TestSingleCandidate      — only one strategy fires (each variant)
  TestTwoValidSignals      — both fire; higher score wins; tie-break order
  TestAllSignalsPreserved  — all evaluations kept regardless of validity
  TestOutputFields         — symbol, timestamp, candidate_count propagated
  TestRationale            — rationale format for each outcome
  TestDeterminism          — same input → same output
  TestSingleStrategy       — engine works with only one registered strategy
  TestConstructor          — rejects empty strategy list
  TestStrategyIdsProperty  — property reflects registered strategies
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.enums import StrategyId, Symbol, Timeframe
from bit.domain.features import FeatureSet, KlineFeatures
from bit.domain.signals import AggregatedSignal, Signal
from bit.services.signal_engine import SignalEngine


# ── Fixtures ─────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_SYMBOL = Symbol.BTCUSDT


def _empty_kline(tf: Timeframe) -> KlineFeatures:
    return KlineFeatures(timeframe=tf)


def _minimal_features(symbol: Symbol = _SYMBOL) -> FeatureSet:
    return FeatureSet(
        symbol=symbol,
        timestamp=_NOW,
        m5=_empty_kline(Timeframe.M5),
        m15=_empty_kline(Timeframe.M15),
        h1=_empty_kline(Timeframe.H1),
        last_price=Decimal("60000"),
    )


class _FixedScoreStrategy:
    """Test stub: always returns a predetermined score."""

    def __init__(self, strategy_id: StrategyId, score: Decimal) -> None:
        self.strategy_id = strategy_id
        self._score = score

    def evaluate(self, features: FeatureSet) -> Signal:
        return Signal(
            strategy_id=self.strategy_id,
            symbol=features.symbol,
            timestamp=features.timestamp,
            score=self._score,
            rationale=f"stub:{float(self._score):.2f}",
        )


def _trend(score: float) -> _FixedScoreStrategy:
    return _FixedScoreStrategy(StrategyId.TREND_CONTINUATION, Decimal(str(score)))


def _breakout(score: float) -> _FixedScoreStrategy:
    return _FixedScoreStrategy(StrategyId.BREAKOUT_CONFIRMATION, Decimal(str(score)))


def _engine(*strategies) -> SignalEngine:
    return SignalEngine(list(strategies))


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestNoValidSignals:
    """All strategies score 0 — nothing to select."""

    def _run(self) -> AggregatedSignal:
        engine = _engine(_trend(0.0), _breakout(0.0))
        return engine.evaluate(_minimal_features())

    def test_selected_is_none(self):
        assert self._run().selected is None

    def test_candidate_count_is_zero(self):
        assert self._run().candidate_count == 0

    def test_all_signals_still_present(self):
        result = self._run()
        assert len(result.all_signals) == 2

    def test_rationale_mentions_no_viable_setup(self):
        assert "No viable setup" in self._run().rationale

    def test_rationale_includes_strategy_scores(self):
        rationale = self._run().rationale
        assert "trend_continuation" in rationale
        assert "breakout_confirmation" in rationale


class TestSingleCandidate:
    """Exactly one strategy fires — that one is always selected."""

    def test_only_trend_valid_selected(self):
        engine = _engine(_trend(0.80), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert result.selected is not None
        assert result.selected.strategy_id == StrategyId.TREND_CONTINUATION

    def test_only_trend_valid_score(self):
        engine = _engine(_trend(0.80), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert result.selected.score == Decimal("0.80")

    def test_only_trend_valid_candidate_count(self):
        engine = _engine(_trend(0.80), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert result.candidate_count == 1

    def test_only_breakout_valid_selected(self):
        engine = _engine(_trend(0.0), _breakout(0.70))
        result = engine.evaluate(_minimal_features())
        assert result.selected is not None
        assert result.selected.strategy_id == StrategyId.BREAKOUT_CONFIRMATION

    def test_only_breakout_valid_score(self):
        engine = _engine(_trend(0.0), _breakout(0.70))
        result = engine.evaluate(_minimal_features())
        assert result.selected.score == Decimal("0.70")

    def test_only_breakout_valid_candidate_count(self):
        engine = _engine(_trend(0.0), _breakout(0.70))
        result = engine.evaluate(_minimal_features())
        assert result.candidate_count == 1


class TestTwoValidSignals:
    """Both strategies fire — the higher score wins."""

    def test_trend_higher_selects_trend(self):
        engine = _engine(_trend(0.80), _breakout(0.60))
        result = engine.evaluate(_minimal_features())
        assert result.selected.strategy_id == StrategyId.TREND_CONTINUATION

    def test_trend_higher_score_correct(self):
        engine = _engine(_trend(0.80), _breakout(0.60))
        result = engine.evaluate(_minimal_features())
        assert result.selected.score == Decimal("0.80")

    def test_breakout_higher_selects_breakout(self):
        engine = _engine(_trend(0.55), _breakout(0.90))
        result = engine.evaluate(_minimal_features())
        assert result.selected.strategy_id == StrategyId.BREAKOUT_CONFIRMATION

    def test_breakout_higher_score_correct(self):
        engine = _engine(_trend(0.55), _breakout(0.90))
        result = engine.evaluate(_minimal_features())
        assert result.selected.score == Decimal("0.90")

    def test_candidate_count_two(self):
        engine = _engine(_trend(0.70), _breakout(0.65))
        result = engine.evaluate(_minimal_features())
        assert result.candidate_count == 2

    def test_tie_break_trend_wins(self):
        """Equal scores: TREND_CONTINUATION has higher priority (index 0)."""
        engine = _engine(_trend(0.70), _breakout(0.70))
        result = engine.evaluate(_minimal_features())
        assert result.selected.strategy_id == StrategyId.TREND_CONTINUATION

    def test_tie_break_breakout_wins_when_trend_absent(self):
        """Only breakout registered — it must win regardless."""
        engine = SignalEngine([_breakout(0.70)])
        result = engine.evaluate(_minimal_features())
        assert result.selected.strategy_id == StrategyId.BREAKOUT_CONFIRMATION

    def test_tie_break_is_stable_regardless_of_registration_order(self):
        """Priority is determined by _STRATEGY_PRIORITY, not insertion order."""
        engine_ab = _engine(_trend(0.60), _breakout(0.60))
        engine_ba = _engine(_breakout(0.60), _trend(0.60))
        result_ab = engine_ab.evaluate(_minimal_features())
        result_ba = engine_ba.evaluate(_minimal_features())
        assert result_ab.selected.strategy_id == result_ba.selected.strategy_id


class TestAllSignalsPreserved:
    """Every strategy evaluation appears in all_signals, regardless of score."""

    def test_all_strategies_represented(self):
        engine = _engine(_trend(0.0), _breakout(0.80))
        result = engine.evaluate(_minimal_features())
        ids = {s.strategy_id for s in result.all_signals}
        assert StrategyId.TREND_CONTINUATION in ids
        assert StrategyId.BREAKOUT_CONFIRMATION in ids

    def test_zero_score_signal_in_all_signals(self):
        engine = _engine(_trend(0.0), _breakout(0.80))
        result = engine.evaluate(_minimal_features())
        trend_sig = next(s for s in result.all_signals if s.strategy_id == StrategyId.TREND_CONTINUATION)
        assert trend_sig.score == Decimal("0")

    def test_all_signals_count_matches_registered(self):
        engine = _engine(_trend(0.50), _breakout(0.70))
        result = engine.evaluate(_minimal_features())
        assert len(result.all_signals) == 2

    def test_signal_rationale_preserved(self):
        engine = _engine(_trend(0.80), _breakout(0.60))
        result = engine.evaluate(_minimal_features())
        trend_sig = next(s for s in result.all_signals if s.strategy_id == StrategyId.TREND_CONTINUATION)
        assert "stub" in trend_sig.rationale  # stub strategy rationale intact


class TestOutputFields:
    """symbol, timestamp, and candidate_count are correctly propagated."""

    def test_symbol_propagated(self):
        engine = _engine(_trend(0.70))
        result = engine.evaluate(_minimal_features(Symbol.ETHUSDT))
        assert result.symbol == Symbol.ETHUSDT

    def test_timestamp_from_features(self):
        engine = _engine(_trend(0.70))
        result = engine.evaluate(_minimal_features())
        assert result.timestamp == _NOW

    def test_candidate_count_reflects_valid_only(self):
        engine = _engine(_trend(0.60), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert result.candidate_count == 1

    def test_candidate_count_zero_score_boundary(self):
        """Score of exactly 0 does not count as a candidate."""
        engine = _engine(_trend(0.0), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert result.candidate_count == 0

    def test_selected_signal_matches_best_in_all_signals(self):
        engine = _engine(_trend(0.80), _breakout(0.60))
        result = engine.evaluate(_minimal_features())
        # The selected signal should be the same object/value as the
        # corresponding entry in all_signals.
        matching = next(
            s for s in result.all_signals
            if s.strategy_id == result.selected.strategy_id
        )
        assert result.selected.score == matching.score


class TestRationale:
    """Rationale format for each selection outcome."""

    def test_no_valid_rationale_prefix(self):
        engine = _engine(_trend(0.0), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert result.rationale.startswith("No viable setup")

    def test_selected_rationale_prefix(self):
        engine = _engine(_trend(0.80), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert result.rationale.startswith("Selected trend_continuation")

    def test_rationale_includes_selected_score(self):
        engine = _engine(_trend(0.80), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert "0.80" in result.rationale

    def test_non_selected_valid_labeled_also_viable(self):
        engine = _engine(_trend(0.80), _breakout(0.60))
        result = engine.evaluate(_minimal_features())
        assert "also viable" in result.rationale
        assert "breakout_confirmation" in result.rationale

    def test_zero_score_strategy_labeled_no_signal(self):
        engine = _engine(_trend(0.80), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert "no signal" in result.rationale

    def test_pipe_separator_present(self):
        engine = _engine(_trend(0.80), _breakout(0.0))
        result = engine.evaluate(_minimal_features())
        assert " | " in result.rationale


class TestDeterminism:
    """Same input always produces identical output."""

    def test_same_features_same_selected(self):
        engine = _engine(_trend(0.70), _breakout(0.60))
        fs = _minimal_features()
        r1 = engine.evaluate(fs)
        r2 = engine.evaluate(fs)
        assert r1.selected.strategy_id == r2.selected.strategy_id
        assert r1.selected.score == r2.selected.score

    def test_same_features_same_rationale(self):
        engine = _engine(_trend(0.70), _breakout(0.60))
        fs = _minimal_features()
        r1 = engine.evaluate(fs)
        r2 = engine.evaluate(fs)
        assert r1.rationale == r2.rationale

    def test_two_instances_same_result(self):
        e1 = _engine(_trend(0.80), _breakout(0.65))
        e2 = _engine(_trend(0.80), _breakout(0.65))
        fs = _minimal_features()
        assert e1.evaluate(fs).selected.score == e2.evaluate(fs).selected.score


class TestSingleStrategy:
    """Engine works correctly when only one strategy is registered."""

    def test_single_strategy_valid_selected(self):
        engine = SignalEngine([_trend(0.75)])
        result = engine.evaluate(_minimal_features())
        assert result.selected is not None
        assert result.selected.strategy_id == StrategyId.TREND_CONTINUATION

    def test_single_strategy_zero_selected_none(self):
        engine = SignalEngine([_trend(0.0)])
        result = engine.evaluate(_minimal_features())
        assert result.selected is None

    def test_single_strategy_all_signals_has_one_entry(self):
        engine = SignalEngine([_trend(0.60)])
        result = engine.evaluate(_minimal_features())
        assert len(result.all_signals) == 1


class TestConstructor:
    def test_empty_strategy_list_raises(self):
        with pytest.raises(ValueError, match="at least one strategy"):
            SignalEngine([])


class TestStrategyIdsProperty:
    def test_returns_all_registered_ids(self):
        engine = _engine(_trend(0.0), _breakout(0.0))
        ids = engine.strategy_ids
        assert StrategyId.TREND_CONTINUATION in ids
        assert StrategyId.BREAKOUT_CONFIRMATION in ids

    def test_count_matches_registered(self):
        engine = _engine(_trend(0.0), _breakout(0.0))
        assert len(engine.strategy_ids) == 2
