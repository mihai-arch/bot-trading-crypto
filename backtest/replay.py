"""Replay the 50/50 blend on cached spot klines.

The blend is two sub-accounts that are not rebalanced into each other:

- 50% daily core rotation (top-1 of BTC/ETH/SOL by 30-day return, cash if <= 0)
- 50% EMA(20)/EMA(100) on 4h with a 40% volatility target, split 1/3 per coin

The two sleeves are funded 50/50 at the start of the sample and are never
rebalanced into each other. Out-of-sample metrics rescale that combined
equity curve at the split, which is how report section 9 measured the
post-hoc blend: about +20% OOS CAGR, +36% total, -46% max drawdown,
Sharpe 0.68.

The research window is 2021-10-22 through 2026-11-11, split 2/3 train
and 1/3 test. The cached klines are Binance spot (see ``fetch_klines.py``).
Bybit's public API was blocked from the network used to build the cache.
On this cache the blend lands within a few points of the Bybit report.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engine import metrics, run, run_multi
from bit.strategies.blend_signals import conservative_weight_series, rotation_weight_series

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
START = pd.Timestamp("2021-10-22", tz="UTC")
END = pd.Timestamp("2026-11-11", tz="UTC")
SPLIT = START + (END - START) * 2 / 3

# Report section 9, post-hoc 50/50 blend, out of sample.
REPORT_OOS = {"CAGR": 0.20, "TotalRet": 0.36, "MaxDD": -0.46, "Sharpe": 0.68}


def _read(data_dir: Path, symbol: str, interval: str) -> pd.DataFrame:
    path = data_dir / f"{symbol}_{interval}.csv.gz"
    frame = pd.read_csv(path)
    frame["time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    frame = frame.set_index("time").sort_index()
    return frame[["open", "high", "low", "close", "volume"]]


def _with_close_time(frame: pd.DataFrame, step: str) -> pd.DataFrame:
    out = frame.copy()
    out["close_time"] = out.index + pd.Timedelta(step)
    return out


def _daily_panel(data_dir: Path) -> dict:
    frames = {symbol: _read(data_dir, symbol, "D") for symbol in SYMBOLS}
    index = None
    for frame in frames.values():
        index = frame.index if index is None else index.intersection(frame.index)
    index = index.sort_values()
    panel = {
        column: pd.DataFrame({symbol: frames[symbol].loc[index, column] for symbol in SYMBOLS})
        for column in ("open", "high", "low", "close", "volume")
    }
    panel["close_time"] = index + pd.Timedelta("1D")
    return panel


def _clip_norm(series: pd.Series, start, end) -> pd.Series:
    daily = series.resample("1D").last().ffill()
    window = daily[(daily.index >= start) & (daily.index <= end)].dropna()
    return window / window.iloc[0]


def blend_curves(data_dir: Path) -> dict[str, pd.Series]:
    """Full-sample equity curves, each normalised to 1 at START."""
    panel = _daily_panel(data_dir)
    closes = {symbol: panel["close"][symbol].tolist() for symbol in SYMBOLS}
    weights = rotation_weight_series(closes, lookback=30)
    matrix = np.column_stack([weights[symbol] for symbol in SYMBOLS])
    rotation_equity, _stats = run_multi(panel, matrix, band=0.05)
    rotation = _clip_norm(rotation_equity, START, END)

    sleeves = []
    for symbol in SYMBOLS:
        frame = _with_close_time(_read(data_dir, symbol, "240"), "4h")
        target = conservative_weight_series(
            frame.close.tolist(),
            fast=20,
            slow=100,
            vol_target=0.40,
            vol_n=180,
            bars_per_year=2190,
        )
        equity, _trades = run(frame, np.asarray(target, float), rebalance_band=0.10)
        sleeves.append(_clip_norm(equity.equity, START, END))
    conservative = sum(sleeves) / len(sleeves)
    return {"rotation": rotation, "conservative": conservative}


def combined_blend(curves: dict[str, pd.Series]) -> pd.Series:
    """50/50 at the start of each sleeve's curve, with no later transfer."""
    aligned = curves["rotation"].index.intersection(curves["conservative"].index)
    return 0.5 * curves["rotation"].loc[aligned] + 0.5 * curves["conservative"].loc[aligned]


def oos_metrics(data_dir: Path | None = None) -> dict:
    root = data_dir or Path(__file__).resolve().parent / "data"
    curves = blend_curves(root)
    blend = combined_blend(curves)
    window = blend[(blend.index >= SPLIT) & (blend.index <= END)].dropna()
    return metrics(window / window.iloc[0])


def main() -> None:
    result = oos_metrics()
    print(f"split {SPLIT.date()} -> {END.date()}")
    for key, value in result.items():
        report = REPORT_OOS.get(key)
        extra = f"  report {report:+.0%}" if report is not None and key != "Sharpe" else ""
        if key == "Sharpe" and report is not None:
            extra = f"  report {report:.2f}"
        if key == "Sharpe":
            print(f"{key:10s} {value: .3f}{extra}")
        else:
            print(f"{key:10s} {value: .1%}{extra}")


if __name__ == "__main__":
    main()
