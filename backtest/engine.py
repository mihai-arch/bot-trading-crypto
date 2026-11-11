"""Bar-by-bar long-only spot backtester.

Signals are computed on the closed bar and filled at the next bar's open.
Costs default to 0.1% fee and 0.05% adverse slippage per side. The buy fee
is part of the cost basis, so trade P&L is net of both sides.

This is the engine used for the 2026-11-11 research note. Stops are optional
and are not used by the blend.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEE = 0.001
SLIP = 0.0005


def run(
    df: pd.DataFrame,
    target,
    stop_pct=None,
    tp_pct=None,
    trail=None,
    rebalance_band=0.0,
    fee=None,
    slip=None,
    start_cash=1.0,
):
    """``target[i]`` is the desired weight decided at the close of bar i.

    NaN means keep the current target. The order fills at the open of bar i+1.
    """
    fee = FEE if fee is None else fee
    slip = SLIP if slip is None else slip
    o = df.open.values
    h = df.high.values
    low = df.low.values
    c = df.close.values
    n = len(df)
    cash = start_cash
    qty = 0.0
    entry_cost = 0.0
    eq = np.empty(n)
    wpos = np.empty(n)
    trades = []
    pending = None
    stop_lvl = None
    tp_lvl = None
    tstop = None

    def trade_to(w_new, px, i, reason="signal"):
        nonlocal cash, qty, entry_cost, stop_lvl, tp_lvl, tstop
        equity = cash + qty * px
        cur_val = qty * px
        tgt_val = w_new * equity
        if tgt_val > cur_val:
            buy_px = px * (1 + slip)
            notional = min(tgt_val - cur_val, cash / (1 + fee))
            if notional <= 1e-12:
                return
            q = notional / buy_px
            cash -= notional * (1 + fee)
            if qty == 0:
                entry_cost = 0.0
            entry_cost += notional * (1 + fee)
            qty += q
            if stop_pct:
                stop_lvl = (entry_cost / qty) * (1 - stop_pct)
            if tp_pct:
                tp_lvl = (entry_cost / qty) * (1 + tp_pct)
        elif tgt_val < cur_val and qty > 0:
            sell_px = px * (1 - slip)
            q = min(qty, (cur_val - tgt_val) / px) if w_new > 0 else qty
            proceeds = q * sell_px * (1 - fee)
            cost_part = entry_cost * q / qty
            pnl = proceeds - cost_part
            cash += proceeds
            qty -= q
            entry_cost -= cost_part
            if w_new == 0 or qty < 1e-15:
                trades.append(
                    dict(exit_i=i, pnl=pnl, ret=pnl / cost_part if cost_part else 0, reason=reason)
                )
                qty = 0.0
                entry_cost = 0.0
                stop_lvl = None
                tp_lvl = None
                tstop = None

    for i in range(n):
        if pending is not None:
            cur_w = (qty * o[i]) / (cash + qty * o[i]) if qty > 0 else 0.0
            if pending == 0 and qty > 0:
                trade_to(0.0, o[i], i)
            elif pending > 0 and (qty == 0 or abs(pending - cur_w) > rebalance_band):
                trade_to(pending, o[i], i)
            pending = None
        if qty > 0:
            levels = [x for x in (stop_lvl, tstop) if x is not None]
            sl = max(levels) if levels else None
            if sl is not None and low[i] <= sl:
                trade_to(0.0, min(o[i], sl), i, "stop")
            elif tp_lvl is not None and h[i] >= tp_lvl:
                trade_to(0.0, max(o[i], tp_lvl), i, "take_profit")
        eq[i] = cash + qty * c[i]
        wpos[i] = (qty * c[i]) / eq[i] if eq[i] > 0 else 0
        t = target[i]
        if not np.isnan(t):
            if t <= 0 and qty > 0:
                pending = 0.0
            elif t > 0:
                pending = t
        if trail is not None and qty > 0 and not np.isnan(trail[i]):
            tstop = trail[i] if tstop is None else max(tstop, trail[i])
    out = pd.DataFrame({"equity": eq, "w": wpos}, index=pd.DatetimeIndex(df["close_time"]))
    return out, pd.DataFrame(trades)


def run_multi(prices: dict, weights, band=0.05, fee=None, slip=None):
    """Multi-asset engine. ``weights[t]`` is decided at the close and traded next open.

    The sum of weights must be <= 1. Holdings drift between rebalances.
    """
    fee = FEE if fee is None else fee
    slip = SLIP if slip is None else slip
    opens = prices["open"].values
    closes = prices["close"].values
    n, k = opens.shape
    weights = np.nan_to_num(np.asarray(weights, float))
    cash = 1.0
    qty = np.zeros(k)
    equity = np.empty(n)
    entries = 0
    pending = None
    for i in range(n):
        if pending is not None:
            px = opens[i]
            val = qty * px
            total = cash + val.sum()
            current = val / total
            changed = (
                np.any(np.abs(pending - current) > band)
                or np.any((pending == 0) & (qty > 0))
                or np.any((pending > 0) & (qty == 0))
            )
            if changed:
                for j in range(k):
                    target_value = pending[j] * total
                    if target_value < val[j] - 1e-12 and qty[j] > 0:
                        sell_v = val[j] - target_value if pending[j] > 0 else val[j]
                        cash += sell_v * (1 - slip) * (1 - fee)
                        qty[j] -= sell_v / px[j]
                        if pending[j] == 0:
                            qty[j] = 0.0
                for j in range(k):
                    target_value = pending[j] * total
                    if target_value > val[j] + 1e-12:
                        if qty[j] == 0:
                            entries += 1
                        budget = min(target_value - val[j], cash / (1 + fee))
                        if budget > 0:
                            cash -= budget * (1 + fee)
                            qty[j] += budget / (px[j] * (1 + slip))
            pending = None
        equity[i] = cash + (qty * closes[i]).sum()
        pending = weights[i]
    series = pd.Series(equity, index=pd.DatetimeIndex(prices["close_time"]))
    return series, {"entries": entries}


def metrics(equity: pd.Series) -> dict:
    eq = equity.dropna()
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    max_dd = (eq / eq.cummax() - 1).min()
    sharpe = rets.mean() / rets.std() * np.sqrt(365) if rets.std() > 0 else np.nan
    return {
        "CAGR": float(cagr),
        "MaxDD": float(max_dd),
        "Sharpe": float(sharpe),
        "TotalRet": float(eq.iloc[-1] / eq.iloc[0] - 1),
    }
