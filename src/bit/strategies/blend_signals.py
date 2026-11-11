"""
Closed-bar signals for the paper blend.

Two sleeves, evaluated only on bars that have already closed:

- Core rotation: among BTC, ETH and SOL, hold the coin with the highest
  trailing return. Stay in cash when every return is <= 0.
- Conservative: long a coin when EMA(fast) > EMA(slow), sized by
  min(1, vol_target / realized_vol). Otherwise weight 0.

EMA is the recursive form used by pandas ``ewm(span, adjust=False)``:
the first value is the first price, then
``y = alpha * price + (1 - alpha) * y_prev`` with ``alpha = 2 / (span + 1)``.

Realized vol is the sample standard deviation (ddof=1) of log returns over
``vol_n`` bars, annualised by ``sqrt(bars_per_year)``. That matches
``pandas.Series.rolling(n).std()``.

The functions return the weight decided at the close. The caller fills at
the next bar's open. Nothing here reads the current forming candle.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def ema_series(values: Sequence[float], span: int) -> list[float]:
    """Recursive EMA. ``values`` must be non-empty. Span must be >= 1."""
    if span < 1:
        raise ValueError(f"EMA span must be >= 1, got {span}.")
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    one_minus = 1.0 - alpha
    out: list[float] = []
    prev = float(values[0])
    out.append(prev)
    for price in values[1:]:
        prev = alpha * float(price) + one_minus * prev
        out.append(prev)
    return out


def _sample_std(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return float("nan")
    mean = math.fsum(values) / n
    var = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def realized_vol_at(
    closes: Sequence[float], index: int, window: int, bars_per_year: float
) -> float:
    """
    Annualised sample vol of the ``window`` log returns ending at ``index``.

    Returns NaN when there is not enough history or a price is non-positive.
    """
    if window < 2 or index < window or index >= len(closes):
        return float("nan")
    rets: list[float] = []
    for k in range(index - window + 1, index + 1):
        prev = float(closes[k - 1])
        price = float(closes[k])
        if prev <= 0.0 or price <= 0.0:
            return float("nan")
        rets.append(math.log(price / prev))
    return _sample_std(rets) * math.sqrt(bars_per_year)


def conservative_weight_series(
    closes: Sequence[float],
    *,
    fast: int = 20,
    slow: int = 100,
    vol_target: float = 0.40,
    vol_n: int = 180,
    bars_per_year: float = 2190.0,
) -> list[float]:
    """
    Target weight in [0, 1] at each close.

    Weight is 0 until ``slow`` closes exist, when the fast EMA is not above
    the slow EMA, or when realized vol is not yet defined. Otherwise
    ``min(1, vol_target / vol)``.
    """
    if fast < 1 or slow < 2:
        raise ValueError(f"Invalid EMA spans fast={fast} slow={slow}.")
    if not closes:
        return []
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    weights: list[float] = []
    for i in range(len(closes)):
        # expanding().count() < slow  →  the first slow-1 bars stay flat.
        if (i + 1) < slow or ema_fast[i] <= ema_slow[i]:
            weights.append(0.0)
            continue
        vol = realized_vol_at(closes, i, vol_n, bars_per_year)
        if not math.isfinite(vol) or vol <= 0.0:
            weights.append(0.0)
            continue
        weights.append(min(1.0, vol_target / vol))
    return weights


def conservative_target_weight(
    closes: Sequence[float],
    *,
    fast: int = 20,
    slow: int = 100,
    vol_target: float = 0.40,
    vol_n: int = 180,
    bars_per_year: float = 2190.0,
) -> float:
    """Weight decided by the last close. 0 when history is too short."""
    series = conservative_weight_series(
        closes,
        fast=fast,
        slow=slow,
        vol_target=vol_target,
        vol_n=vol_n,
        bars_per_year=bars_per_year,
    )
    return series[-1] if series else 0.0


def trailing_return(closes: Sequence[float], lookback: int) -> float | None:
    """Close-to-close return over ``lookback`` bars. None if history is short."""
    if lookback < 1 or len(closes) <= lookback:
        return None
    prev = float(closes[-1 - lookback])
    last = float(closes[-1])
    if prev <= 0.0:
        return None
    return last / prev - 1.0


def choose_rotation(
    closes: dict[str, Sequence[float]],
    lookback: int,
) -> tuple[str | None, dict[str, float | None]]:
    """
    Pick the symbol with the highest trailing return, or None when every
    return is missing or <= 0.

    Ties go to the symbol that appears first in ``closes``. Callers should
    pass symbols in a stable order (BTC, ETH, SOL).
    """
    returns = {symbol: trailing_return(series, lookback) for symbol, series in closes.items()}
    winner: str | None = None
    best = 0.0
    for symbol in closes:
        ret = returns[symbol]
        if ret is not None and ret > best:
            best = ret
            winner = symbol
    return winner, returns


def rotation_weight_series(
    closes: dict[str, Sequence[float]],
    lookback: int,
) -> dict[str, list[float]]:
    """
    Per-bar rotation weights. At each index the leader with a positive
    trailing return gets 1 and the others get 0. Early bars (before
    ``lookback``) are cash.

    All series must have the same length and represent the same bar times.
    """
    if not closes:
        return {}
    symbols = list(closes)
    length = len(closes[symbols[0]])
    for symbol in symbols[1:]:
        if len(closes[symbol]) != length:
            raise ValueError("Rotation series must be aligned to the same length.")
    weights = {symbol: [0.0] * length for symbol in symbols}
    for i in range(lookback, length):
        window = {symbol: closes[symbol][: i + 1] for symbol in symbols}
        winner, _ = choose_rotation(window, lookback)
        if winner is not None:
            weights[winner][i] = 1.0
    return weights
