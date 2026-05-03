"""
TrendContinuationStrategy

Evaluates whether price is in a healthy bullish trend continuation context.
Returns a Signal with a score in [0, 1] and a human-readable rationale.

Scoring model — five binary conditions, weights sum to 1.0:

  C1  H1 EMA aligned     h1.ema_fast > h1.ema_slow                         weight 0.30
  C2  M15 EMA aligned    m15.ema_fast > m15.ema_slow                        weight 0.20
  C3  M15 momentum ok    m15.rsi in [RSI_M15_MIN, RSI_M15_OB_MAX]           weight 0.25
  C4  M15 not extended   m15.ema_distance_pct in [EMA_DIST_MIN, EMA_DIST_MAX] weight 0.15
  C5  M15 volume active  m15.relative_volume >= MIN_RELATIVE_VOL             weight 0.10

score = Σ(weight_i × condition_met_i)   — all weights and conditions are explicit constants.

Conditions with unavailable feature data (None) are treated as not met and explained
in the rationale. This is conservative: missing data means we cannot trade safely.

Context-only (metadata, no score impact):
  - H1 RSI: flagged if below RSI_H1_WEAK — uptrend may be losing stamina.
  - M15 5-bar return: flagged if above EUPHORIA_PCT — possible spike extension.
  - M5 relative_volume: captured for downstream inspection.

To tune thresholds, adjust the class-level constants and re-run the tests.
All thresholds are candidates to move into BITConfig when the strategy is stable.
"""

from decimal import Decimal

from ..domain.enums import StrategyId
from ..domain.features import FeatureSet
from ..domain.signals import Signal


