"""
BreakoutConfirmationStrategy

Evaluates whether price is breaking out of a consolidation range with
sufficient confirmation to reduce false breakouts.
Returns a Signal with a score in [0, 1] and a human-readable rationale.

Scoring model — five binary conditions, weights sum to 1.0:

  C1  H1 consolidation     (h1.high_20 - h1.low_20) / h1.low_20 < RANGE_WIDTH_PCT  weight 0.20
  C2  M15 at breakout      (m15.high_20 - m15.current_close) / m15.high_20          weight 0.30
                             <= BREAKOUT_ZONE_PCT (close within 0.3% of 20-bar high)
  C3  M15 volume spike     m15.relative_volume >= MIN_RELATIVE_VOL (1.5x)            weight 0.25
  C4  M15 momentum ok      m15.rsi in [RSI_M15_MIN, RSI_M15_OB_MAX]                  weight 0.15
  C5  M15 not extended     m15.ema_distance_pct <= EMA_DIST_MAX_PCT (6%)             weight 0.10

score = Σ(weight_i × condition_met_i)   — all weights and conditions are explicit constants.

Note on C2 (breakout detection):
  `high_20` includes the current bar's high (max of last 20 highs), so `close > high_20`
  is structurally impossible — the current close is always ≤ its own high ≤ high_20.
  Instead, C2 checks whether close is within BREAKOUT_ZONE_PCT of the 20-bar high,
  capturing candles that close near resistance with a small upper wick (strong breakout
  character). A large gap means price is still well below the range ceiling.

Conditions with unavailable feature data (None) are treated as not met and explained
in the rationale. This is conservative: missing data means we cannot trade safely.

Context-only (metadata, no score impact):
  - H1 RSI: flagged if below RSI_H1_WEAK — higher timeframe trend may be weak.
  - M15 5-bar return: flagged if above EUPHORIA_RETURN_PCT — possible extended spike.
  - M5 relative_volume: captured for downstream inspection.

To tune thresholds, adjust the class-level constants and re-run the tests.
All thresholds are candidates to move into BITConfig when the strategy is stable.
"""

from decimal import Decimal

from ..domain.enums import StrategyId
from ..domain.features import FeatureSet
from ..domain.signals import Signal


