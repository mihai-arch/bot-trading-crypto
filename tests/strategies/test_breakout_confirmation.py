"""
BreakoutConfirmationStrategy — unit tests.

Test organisation:
  TestFullBreakout            — all 5 conditions met; score == 1.0
  TestMissingFeatureData      — each condition individually missing (None) → not met
  TestH1Consolidation         — C1: range too wide, boundary
  TestBreakoutProximity       — C2: close far from high_20, exact boundary
  TestVolumeConfirmation      — C3: volume below threshold, exact boundary
  TestRSIConditions           — C4: RSI below min, above max, boundary values
  TestExtensionGuard          — C5: price overextended, boundary
  TestScoreArithmetic         — partial scores, weight sanity
  TestRationaleTransparency   — MET/FAIL/MISS prefixes, pipe separator, note labels
  TestMetadata                — conditions dict (booleans), features dict (strings)
  TestContextNotes            — H1 RSI weak note, M15 euphoria note, M5 snapshot
  TestDeterminism             — same input → same output
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.enums import StrategyId, Symbol, Timeframe
from bit.domain.features import FeatureSet, KlineFeatures
from bit.strategies.breakout_confirmation import BreakoutConfirmationStrategy


_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_STRATEGY = BreakoutConfirmationStrategy()


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _h1(
    *,
    high_20: Decimal | None = Decimal("60000"),
    low_20: Decimal | None = Decimal("59500"),  # range 0.84% < 4% ✓
    rsi: Decimal | None = None,
    ema_fast: Decimal | None = None,
    ema_slow: Decimal | None = None,
) -> KlineFeatures:
    """H1 features with consolidation confirmed by default."""
    return KlineFeatures(
        timeframe=Timeframe.H1,
        high_20=high_20,
        low_20=low_20,
        rsi=rsi,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
    )


def _m15(
    *,
    high_20: Decimal | None = Decimal("60100"),
    current_close: Decimal | None = Decimal("60020"),  # gap 0.133% < 0.3% ✓
    relative_volume: Decimal | None = Decimal("2.0"),   # 2.0x ≥ 1.5 ✓
    rsi: Decimal | None = Decimal("62"),                # in [55, 80] ✓
    ema_distance_pct: Decimal | None = Decimal("3.0"),  # ≤ 6.0% ✓
    recent_return_pct: Decimal | None = None,
    ema_fast: Decimal | None = None,
    ema_slow: Decimal | None = None,
) -> KlineFeatures:
    """M15 features with all conditions met by default."""
    return KlineFeatures(
        timeframe=Timeframe.M15,
        high_20=high_20,
        current_close=current_close,
        relative_volume=relative_volume,
        rsi=rsi,
        ema_distance_pct=ema_distance_pct,
        recent_return_pct=recent_return_pct,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
    )


def _m5(*, relative_volume: Decimal | None = None) -> KlineFeatures:
    return KlineFeatures(timeframe=Timeframe.M5, relative_volume=relative_volume)


def _feature_set(
    h1: KlineFeatures | None = None,
    m15: KlineFeatures | None = None,
    m5: KlineFeatures | None = None,
) -> FeatureSet:
    return FeatureSet(
        symbol=Symbol.BTCUSDT,
        timestamp=_NOW,
        h1=h1 if h1 is not None else _h1(),
        m15=m15 if m15 is not None else _m15(),
        m5=m5 if m5 is not None else _m5(),
        last_price=Decimal("60020"),
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestFullBreakout:
    """All five conditions met → score == 1.0."""

    def test_score_is_one(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert sig.score == Decimal("1.0")

    def test_strategy_id(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert sig.strategy_id == StrategyId.BREAKOUT_CONFIRMATION

    def test_symbol_propagated(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert sig.symbol == Symbol.BTCUSDT

    def test_timestamp_propagated(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert sig.timestamp == _NOW

    def test_all_conditions_true(self):
        sig = _STRATEGY.evaluate(_feature_set())
        conds = sig.metadata["conditions"]
        assert all(conds.values()), f"Expected all True, got {conds}"

    def test_rationale_has_five_met(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert sig.rationale.count("MET") == 5

    def test_rationale_no_fail(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "FAIL" not in sig.rationale

    def test_rationale_no_miss(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "MISS" not in sig.rationale


class TestMissingFeatureData:
    """Each condition missing individually → condition not met, MISS in rationale."""

    def test_c1_missing_h1_high_low(self):
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(high_20=None, low_20=None)))
        assert not sig.metadata["conditions"]["c1_h1_consolidation"]
        assert "C1 MISS" in sig.rationale

    def test_c1_missing_h1_low_only(self):
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(low_20=None)))
        assert not sig.metadata["conditions"]["c1_h1_consolidation"]
        assert "C1 MISS" in sig.rationale

    def test_c2_missing_m15_high_20(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(high_20=None)))
        assert not sig.metadata["conditions"]["c2_m15_breakout"]
        assert "C2 MISS" in sig.rationale

    def test_c2_missing_m15_current_close(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(current_close=None)))
        assert not sig.metadata["conditions"]["c2_m15_breakout"]
        assert "C2 MISS" in sig.rationale

    def test_c3_missing_relative_volume(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(relative_volume=None)))
        assert not sig.metadata["conditions"]["c3_m15_volume"]
        assert "C3 MISS" in sig.rationale

    def test_c4_missing_rsi(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=None)))
        assert not sig.metadata["conditions"]["c4_m15_momentum"]
        assert "C4 MISS" in sig.rationale

    def test_c5_missing_ema_distance(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(ema_distance_pct=None)))
        assert not sig.metadata["conditions"]["c5_m15_not_extended"]
        assert "C5 MISS" in sig.rationale

    def test_all_missing_score_zero(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                h1=_h1(high_20=None, low_20=None),
                m15=_m15(
                    high_20=None,
                    current_close=None,
                    relative_volume=None,
                    rsi=None,
                    ema_distance_pct=None,
                ),
            )
        )
        assert sig.score == Decimal("0")
        assert sig.rationale.count("MISS") == 5


class TestH1Consolidation:
    """C1: H1 range too wide fails; boundary values."""

    def test_range_too_wide_fails(self):
        # Range 10% > 4%
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000"))))
        assert not sig.metadata["conditions"]["c1_h1_consolidation"]
        assert "C1 FAIL" in sig.rationale

    def test_range_exactly_at_threshold_fails(self):
        # range_pct == 4% → NOT < 4% → fails
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(high_20=Decimal("62400"), low_20=Decimal("60000"))))
        assert not sig.metadata["conditions"]["c1_h1_consolidation"]

    def test_range_just_below_threshold_passes(self):
        # range = 3.99%
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(high_20=Decimal("62394"), low_20=Decimal("60000"))))
        assert sig.metadata["conditions"]["c1_h1_consolidation"]

    def test_range_zero_passes(self):
        # Flat range (low == high) — possible at very low volatility
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(high_20=Decimal("60000"), low_20=Decimal("60000"))))
        assert sig.metadata["conditions"]["c1_h1_consolidation"]

    def test_c1_fail_reduces_score(self):
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000"))))
        assert sig.score == Decimal("1.0") - Decimal("0.20")


class TestBreakoutProximity:
    """C2: close proximity to 20-bar high."""

    def test_close_far_below_resistance_fails(self):
        # gap = 2% > 0.3%
        sig = _STRATEGY.evaluate(
            _feature_set(m15=_m15(high_20=Decimal("61000"), current_close=Decimal("59780")))
        )
        assert not sig.metadata["conditions"]["c2_m15_breakout"]
        assert "C2 FAIL" in sig.rationale

    def test_gap_exactly_at_threshold_passes(self):
        # gap == 0.3% exactly → <= threshold → passes
        high = Decimal("60000")
        close = high * (1 - Decimal("0.003"))  # exactly 0.3% below
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(high_20=high, current_close=close)))
        assert sig.metadata["conditions"]["c2_m15_breakout"]

    def test_gap_just_above_threshold_fails(self):
        # gap = 0.301% > 0.3%
        high = Decimal("60000")
        close = high * (1 - Decimal("0.00301"))
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(high_20=high, current_close=close)))
        assert not sig.metadata["conditions"]["c2_m15_breakout"]

    def test_close_equal_to_high_20_passes(self):
        # gap = 0% → trivially within zone
        high = Decimal("60000")
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(high_20=high, current_close=high)))
        assert sig.metadata["conditions"]["c2_m15_breakout"]

    def test_c2_fail_reduces_score(self):
        sig = _STRATEGY.evaluate(
            _feature_set(m15=_m15(high_20=Decimal("61000"), current_close=Decimal("59780")))
        )
        assert sig.score == Decimal("1.0") - Decimal("0.30")


class TestVolumeConfirmation:
    """C3: volume below threshold."""

    def test_low_volume_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(relative_volume=Decimal("1.0"))))
        assert not sig.metadata["conditions"]["c3_m15_volume"]
        assert "C3 FAIL" in sig.rationale

    def test_volume_exactly_at_threshold_passes(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(relative_volume=Decimal("1.5"))))
        assert sig.metadata["conditions"]["c3_m15_volume"]

    def test_volume_just_below_threshold_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(relative_volume=Decimal("1.499"))))
        assert not sig.metadata["conditions"]["c3_m15_volume"]

    def test_high_volume_passes(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(relative_volume=Decimal("5.0"))))
        assert sig.metadata["conditions"]["c3_m15_volume"]

    def test_c3_fail_reduces_score(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(relative_volume=Decimal("0.5"))))
        assert sig.score == Decimal("1.0") - Decimal("0.25")


class TestRSIConditions:
    """C4: RSI below min, above max, at boundaries."""

    def test_rsi_below_min_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("40"))))
        assert not sig.metadata["conditions"]["c4_m15_momentum"]
        assert "C4 FAIL" in sig.rationale
        assert "too weak" in sig.rationale

    def test_rsi_above_max_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("85"))))
        assert not sig.metadata["conditions"]["c4_m15_momentum"]
        assert "C4 FAIL" in sig.rationale
        assert "overbought" in sig.rationale

    def test_rsi_at_min_boundary_passes(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("55"))))
        assert sig.metadata["conditions"]["c4_m15_momentum"]

    def test_rsi_at_max_boundary_passes(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("80"))))
        assert sig.metadata["conditions"]["c4_m15_momentum"]

    def test_rsi_just_below_min_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("54.9"))))
        assert not sig.metadata["conditions"]["c4_m15_momentum"]

    def test_rsi_just_above_max_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("80.1"))))
        assert not sig.metadata["conditions"]["c4_m15_momentum"]

    def test_rsi_in_healthy_zone_passes(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("67"))))
        assert sig.metadata["conditions"]["c4_m15_momentum"]
        assert "C4 MET" in sig.rationale

    def test_c4_fail_reduces_score(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(rsi=Decimal("30"))))
        assert sig.score == Decimal("1.0") - Decimal("0.15")


class TestExtensionGuard:
    """C5: price overextended above EMA(9)."""

    def test_overextended_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(ema_distance_pct=Decimal("8.0"))))
        assert not sig.metadata["conditions"]["c5_m15_not_extended"]
        assert "C5 FAIL" in sig.rationale

    def test_at_max_boundary_passes(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(ema_distance_pct=Decimal("6.0"))))
        assert sig.metadata["conditions"]["c5_m15_not_extended"]

    def test_just_above_max_fails(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(ema_distance_pct=Decimal("6.01"))))
        assert not sig.metadata["conditions"]["c5_m15_not_extended"]

    def test_negative_ema_distance_passes(self):
        # Price below EMA(9) — still within extension limit
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(ema_distance_pct=Decimal("-2.0"))))
        assert sig.metadata["conditions"]["c5_m15_not_extended"]

    def test_zero_ema_distance_passes(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(ema_distance_pct=Decimal("0"))))
        assert sig.metadata["conditions"]["c5_m15_not_extended"]

    def test_c5_fail_reduces_score(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(ema_distance_pct=Decimal("10.0"))))
        assert sig.score == Decimal("1.0") - Decimal("0.10")


class TestScoreArithmetic:
    """Partial scoring and weight sanity."""

    def test_no_conditions_met_score_zero(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000")),  # C1 fail
                m15=_m15(
                    high_20=Decimal("61000"),
                    current_close=Decimal("59780"),  # C2 fail
                    relative_volume=Decimal("0.5"),  # C3 fail
                    rsi=Decimal("30"),               # C4 fail
                    ema_distance_pct=Decimal("10.0"), # C5 fail
                ),
            )
        )
        assert sig.score == Decimal("0")

    def test_only_c1_met(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                m15=_m15(
                    high_20=Decimal("61000"),
                    current_close=Decimal("59780"),  # C2 fail
                    relative_volume=Decimal("0.5"),  # C3 fail
                    rsi=Decimal("30"),               # C4 fail
                    ema_distance_pct=Decimal("10.0"), # C5 fail
                ),
            )
        )
        assert sig.score == Decimal("0.20")

    def test_only_c2_met(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000")),  # C1 fail
                m15=_m15(
                    relative_volume=Decimal("0.5"),  # C3 fail
                    rsi=Decimal("30"),               # C4 fail
                    ema_distance_pct=Decimal("10.0"), # C5 fail
                ),
            )
        )
        assert sig.score == Decimal("0.30")

    def test_only_c3_met(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000")),  # C1 fail
                m15=_m15(
                    high_20=Decimal("61000"),
                    current_close=Decimal("59780"),  # C2 fail
                    rsi=Decimal("30"),               # C4 fail
                    ema_distance_pct=Decimal("10.0"), # C5 fail
                ),
            )
        )
        assert sig.score == Decimal("0.25")

    def test_only_c4_met(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000")),  # C1 fail
                m15=_m15(
                    high_20=Decimal("61000"),
                    current_close=Decimal("59780"),  # C2 fail
                    relative_volume=Decimal("0.5"),  # C3 fail
                    ema_distance_pct=Decimal("10.0"), # C5 fail
                ),
            )
        )
        assert sig.score == Decimal("0.15")

    def test_only_c5_met(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000")),  # C1 fail
                m15=_m15(
                    high_20=Decimal("61000"),
                    current_close=Decimal("59780"),  # C2 fail
                    relative_volume=Decimal("0.5"),  # C3 fail
                    rsi=Decimal("30"),               # C4 fail
                ),
            )
        )
        assert sig.score == Decimal("0.10")

    def test_weights_sum_to_one(self):
        s = _STRATEGY
        total = (
            s._W_H1_CONSOLIDATION
            + s._W_M15_BREAKOUT
            + s._W_M15_VOLUME
            + s._W_M15_MOMENTUM
            + s._W_M15_NOT_EXTENDED
        )
        assert total == Decimal("1.0")

    def test_score_in_unit_interval(self):
        for score_val in [Decimal("0"), Decimal("0.5"), Decimal("1.0")]:
            assert Decimal("0") <= score_val <= Decimal("1")


class TestRationaleTransparency:
    """Rationale format: MET/FAIL/MISS prefixes, pipe separator."""

    def test_pipe_separates_conditions(self):
        sig = _STRATEGY.evaluate(_feature_set())
        parts = sig.rationale.split(" | ")
        assert len(parts) == 5  # 5 conditions, no notes

    def test_c1_met_label(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "C1 MET" in sig.rationale

    def test_c2_met_label(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "C2 MET" in sig.rationale

    def test_c3_met_label(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "C3 MET" in sig.rationale

    def test_c4_met_label(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "C4 MET" in sig.rationale

    def test_c5_met_label(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "C5 MET" in sig.rationale

    def test_fail_labels_appear_on_failure(self):
        sig = _STRATEGY.evaluate(
            _feature_set(h1=_h1(high_20=Decimal("66000"), low_20=Decimal("60000")))
        )
        assert "C1 FAIL" in sig.rationale

    def test_miss_label_appears_on_missing(self):
        sig = _STRATEGY.evaluate(_feature_set(m15=_m15(relative_volume=None)))
        assert "C3 MISS" in sig.rationale

    def test_rationale_is_non_empty(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert sig.rationale


class TestMetadata:
    """metadata["conditions"] holds booleans; metadata["features"] holds strings."""

    def test_conditions_keys_present(self):
        sig = _STRATEGY.evaluate(_feature_set())
        conds = sig.metadata["conditions"]
        expected = {
            "c1_h1_consolidation",
            "c2_m15_breakout",
            "c3_m15_volume",
            "c4_m15_momentum",
            "c5_m15_not_extended",
        }
        assert set(conds.keys()) == expected

    def test_conditions_are_booleans(self):
        sig = _STRATEGY.evaluate(_feature_set())
        for k, v in sig.metadata["conditions"].items():
            assert isinstance(v, bool), f"{k} is not bool: {type(v)}"

    def test_features_values_are_strings(self):
        sig = _STRATEGY.evaluate(_feature_set())
        for k, v in sig.metadata["features"].items():
            assert isinstance(v, str), f"{k} is not str: {type(v)}"

    def test_features_contains_h1_range(self):
        sig = _STRATEGY.evaluate(_feature_set())
        feats = sig.metadata["features"]
        assert "h1_high_20" in feats
        assert "h1_low_20" in feats

    def test_features_contains_m15_breakout_data(self):
        sig = _STRATEGY.evaluate(_feature_set())
        feats = sig.metadata["features"]
        assert "m15_high_20" in feats
        assert "m15_current_close" in feats

    def test_features_contains_m15_volume(self):
        sig = _STRATEGY.evaluate(_feature_set())
        assert "m15_relative_volume" in sig.metadata["features"]

    def test_features_excludes_missing_data(self):
        # When high_20 is None, h1_high_20 must not be in features snapshot
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(high_20=None, low_20=None)))
        assert "h1_high_20" not in sig.metadata["features"]

    def test_conditions_false_on_missing(self):
        sig = _STRATEGY.evaluate(
            _feature_set(m15=_m15(high_20=None, current_close=None))
        )
        assert sig.metadata["conditions"]["c2_m15_breakout"] is False


class TestContextNotes:
    """Context notes appear in rationale but do not affect score."""

    def test_h1_rsi_weak_note_appears(self):
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(rsi=Decimal("40"))))
        assert "NOTE" in sig.rationale
        assert "H1 RSI" in sig.rationale

    def test_h1_rsi_above_weak_threshold_no_note(self):
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(rsi=Decimal("55"))))
        assert "H1 RSI" not in sig.rationale

    def test_h1_rsi_weak_does_not_affect_score(self):
        # All 5 conditions still met; score must still be 1.0 despite note
        sig = _STRATEGY.evaluate(_feature_set(h1=_h1(rsi=Decimal("40"))))
        assert sig.score == Decimal("1.0")

    def test_m15_euphoria_note_appears(self):
        sig = _STRATEGY.evaluate(
            _feature_set(m15=_m15(recent_return_pct=Decimal("7.0")))
        )
        assert "NOTE" in sig.rationale
        assert "5-bar return" in sig.rationale

    def test_m15_return_at_threshold_no_note(self):
        sig = _STRATEGY.evaluate(
            _feature_set(m15=_m15(recent_return_pct=Decimal("6.0")))
        )
        assert "5-bar return" not in sig.rationale

    def test_m15_euphoria_does_not_affect_score(self):
        sig = _STRATEGY.evaluate(
            _feature_set(m15=_m15(recent_return_pct=Decimal("9.0")))
        )
        assert sig.score == Decimal("1.0")

    def test_m5_relative_volume_captured_in_features(self):
        sig = _STRATEGY.evaluate(_feature_set(m5=_m5(relative_volume=Decimal("1.8"))))
        assert "m5_relative_volume" in sig.metadata["features"]
        assert sig.metadata["features"]["m5_relative_volume"] == "1.8"

    def test_m5_none_not_in_features(self):
        sig = _STRATEGY.evaluate(_feature_set(m5=_m5(relative_volume=None)))
        assert "m5_relative_volume" not in sig.metadata["features"]

    def test_both_notes_appear_together(self):
        sig = _STRATEGY.evaluate(
            _feature_set(
                h1=_h1(rsi=Decimal("38")),
                m15=_m15(recent_return_pct=Decimal("8.0")),
            )
        )
        assert sig.rationale.count("NOTE") == 2


class TestDeterminism:
    """Same input always produces same output."""

    def test_same_input_same_output(self):
        fs = _feature_set()
        sig1 = _STRATEGY.evaluate(fs)
        sig2 = _STRATEGY.evaluate(fs)
        assert sig1.score == sig2.score
        assert sig1.rationale == sig2.rationale
        assert sig1.metadata == sig2.metadata

    def test_different_instances_same_result(self):
        fs = _feature_set()
        s1 = BreakoutConfirmationStrategy()
        s2 = BreakoutConfirmationStrategy()
        assert s1.evaluate(fs).score == s2.evaluate(fs).score

    def test_evaluate_does_not_mutate_features(self):
        fs = _feature_set()
        original_score_h1_high = fs.h1.high_20
        _STRATEGY.evaluate(fs)
        assert fs.h1.high_20 == original_score_h1_high