class TrendContinuationStrategy:
    strategy_id = StrategyId.TREND_CONTINUATION

    # ── Condition weights (must sum to exactly 1.0) ───────────────────────────
    _W_H1_TREND = Decimal("0.30")       # H1 EMA alignment — primary trend direction filter
    _W_M15_STRUCTURE = Decimal("0.20")  # M15 EMA alignment — intermediate structure
    _W_M15_MOMENTUM = Decimal("0.25")   # M15 RSI in healthy zone — momentum quality
    _W_M15_PRICE_LOC = Decimal("0.15")  # M15 price not overextended above EMA(9)
    _W_M15_VOLUME = Decimal("0.10")     # M15 volume participation

    # ── RSI thresholds ─────────────────────────────────────────────────────────
    _RSI_M15_MIN = Decimal("50")     # C3: M15 RSI below this → momentum too weak to enter
    _RSI_M15_OB_MAX = Decimal("75")  # C3: M15 RSI above this → overbought, do not chase
    _RSI_H1_WEAK = Decimal("45")     # context note only: H1 trend may be losing momentum

    # ── Price structure thresholds ─────────────────────────────────────────────
    _EMA_DIST_MIN_PCT = Decimal("-1.0")  # C4: max tolerated pullback below EMA(9); 1% below = acceptable
    _EMA_DIST_MAX_PCT = Decimal("2.5")   # C4: max extension above EMA(9); beyond this = chasing

    # ── Volume threshold ───────────────────────────────────────────────────────
    _MIN_RELATIVE_VOL = Decimal("0.8")  # C5: volume must be ≥ 80% of its 20-bar SMA

    # ── Metadata-only alert thresholds ────────────────────────────────────────
    _EUPHORIA_PCT = Decimal("5.0")  # M15 5-bar return above this → flag as possible spike

    def evaluate(self, features: FeatureSet) -> Signal:
        """
        Score the feature set for a trend continuation setup.

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

        # ── C1: H1 EMA alignment (weight 0.30) ───────────────────────────────
        # Confirms the higher-timeframe trend is bullish. This is the primary
        # context filter — a bearish H1 EMA cross disqualifies the setup.
        if h1.ema_fast is not None and h1.ema_slow is not None:
            feature_snapshot["h1_ema_fast"] = str(h1.ema_fast)
            feature_snapshot["h1_ema_slow"] = str(h1.ema_slow)
            c1 = h1.ema_fast > h1.ema_slow
            if c1:
                reasons.append(
                    f"C1 MET: H1 uptrend confirmed"
                    f" — EMA(9) {h1.ema_fast:.2f} > EMA(21) {h1.ema_slow:.2f}"
                )
            else:
                reasons.append(
                    f"C1 FAIL: H1 not bullish"
                    f" — EMA(9) {h1.ema_fast:.2f} ≤ EMA(21) {h1.ema_slow:.2f}"
                )
        else:
            c1 = False
            reasons.append("C1 MISS: H1 EMA unavailable — need ≥21 closed bars")

        conditions["c1_h1_trend"] = c1
        if c1:
            score += self._W_H1_TREND

        # ── C2: M15 EMA alignment (weight 0.20) ──────────────────────────────
        # The intermediate timeframe must also show bullish EMA structure.
        # H1 bullish + M15 bearish = weakening trend, not a continuation setup.
        if m15.ema_fast is not None and m15.ema_slow is not None:
            feature_snapshot["m15_ema_fast"] = str(m15.ema_fast)
            feature_snapshot["m15_ema_slow"] = str(m15.ema_slow)
            c2 = m15.ema_fast > m15.ema_slow
            if c2:
                reasons.append(
                    f"C2 MET: M15 structure bullish"
                    f" — EMA(9) {m15.ema_fast:.2f} > EMA(21) {m15.ema_slow:.2f}"
                )
            else:
                reasons.append(
                    f"C2 FAIL: M15 not bullish"
                    f" — EMA(9) {m15.ema_fast:.2f} ≤ EMA(21) {m15.ema_slow:.2f}"
                )
        else:
            c2 = False
            reasons.append("C2 MISS: M15 EMA unavailable — need ≥21 closed bars")

        conditions["c2_m15_structure"] = c2
        if c2:
            score += self._W_M15_STRUCTURE

        # ── C3: M15 RSI momentum quality (weight 0.25) ───────────────────────
        # RSI must be in the bullish zone: strong enough to show momentum,
        # but not so high that the move is overheated and prone to reversal.
        if m15.rsi is not None:
            feature_snapshot["m15_rsi"] = str(m15.rsi)
            if m15.rsi < self._RSI_M15_MIN:
                c3 = False
                reasons.append(
                    f"C3 FAIL: M15 RSI {m15.rsi:.1f} < {self._RSI_M15_MIN}"
                    f" — momentum too weak to enter"
                )
            elif m15.rsi > self._RSI_M15_OB_MAX:
                c3 = False
                reasons.append(
                    f"C3 FAIL: M15 RSI {m15.rsi:.1f} > {self._RSI_M15_OB_MAX}"
                    f" — overbought, do not chase"
                )
            else:
                c3 = True
                reasons.append(
                    f"C3 MET: M15 RSI {m15.rsi:.1f}"
                    f" in healthy zone [{self._RSI_M15_MIN}, {self._RSI_M15_OB_MAX}]"
                )
        else:
            c3 = False
            reasons.append("C3 MISS: M15 RSI unavailable — need ≥15 closed bars")

        conditions["c3_m15_momentum"] = c3
        if c3:
            score += self._W_M15_MOMENTUM

        # ── C4: M15 price not overextended (weight 0.15) ─────────────────────
        # Price should be near but not too far above EMA(9). A tight pullback
        # into EMA zone is ideal. Too far below → breakdown. Too far above → chasing.
        if m15.ema_distance_pct is not None:
            feature_snapshot["m15_ema_dist_pct"] = str(m15.ema_distance_pct)
            if m15.ema_distance_pct < self._EMA_DIST_MIN_PCT:
                c4 = False
                reasons.append(
                    f"C4 FAIL: M15 price {m15.ema_distance_pct:.2f}% below EMA(9)"
                    f" — exceeds pullback limit {self._EMA_DIST_MIN_PCT}%"
                )
            elif m15.ema_distance_pct > self._EMA_DIST_MAX_PCT:
                c4 = False
                reasons.append(
                    f"C4 FAIL: M15 price {m15.ema_distance_pct:.2f}% above EMA(9)"
                    f" — overextended beyond {self._EMA_DIST_MAX_PCT}%"
                )
            else:
                c4 = True
                reasons.append(
                    f"C4 MET: M15 price {m15.ema_distance_pct:.2f}% from EMA(9)"
                    f" — within continuation zone"
                )
        else:
            c4 = False
            reasons.append("C4 MISS: M15 EMA distance unavailable — need ≥9 closed bars")

        conditions["c4_m15_price_location"] = c4
        if c4:
            score += self._W_M15_PRICE_LOC

        # ── C5: M15 volume participation (weight 0.10) ────────────────────────
        # Volume must be at least 80% of its 20-bar average. Low volume during
        # a potential continuation setup is a low-conviction signal.
        if m15.relative_volume is not None:
            feature_snapshot["m15_relative_volume"] = str(m15.relative_volume)
            c5 = m15.relative_volume >= self._MIN_RELATIVE_VOL
            if c5:
                reasons.append(
                    f"C5 MET: M15 volume {m15.relative_volume:.2f}x average"
                    f" (min {self._MIN_RELATIVE_VOL}x)"
                )
            else:
                reasons.append(
                    f"C5 FAIL: M15 volume {m15.relative_volume:.2f}x — below"
                    f" {self._MIN_RELATIVE_VOL}x threshold"
                )
        else:
            c5 = False
            reasons.append("C5 MISS: M15 volume data unavailable — need ≥20 closed bars")

        conditions["c5_m15_volume"] = c5
        if c5:
            score += self._W_M15_VOLUME

        # ── Context notes (not scored — informational only) ───────────────────
        notes: list[str] = []

        if h1.rsi is not None:
            feature_snapshot["h1_rsi"] = str(h1.rsi)
            if h1.rsi < self._RSI_H1_WEAK:
                notes.append(
                    f"NOTE: H1 RSI {h1.rsi:.1f} is weak"
                    f" — uptrend may be losing momentum"
                )

        if m15.recent_return_pct is not None:
            feature_snapshot["m15_recent_return_pct"] = str(m15.recent_return_pct)
            if m15.recent_return_pct > self._EUPHORIA_PCT:
                notes.append(
                    f"NOTE: M15 5-bar return {m15.recent_return_pct:.2f}%"
                    f" elevated — possible euphoric spike, not a pullback entry"
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
