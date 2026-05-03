"""
Bybit v5 API response parsers.

Pure functions: raw API result dict → typed domain model.
No HTTP, no I/O, no state. Fully unit-testable with plain Python dicts.

Each parser receives the 'result' field that BybitRestClient already extracted
from the response envelope. Callers never see retCode or retMsg here.

Bybit v5 field mapping reference (spot category):
  GET /v5/market/kline
    result.list[][0]  startTime (ms)
    result.list[][1]  open
    result.list[][2]  high
    result.list[][3]  low
    result.list[][4]  close
    result.list[][5]  volume (base asset)
    result.list[][6]  turnover (quote asset)
    NOTE: list is newest-first. We reverse to oldest-first.

  GET /v5/market/tickers (spot)
    result.list[0].lastPrice
    result.list[0].bid1Price
    result.list[0].ask1Price

  GET /v5/market/instruments-info (spot)
    result.list[0].priceFilter.tickSize
    result.list[0].lotSizeFilter.basePrecision   → qty_step
    result.list[0].lotSizeFilter.minOrderQty
    result.list[0].lotSizeFilter.minOrderAmt     → min_order_usdt
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from ..domain.enums import Symbol, Timeframe
from ..domain.market import InstrumentFilter, Kline, Ticker


class BybitParseError(ValueError):
    """Raised when a Bybit API result dict cannot be mapped to a domain model."""


# ── Klines ────────────────────────────────────────────────────────────────────

def parse_klines(result: dict, symbol: Symbol, timeframe: Timeframe) -> list[Kline]:
    """
    Parse GET /v5/market/kline result into a sorted list of Kline.

    Returns:
        Klines sorted oldest → newest. The last entry (most recent) is
        marked is_closed=False because it may still be forming.
        All other entries are marked is_closed=True.
        Returns [] if the result list is empty.

    Raises:
        BybitParseError: If the result structure is unexpected or a field
            cannot be converted to the expected type.
    """
    try:
        raw_list: list = result["list"]
    except KeyError:
        raise BybitParseError("Kline result missing 'list' field")

    if not raw_list:
        return []

    klines: list[Kline] = []
    for i, entry in enumerate(raw_list):
        if len(entry) < 6:
            raise BybitParseError(
                f"Kline entry at index {i} has insufficient fields "
                f"(need ≥6, got {len(entry)}): {entry}"
            )
        try:
            open_time = datetime.fromtimestamp(int(entry[0]) / 1000, tz=timezone.utc)
            klines.append(
                Kline(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=Decimal(entry[1]),
                    high=Decimal(entry[2]),
                    low=Decimal(entry[3]),
                    close=Decimal(entry[4]),
                    volume=Decimal(entry[5]),
                    is_closed=True,  # Fixed up for the last entry after reversal below.
                )
            )
        except (ValueError, InvalidOperation) as exc:
            raise BybitParseError(
                f"Failed to parse kline entry at index {i}: {exc}"
            ) from exc

    # Bybit returns newest-first. Reverse to oldest → newest.
    klines.reverse()

    # The last kline after reversal is the most recent. It may still be forming.
    # Mark it open so FeatureEngine knows not to use it for closed-candle logic.
    klines[-1] = klines[-1].model_copy(update={"is_closed": False})

    return klines


# ── Ticker ────────────────────────────────────────────────────────────────────

def parse_ticker(result: dict, symbol: Symbol) -> Ticker:
    """
    Parse GET /v5/market/tickers result into a Ticker.

    The Bybit spot ticker does not include a per-item timestamp.
    We use datetime.now(utc) as the fetch time — it represents when we
    observed this data, which is the semantically correct value.

    Raises:
        BybitParseError: If required fields are missing or malformed.
    """
    try:
        ticker_list: list = result["list"]
    except KeyError:
        raise BybitParseError("Ticker result missing 'list' field")

    if not ticker_list:
        raise BybitParseError(f"Ticker list is empty for {symbol}")

    t = ticker_list[0]
    try:
        return Ticker(
            symbol=symbol,
            last_price=Decimal(t["lastPrice"]),
            bid=Decimal(t["bid1Price"]),
            ask=Decimal(t["ask1Price"]),
            timestamp=datetime.now(tz=timezone.utc),
        )
    except (KeyError, InvalidOperation) as exc:
        raise BybitParseError(f"Failed to parse ticker fields for {symbol}: {exc}") from exc


# ── Instrument filter ─────────────────────────────────────────────────────────

def parse_instrument_filter(result: dict, symbol: Symbol) -> InstrumentFilter:
    """
    Parse GET /v5/market/instruments-info result into an InstrumentFilter.

    Fields extracted:
        priceFilter.tickSize         → tick_size
        lotSizeFilter.basePrecision  → qty_step
        lotSizeFilter.minOrderQty    → min_order_qty
        lotSizeFilter.minOrderAmt    → min_order_usdt

    Raises:
        BybitParseError: If required fields are missing or malformed.
    """
    try:
        items: list = result["list"]
    except KeyError:
        raise BybitParseError("Instrument result missing 'list' field")

    if not items:
        raise BybitParseError(f"Instrument list is empty for {symbol}")

    item = items[0]
    try:
        lot = item["lotSizeFilter"]
        price = item["priceFilter"]
        return InstrumentFilter(
            symbol=symbol,
            tick_size=Decimal(price["tickSize"]),
            qty_step=Decimal(lot["basePrecision"]),
            min_order_qty=Decimal(lot["minOrderQty"]),
            min_order_usdt=Decimal(lot["minOrderAmt"]),
        )
    except (KeyError, InvalidOperation) as exc:
        raise BybitParseError(
            f"Failed to parse instrument filter fields for {symbol}: {exc}"
        ) from exc
