"""
Tests for TrendContinuationStrategy.

Test organisation:
  - Fully bullish case (all conditions met, score = 1.0)
  - Per-condition failures (each condition independently fails)
  - Missing feature data (None inputs handled gracefully)
  - Score arithmetic (score equals exact weighted sum)
  - Rationale transparency (all conditions visible, all labels present)
  - Metadata completeness (conditions dict, features snapshot)
  - Determinism (same input → same output)
  - Context notes (H1 RSI weak, euphoric return — do not affect score)
  - Boundary values (exact thresholds pass/fail correctly)
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.enums import StrategyId, Symbol, Timeframe
from bit.domain.features import FeatureSet, KlineFeatures
from bit.strategies.trend_continuation import TrendContinuationStrategy

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_SYM = Symbol.BTCUSDT
_STRATEGY = TrendContinuationStrategy()

# Shorthand for the threshold constants — tests reference these to avoid
# duplicating magic numbers and to break if a threshold changes.
_W_H1 = TrendContinuationStrategy._W_H1_TREND
_W_M15_STR = TrendContinuationStrategy._W_M15_STRUCTURE
_W_M15_MOM = TrendContinuationStrategy._W_M15_MOMENTUM
_W_M15_LOC = TrendContinuationStrategy._W_M15_PRICE_LOC
_W_M15_VOL = TrendContinuationStrategy._W_M15_VOLUME
_RSI_MIN = TrendContinuationStrategy._RSI_M15_MIN
_RSI_OB = TrendContinuationStrategy._RSI_M15_OB_MAX
_RSI_H1_WEAK = TrendContinuationStrategy._RSI_H1_WEAK
_DIST_MIN = TrendContinuationStrategy._EMA_DIST_MIN_PCT
_DIST_MAX = TrendContinuationStrategy._EMA_DIST_MAX_PCT
_MIN_VOL = TrendContinuationStrategy._MIN_RELATIVE_VOL
_EUPHORIA = TrendContinuationStrategy._EUPHORIA_PCT


# ── Fixture helpers ───────────────────────────────────────────────────────────


def _kf(timeframe: Timeframe, **kwargs) -> KlineFeatures:
    """Construct KlineFeatures with given fields; all others default to None."""
    return KlineFeatures(timeframe=timeframe, **kwargs)


def _bullish_h1() -> KlineFeatures:
    """H1 features where C1 (EMA alignment) is clearly met."""
    return _kf(
        Timeframe.H1,
        ema_fast=Decimal("61000"),
        ema_slow=Decimal("60000"),
        rsi=Decimal("58"),
        atr=Decimal("800"),
    )


def _bullish_m15() -> KlineFeatures:
    """M15 features where C2-C5 are all clearly met."""
    return _kf(
        Timeframe.M15,
        ema_fast=Decimal("61050"),
        ema_slow=Decimal("60800"),
        rsi=Decimal("60"),
        ema_distance_pct=Decimal("1.5"),
        relative_volume=Decimal("1.3"),
        recent_return_pct=Decimal("0.8"),
    )


def _bullish_m5() -> KlineFeatures:
    """M5 features with context data (no scoring impact)."""
    return _kf(Timeframe.M5, relative_volume=Decimal("1.1"))


def _fully_bullish() -> FeatureSet:
    """FeatureSet where all five trend continuation conditions are met."""
    return FeatureSet(
        symbol=_SYM,
        timestamp=_NOW,
        h1=_bullish_h1(),
        m15=_bullish_m15(),
        m5=_bullish_m5(),
        last_price=Decimal("61100"),
    )


def _empty_features() -> FeatureSet:
    """FeatureSet with all feature fields set to None (zero history)."""
    return FeatureSet(
        symbol=_SYM,
        timestamp=_NOW,
        h1=_kf(Timeframe.H1),
        m15=_kf(Timeframe.M15),
        m5=_kf(Timeframe.M5),
        last_price=Decimal("60000"),
    )


# ── Fully bullish case ────────────────────────────────────────────────────────


class TestFullyBullishCase:
    def test_all_conditions_met_score_is_one(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert signal.score == Decimal("1")

    def test_all_conditions_true_in_metadata(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        cond = signal.metadata["conditions"]
        assert cond["c1_h1_trend"] is True
        assert cond["c2_m15_structure"] is True
        assert cond["c3_m15_momentum"] is True
        assert cond["c4_m15_price_location"] is True
        assert cond["c5_m15_volume"] is True

    def test_strategy_id_correct(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert signal.strategy_id == StrategyId.TREND_CONTINUATION

    def test_symbol_passed_through(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert signal.symbol == _SYM

    def test_timestamp_passed_through(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert signal.timestamp == _NOW

    def test_rationale_not_empty(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert len(signal.rationale) > 0


# ── Empty / missing data ──────────────────────────────────────────────────────


class TestMissingFeatureData:
    def test_all_none_returns_zero_score(self):
        signal = _STRATEGY.evaluate(_empty_features())
        assert signal.score == Decimal("0")

    def test_all_none_all_conditions_false(self):
        signal = _STRATEGY.evaluate(_empty_features())
        cond = signal.metadata["conditions"]
        for key in cond:
            assert cond[key] is False, f"{key} should be False when features are missing"

    def test_h1_ema_missing_fails_c1(self):
        f = _fully_bullish()
        m = f.model_copy(update={"h1": _kf(Timeframe.H1)})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c1_h1_trend"] is False

    def test_h1_ema_missing_score_loses_h1_weight(self):
        f = _fully_bullish()
        m = f.model_copy(update={"h1": _kf(Timeframe.H1)})
        signal = _STRATEGY.evaluate(m)
        assert signal.score == Decimal("1") - _W_H1

    def test_m15_ema_missing_fails_c2(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_fast": None, "ema_slow": None})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c2_m15_structure"] is False

    def test_m15_rsi_missing_fails_c3(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": None})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c3_m15_momentum"] is False

    def test_m15_ema_dist_missing_fails_c4(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": None})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c4_m15_price_location"] is False

    def test_m15_relative_volume_missing_fails_c5(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"relative_volume": None})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c5_m15_volume"] is False

    def test_missing_conditions_noted_in_rationale(self):
        signal = _STRATEGY.evaluate(_empty_features())
        assert "MISS" in signal.rationale


# ── Weak volume ───────────────────────────────────────────────────────────────


class TestWeakVolume:
    def test_low_volume_fails_c5(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"relative_volume": Decimal("0.3")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c5_m15_volume"] is False

    def test_low_volume_score_loses_volume_weight(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"relative_volume": Decimal("0.3")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.score == Decimal("1") - _W_M15_VOL

    def test_volume_exactly_at_threshold_passes(self):
        """Boundary: relative_volume == MIN_RELATIVE_VOL must pass."""
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"relative_volume": _MIN_VOL})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c5_m15_volume"] is True

    def test_volume_just_below_threshold_fails(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"relative_volume": _MIN_VOL - Decimal("0.01")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c5_m15_volume"] is False

    def test_low_volume_mentioned_in_rationale(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"relative_volume": Decimal("0.3")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert "C5 FAIL" in signal.rationale


# ── Overextended price ────────────────────────────────────────────────────────


class TestOverextendedPrice:
    def test_price_too_extended_above_ema_fails_c4(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": Decimal("4.0")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c4_m15_price_location"] is False

    def test_price_extended_score_loses_price_loc_weight(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": Decimal("4.0")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.score == Decimal("1") - _W_M15_LOC

    def test_price_too_far_below_ema_fails_c4(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": Decimal("-3.0")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c4_m15_price_location"] is False

    def test_price_at_max_extension_passes(self):
        """Boundary: ema_distance_pct == EMA_DIST_MAX_PCT must pass."""
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": _DIST_MAX})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c4_m15_price_location"] is True

    def test_price_at_min_pullback_passes(self):
        """Boundary: ema_distance_pct == EMA_DIST_MIN_PCT must pass."""
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": _DIST_MIN})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c4_m15_price_location"] is True

    def test_price_just_over_max_fails(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": _DIST_MAX + Decimal("0.01")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c4_m15_price_location"] is False

    def test_price_just_below_min_fails(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_distance_pct": _DIST_MIN - Decimal("0.01")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c4_m15_price_location"] is False


# ── Conflicting timeframes ────────────────────────────────────────────────────


class TestConflictingTimeframes:
    def test_h1_bearish_fails_c1(self):
        f = _fully_bullish()
        h1 = f.h1.model_copy(update={
            "ema_fast": Decimal("59000"),
            "ema_slow": Decimal("60000"),  # fast < slow
        })
        m = f.model_copy(update={"h1": h1})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c1_h1_trend"] is False

    def test_h1_bearish_score_loses_h1_weight(self):
        f = _fully_bullish()
        h1 = f.h1.model_copy(update={"ema_fast": Decimal("59000"), "ema_slow": Decimal("60000")})
        m = f.model_copy(update={"h1": h1})
        signal = _STRATEGY.evaluate(m)
        assert signal.score == Decimal("1") - _W_H1

    def test_m15_bearish_fails_c2(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={
            "ema_fast": Decimal("59500"),
            "ema_slow": Decimal("60000"),  # fast < slow
        })
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c2_m15_structure"] is False

    def test_conflict_visible_in_rationale(self):
        """H1 bullish but M15 bearish — both labels appear and disagree."""
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"ema_fast": Decimal("59500"), "ema_slow": Decimal("60000")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert "C1 MET" in signal.rationale
        assert "C2 FAIL" in signal.rationale

    def test_h1_ema_equal_fails_c1(self):
        """EMA(9) == EMA(21) is not a bullish cross."""
        f = _fully_bullish()
        h1 = f.h1.model_copy(update={"ema_fast": Decimal("60000"), "ema_slow": Decimal("60000")})
        m = f.model_copy(update={"h1": h1})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c1_h1_trend"] is False


# ── RSI conditions ────────────────────────────────────────────────────────────


class TestRSIConditions:
    def test_rsi_below_min_fails_c3(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": Decimal("40")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c3_m15_momentum"] is False

    def test_rsi_above_ob_max_fails_c3(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": Decimal("80")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c3_m15_momentum"] is False

    def test_rsi_at_lower_boundary_passes(self):
        """Boundary: rsi == RSI_M15_MIN must pass."""
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": _RSI_MIN})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c3_m15_momentum"] is True

    def test_rsi_at_upper_boundary_passes(self):
        """Boundary: rsi == RSI_M15_OB_MAX must pass."""
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": _RSI_OB})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c3_m15_momentum"] is True

    def test_rsi_just_below_min_fails(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": _RSI_MIN - Decimal("0.1")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c3_m15_momentum"] is False

    def test_rsi_just_above_ob_max_fails(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": _RSI_OB + Decimal("0.1")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.metadata["conditions"]["c3_m15_momentum"] is False

    def test_overbought_noted_in_rationale(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": Decimal("80")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert "C3 FAIL" in signal.rationale
        assert "overbought" in signal.rationale.lower()

    def test_weak_rsi_noted_in_rationale(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"rsi": Decimal("40")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert "C3 FAIL" in signal.rationale
        assert "weak" in signal.rationale.lower()


# ── Score arithmetic ──────────────────────────────────────────────────────────


class TestScoreArithmetic:
    def test_score_equals_sum_of_met_condition_weights(self):
        """Only C1 and C3 are met — score must equal _W_H1 + _W_M15_MOM."""
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={
            # C2 fails: M15 EMA bearish
            "ema_fast": Decimal("59500"),
            "ema_slow": Decimal("60000"),
            # C3 still passes (rsi=60 from _bullish_m15 is preserved)
            # C4 fails: overextended
            "ema_distance_pct": Decimal("5.0"),
            # C5 fails: weak volume
            "relative_volume": Decimal("0.2"),
        })
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        expected = _W_H1 + _W_M15_MOM
        assert signal.score == expected

    def test_no_conditions_met_score_is_zero(self):
        signal = _STRATEGY.evaluate(_empty_features())
        assert signal.score == Decimal("0")

    def test_all_conditions_met_score_is_one(self):
        assert _STRATEGY.evaluate(_fully_bullish()).score == Decimal("1")

    def test_weights_sum_to_one(self):
        """Sanity: all five weights must sum to exactly 1.0."""
        total = _W_H1 + _W_M15_STR + _W_M15_MOM + _W_M15_LOC + _W_M15_VOL
        assert total == Decimal("1")

    def test_score_always_in_valid_range(self):
        for signal in [
            _STRATEGY.evaluate(_fully_bullish()),
            _STRATEGY.evaluate(_empty_features()),
        ]:
            assert Decimal("0") <= signal.score <= Decimal("1")


# ── Rationale transparency ────────────────────────────────────────────────────


class TestRationaleTransparency:
    def test_rationale_contains_all_condition_labels(self):
        """Every condition label C1-C5 must appear in the rationale."""
        signal = _STRATEGY.evaluate(_fully_bullish())
        for label in ("C1", "C2", "C3", "C4", "C5"):
            assert label in signal.rationale, f"Label {label} missing from rationale"

    def test_failed_conditions_use_fail_or_miss_prefix(self):
        signal = _STRATEGY.evaluate(_empty_features())
        assert "FAIL" in signal.rationale or "MISS" in signal.rationale

    def test_met_conditions_use_met_prefix(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert "MET" in signal.rationale


# ── Metadata ──────────────────────────────────────────────────────────────────


class TestMetadata:
    def test_conditions_dict_present(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert "conditions" in signal.metadata

    def test_conditions_dict_has_all_five_keys(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        cond = signal.metadata["conditions"]
        expected = {
            "c1_h1_trend", "c2_m15_structure", "c3_m15_momentum",
            "c4_m15_price_location", "c5_m15_volume",
        }
        assert expected == set(cond.keys())

    def test_features_snapshot_present(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert "features" in signal.metadata

    def test_features_snapshot_populated_on_bullish_case(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        snap = signal.metadata["features"]
        assert len(snap) > 0

    def test_features_snapshot_empty_when_no_data(self):
        signal = _STRATEGY.evaluate(_empty_features())
        # No features can be snapshotted if all inputs are None.
        assert len(signal.metadata["features"]) == 0

    def test_feature_values_are_strings(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        for v in signal.metadata["features"].values():
            assert isinstance(v, str), f"Feature value should be a string, got {type(v)}"

    def test_condition_values_are_bools(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        for v in signal.metadata["conditions"].values():
            assert isinstance(v, bool), f"Condition value should be bool, got {type(v)}"


# ── Context notes (no score impact) ──────────────────────────────────────────


class TestContextNotes:
    def test_h1_rsi_weak_adds_note_to_rationale(self):
        f = _fully_bullish()
        h1 = f.h1.model_copy(update={"rsi": _RSI_H1_WEAK - Decimal("1")})
        m = f.model_copy(update={"h1": h1})
        signal = _STRATEGY.evaluate(m)
        assert "NOTE" in signal.rationale
        assert "H1 RSI" in signal.rationale

    def test_h1_rsi_weak_does_not_affect_score(self):
        f = _fully_bullish()
        h1 = f.h1.model_copy(update={"rsi": _RSI_H1_WEAK - Decimal("1")})
        m = f.model_copy(update={"h1": h1})
        signal = _STRATEGY.evaluate(m)
        assert signal.score == Decimal("1")

    def test_h1_rsi_at_weak_threshold_adds_note(self):
        """Boundary: H1 RSI exactly at RSI_H1_WEAK does NOT trigger note."""
        f = _fully_bullish()
        h1 = f.h1.model_copy(update={"rsi": _RSI_H1_WEAK})
        m = f.model_copy(update={"h1": h1})
        signal = _STRATEGY.evaluate(m)
        # At exactly the threshold, note is NOT added (< not <=)
        note_present = "NOTE" in signal.rationale and "H1 RSI" in signal.rationale
        assert not note_present

    def test_euphoric_return_adds_note(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"recent_return_pct": _EUPHORIA + Decimal("1")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert "NOTE" in signal.rationale
        assert "euphoric" in signal.rationale.lower()

    def test_euphoric_return_does_not_affect_score(self):
        f = _fully_bullish()
        m15 = f.m15.model_copy(update={"recent_return_pct": _EUPHORIA + Decimal("1")})
        m = f.model_copy(update={"m15": m15})
        signal = _STRATEGY.evaluate(m)
        assert signal.score == Decimal("1")

    def test_m5_relative_volume_captured_in_snapshot(self):
        signal = _STRATEGY.evaluate(_fully_bullish())
        assert "m5_relative_volume" in signal.metadata["features"]


# ── Determinism ───────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_same_score(self):
        features = _fully_bullish()
        s1 = _STRATEGY.evaluate(features)
        s2 = _STRATEGY.evaluate(features)
        assert s1.score == s2.score

    def test_same_input_same_rationale(self):
        features = _fully_bullish()
        s1 = _STRATEGY.evaluate(features)
        s2 = _STRATEGY.evaluate(features)
        assert s1.rationale == s2.rationale

    def test_same_input_same_metadata(self):
        features = _fully_bullish()
        s1 = _STRATEGY.evaluate(features)
        s2 = _STRATEGY.evaluate(features)
        assert s1.metadata == s2.metadata
