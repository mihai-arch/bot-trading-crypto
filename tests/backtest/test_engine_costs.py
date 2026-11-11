"""Sanity checks for the ported backtester. No market-data files required."""

import numpy as np
import pandas as pd

from backtest.engine import run


def _flat(n=20, price=100.0) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    frame = pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price},
        index=index,
    )
    frame["close_time"] = frame.index + pd.Timedelta("1D")
    return frame


def test_round_trip_on_a_flat_market_costs_about_30_bps():
    frame = _flat()
    # Enter on bar 0 (fills at bar 1 open) and exit on bar 1 (fills at bar 2 open).
    target = np.zeros(len(frame))
    target[0] = 1.0
    target[1] = 0.0
    _equity, trades = run(frame, target)
    assert len(trades) == 1
    # 0.1% fee + 0.05% slippage on each side ≈ -0.30% on a flat price.
    assert abs(float(trades.ret.iloc[0]) - (-0.0030)) <= 0.0002


def test_same_bar_signal_is_not_a_money_machine():
    frame = _flat(60)
    # Alternate the close above and below the open. The engine fills next open,
    # so knowing the current bar's colour is not an edge.
    frame = frame.copy()
    frame["close"] = np.where(np.arange(len(frame)) % 2 == 0, 101.0, 99.0)
    signal = (frame.close > frame.open).astype(float).to_numpy()
    equity, _trades = run(frame, signal)
    # Fills are at the next open, which does not know this bar's close.
    assert float(equity.equity.iloc[-1]) < 1.0