class BreakoutConfirmationStrategy:
    strategy_id = StrategyId.BREAKOUT_CONFIRMATION

    # ── Condition weights (must sum to exactly 1.0) ───────────────────────────
    _W_H1_CONSOLIDATION = Decimal("0.20")  # H1 prior range — context quality filter
    _W_M15_BREAKOUT = Decimal("0.30")      # M15 close near 20-bar high — breakout character
    _W_M15_VOLUME = Decimal("0.25")        # M15 volume spike — confirms genuine interest
    _W_M15_MOMENTUM = Decimal("0.15")      # M15 RSI in supportive zone — not exhausted
    _W_M15_NOT_EXTENDED = Decimal("0.10")  # M15 not too far above EMA — not chasing

    # ── Consolidation threshold ────────────────────────────────────────────────
    _RANGE_WIDTH_PCT = Decimal("0.04")  # C1: H1 (high_20-low_20)/low_20 < 4% = consolidation

    # ── Breakout proximity threshold ──────────────────────────────────────────
    _BREAKOUT_ZONE_PCT = Decimal("0.003")  # C2: close within 0.3% of 20-bar high

    # ── Volume threshold ───────────────────────────────────────────────────────
    _MIN_RELATIVE_VOL = Decimal("1.5")  # C3: volume must be ≥ 150% of its 20-bar SMA

    # ── RSI thresholds ─────────────────────────────────────────────────────────
    _RSI_M15_MIN = Decimal("55")     # C4: M15 RSI below this → momentum too weak
    _RSI_M15_OB_MAX = Decimal("80")  # C4: M15 RSI above this → overbought, do not chase
    _RSI_H1_WEAK = Decimal("45")     # context note only: H1 may be in weak trend

    # ── Extension threshold ────────────────────────────────────────────────────
    _EMA_DIST_MAX_PCT = Decimal("6.0")  # C5: price not more than 6% above EMA(9)

    # ── Metadata-only alert thresholds ────────────────────────────────────────
    _EUPHORIA_RETURN_PCT = Decimal("6.0")  # M15 5-bar return above this → flag as spike

    def evaluate(self, features: FeatureSet) -> Signal:
        """
        Score the feature set for a breakout confirmation setup.

        Each of the five conditions contributes its weight to the score when met.
        A condition with unavailable feature data is treated as not met and noted
        in the rationale — this is conservative and intentional.

        Returns:
            Signal with:
            - score in [0, 1] (exact sum of weights of conditions met)
            - rationale: pipe-separated description of every condition result
            - metadata["conditions"]: dict[str, bool] per-condition result
            - metadata["features"]: dict[str, str] raw feature values used
        """
        h1 = features.h1
        m15 = features.m15
        m5 = features.m5

        score = Decimal("0")
        reasons: list[str] = []
        conditions: dict[str, bool] = {}
        feature_snapshot: dict[str, str] = {}

        # ── C1: H1 consolidation quality (weight 0.20) ───────────────────────
        # Confirms the higher-timeframe context is a tight range, not a trending
        # market. Breakouts from wide/trending ranges are less reliable.
        if h1.high_20 is not None and h1.low_20 is not None and h1.low_20 > 0:
            feature_snapshot["h1_high_20"] = str(h1.high_20)
            feature_snapshot["h1_low_20"] = str(h1.low_20)
            range_pct = (h1.high_20 - h1.low_20) / h1.low_20
            feature_snapshot["h1_range_pct"] = str(range_pct)
            c1 = range_pct < self._RANGE_WIDTH_PCT
            if c1:
                reasons.append(
                    f"C1 MET: H1 consolidation confirmed"
                    f" — 20-bar range {range_pct * 100:.2f}% < {self._RANGE_WIDTH_PCT * 100:.0f}%"
                )
            else:
                reasons.append(
                    f"C1 FAIL: H1 not consolidating"
                    f" — 20-bar range {range_pct * 100:.2f}% >= {self._RANGE_WIDTH_PCT * 100:.0f}%"
                )
        else:
            c1 = False
            reasons.append("C1 MISS: H1 high_20/low_20 unavailable — need ≥20 closed bars")

        conditions["c1_h1_consolidation"] = c1
        if c1:
            score += self._W_H1_CONSOLIDATION

        # ── C2: M15 close at breakout level (weight 0.30) ────────────────────
        # Price must close within BREAKOUT_ZONE_PCT of the 20-bar high, indicating
        # a strong breakout candle with minimal upper wick (rejection).
        # Note: close > high_20 is impossible since high_20 >= current bar's high >= close.
        if m15.high_20 is not None and m15.current_close is not None and m15.high_20 > 0:
            feature_snapshot["m15_high_20"] = str(m15.high_20)
            feature_snapshot["m15_current_close"] = str(m15.current_close)
            gap_pct = (m15.high_20 - m15.current_close) / m15.high_20
            feature_snapshot["m15_breakout_gap_pct"] = str(gap_pct)
            c2 = gap_pct <= self._BREAKOUT_ZONE_PCT
            if c2:
                reasons.append(
                    f"C2 MET: M15 close within breakout zone"
                    f" — {gap_pct * 100:.3f}% below 20-bar high"
                    f" (max {self._BREAKOUT_ZONE_PCT * 100:.1f}%)"
                )
            else:
                reasons.append(
                    f"C2 FAIL: M15 close too far from resistance"
                    f" — {gap_pct * 100:.3f}% below 20-bar high"
                    f" (max {self._BREAKOUT_ZONE_PCT * 100:.1f}%)"
                )
        else:
            c2 = False
            reasons.append("C2 MISS: M15 high_20/current_close unavailable — need ≥20 closed bars")

        conditions["c2_m15_breakout"] = c2
        if c2:
            score += self._W_M15_BREAKOUT

        # ── C3: M15 volume spike (weight 0.25) ────────────────────────────────
        # Volume must be at least 1.5x its 20-bar average to confirm genuine
        # buying interest, distinguishing real breakouts from low-volume drift.
        if m15.relative_volume is not None:
            feature_snapshot["m15_relative_volume"] = str(m15.relative_volume)
            c3 = m15.relative_volume >= self._MIN_RELATIVE_VOL
            if c3:
                reasons.append(
                    f"C3 MET: M15 volume {m15.relative_volume:.2f}x average"
                    f" (min {self._MIN_RELATIVE_VOL}x) — breakout confirmed by volume"
                )
            else:
                reasons.append(
                    f"C3 FAIL: M15 volume {m15.relative_volume:.2f}x — below"
                    f" {self._MIN_RELATIVE_VOL}x threshold, possible false breakout"
                )
        else:
            c3 = False
            reasons.append("C3 MISS: M15 volume data unavailable — need ≥20 closed bars")

        conditions["c3_m15_volume"] = c3
        if c3:
            score += self._W_M15_VOLUME

        # ── C4: M15 RSI momentum quality (weight 0.15) ────────────────────────
        # RSI must be in the bullish zone: strong enough to confirm momentum,
        # but not so high that the move is overheated and prone to reversal.
        if m15.rsi is not None:
            feature_snapshot["m15_rsi"] = str(m15.rsi)
            if m15.rsi < self._RSI_M15_MIN:
                c4 = False
                reasons.append(
                    f"C4 FAIL: M15 RSI {m15.rsi:.1f} < {self._RSI_M15_MIN}"
                    f" — momentum too weak for breakout entry"
                )
            elif m15.rsi > self._RSI_M15_OB_MAX:
                c4 = False
                reasons.append(
                    f"C4 FAIL: M15 RSI {m15.rsi:.1f} > {self._RSI_M15_OB_MAX}"
                    f" — overbought, breakout likely exhausted"
                )
            else:
                c4 = True
                reasons.append(
                    f"C4 MET: M15 RSI {m15.rsi:.1f}"
                    f" in healthy zone [{self._RSI_M15_MIN}, {self._RSI_M15_OB_MAX}]"
                )
        else:
            c4 = False
            reasons.append("C4 MISS: M15 RSI unavailable — need ≥15 closed bars")

        conditions["c4_m15_momentum"] = c4
        if c4:
            score += self._W_M15_MOMENTUM

        # ── C5: M15 price not overextended (weight 0.10) ──────────────────────
        # Price should not be too far above EMA(9). An excessively extended
        # breakout candle risks chasing and a snap-back to the EMA.
        if m15.ema_distance_pct is not None:
            feature_snapshot["m15_ema_dist_pct"] = str(m15.ema_distance_pct)
            c5 = m15.ema_distance_pct <= self._EMA_DIST_MAX_PCT
            if c5:
                reasons.append(
                    f"C5 MET: M15 price {m15.ema_distance_pct:.2f}% above EMA(9)"
                    f" — within extension limit {self._EMA_DIST_MAX_PCT}%"
                )
            else:
                reasons.append(
                    f"C5 FAIL: M15 price {m15.ema_distance_pct:.2f}% above EMA(9)"
                    f" — overextended beyond {self._EMA_DIST_MAX_PCT}%"
                )
        else:
            c5 = False
            reasons.append("C5 MISS: M15 EMA distance unavailable — need ≥9 closed bars")

        conditions["c5_m15_not_extended"] = c5
        if c5:
            score += self._W_M15_NOT_EXTENDED

        # ── Context notes (not scored — informational only) ───────────────────
        notes: list[str] = []

        if h1.rsi is not None:
            feature_snapshot["h1_rsi"] = str(h1.rsi)
            if h1.rsi < self._RSI_H1_WEAK:
                notes.append(
                    f"NOTE: H1 RSI {h1.rsi:.1f} is weak"
                    f" — higher timeframe trend may not support breakout"
                )

        if m15.recent_return_pct is not None:
            feature_snapshot["m15_recent_return_pct"] = str(m15.recent_return_pct)
            if m15.recent_return_pct > self._EUPHORIA_RETURN_PCT:
                notes.append(
                    f"NOTE: M15 5-bar return {m15.recent_return_pct:.2f}%"
                    f" elevated — possible euphoric spike, not a clean breakout"
                )

        if m5.relative_volume is not None:
            feature_snapshot["m5_relative_volume"] = str(m5.relative_volume)

        return Signal(
            strategy_id=self.strategy_id,
            symbol=features.symbol,
            timestamp=features.timestamp,
            score=score,
            rationale=" | ".join(reasons + notes),
            metadata={
                "conditions": conditions,
                "features": feature_snapshot,
            },
        )
