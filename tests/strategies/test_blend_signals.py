"""Blend signals match the research definitions and do not peek ahead."""

import math

import numpy as np
import pandas as pd

from bit.strategies.blend_signals import (
    choose_rotation,
    conservative_weight_series,
    ema_series,
    rotation_weight_series,
    trailing_return,
)


def test_ema_matches_pandas_adjust_false():
    closes = [float(v) for v in range(1, 40)]
    got = np.asarray(ema_series(closes, 20))
    expected = pd.Series(closes).ewm(span=20, adjust=False).mean().to_numpy()
    np.testing.assert_allclose(got, expected, rtol=1e-12, atol=1e-12)


def test_conservative_weights_match_pandas_on_a_random_path():
    rng = np.random.default_rng(7)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 400))))
    got = np.asarray(
        conservative_weight_series(
            closes, fast=20, slow=100, vol_target=0.40, vol_n=180, bars_per_year=2190
        )
    )
    series = pd.Series(closes)
    fast = series.ewm(span=20, adjust=False).mean()
    slow = series.ewm(span=100, adjust=False).mean()
    signal = (fast > slow).astype(float)
    signal[series.expanding().count() < 100] = 0.0
    vol = np.log(series).diff().rolling(180).std() * math.sqrt(2190)
    expected = (signal * np.minimum(1.0, 0.40 / vol)).fillna(0.0).to_numpy()
    np.testing.assert_allclose(got, expected, rtol=1e-9, atol=1e-12)


def test_weight_uses_only_closes_up_to_each_bar():
    rng = np.random.default_rng(3)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0.001, 0.01, 250))))
    full = conservative_weight_series(closes, vol_n=180, bars_per_year=2190)
    bumped = closes[:-1] + [closes[-1] * 1.4]
    revised = conservative_weight_series(bumped, vol_n=180, bars_per_year=2190)
    # A change to the last close must not rewrite any earlier decision.
    assert revised[:-1] == full[:-1]


def test_rotation_picks_the_positive_leader_and_goes_to_cash():
    flat = [100.0] * 40
    rising = [100.0 + i for i in range(40)]
    falling = [100.0 - i * 0.5 for i in range(40)]
    winner, returns = choose_rotation(
        {"BTCUSDT": flat, "ETHUSDT": falling, "SOLUSDT": rising}, 30
    )
    assert winner == "SOLUSDT"
    assert returns["ETHUSDT"] < 0
    assert trailing_return(falling, 30) <= 0

    all_down = {"BTCUSDT": falling, "ETHUSDT": falling, "SOLUSDT": [90.0 - i for i in range(40)]}
    winner, _ = choose_rotation(all_down, 30)
    assert winner is None


def test_rotation_series_is_cash_before_the_lookback():
    closes = {
        "BTCUSDT": [100.0] * 10,
        "ETHUSDT": [100.0 + i for i in range(10)],
        "SOLUSDT": [50.0] * 10,
    }
    weights = rotation_weight_series(closes, lookback=5)
    assert weights["ETHUSDT"][:5] == [0.0] * 5
    assert weights["ETHUSDT"][-1] == 1.0
    assert weights["BTCUSDT"][-1] == 0.0
