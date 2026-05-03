"""
Parser unit tests — pure functions, no HTTP, no mocking.

Each test passes a raw dict directly to a parser and asserts on the
resulting domain model. If a parser breaks, exactly one test here fails.
"""

from datetime import timezone
from decimal import Decimal

import pytest

from bit.bybit.parsers import (
    BybitParseError,
    parse_instrument_filter,
    parse_klines,
    parse_ticker,
)
from bit.domain.enums import Symbol, Timeframe

from .fixtures import (
    INSTRUMENT_RESULT,
    KLINE_RESULT,
    TICKER_RESULT,
    T_MID,
    T_NEWEST,
    T_OLDEST,
)


class TestParseKlines:
    def test_returns_correct_count(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        assert len(klines) == 3

    def test_sorted_oldest_to_newest(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        timestamps = [k.open_time.timestamp() for k in klines]
        assert timestamps == sorted(timestamps)

    def test_oldest_entry_timestamp(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        assert klines[0].open_time.timestamp() == T_OLDEST / 1000

    def test_newest_entry_timestamp(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        assert klines[-1].open_time.timestamp() == T_NEWEST / 1000

    def test_last_kline_is_open(self):
        """Most recent candle is marked potentially open — may still be forming."""
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        assert klines[-1].is_closed is False

    def test_all_but_last_are_closed(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        for k in klines[:-1]:
            assert k.is_closed is True

    def test_oldest_ohlcv_values(self):
        """Verify reversal maps to the correct fixture entry."""
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        oldest = klines[0]
        assert oldest.open  == Decimal("59900.00")
        assert oldest.high  == Decimal("60070.00")
        assert oldest.low   == Decimal("59880.00")
        assert oldest.close == Decimal("60050.00")
        assert oldest.volume == Decimal("1.50000")

    def test_newest_ohlcv_values(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        newest = klines[-1]
        assert newest.open  == Decimal("60200.00")
        assert newest.close == Decimal("60220.00")

    def test_symbol_set_on_all_klines(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        assert all(k.symbol == Symbol.BTCUSDT for k in klines)

    def test_timeframe_set_on_all_klines(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        assert all(k.timeframe == Timeframe.M5 for k in klines)

    def test_timestamps_are_utc(self):
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        for k in klines:
            assert k.open_time.tzinfo == timezone.utc

    def test_five_minute_spacing(self):
        """Adjacent klines should be 300 seconds apart."""
        klines = parse_klines(KLINE_RESULT, Symbol.BTCUSDT, Timeframe.M5)
        gap = klines[1].open_time.timestamp() - klines[0].open_time.timestamp()
        assert gap == 300.0

    def test_empty_list_returns_empty(self):
        result = {"symbol": "BTCUSDT", "category": "spot", "list": []}
        assert parse_klines(result, Symbol.BTCUSDT, Timeframe.M5) == []

    def test_single_kline_is_marked_open(self):
        """With only one kline, it's both first and last — so it's open."""
        result = {
            "list": [
                [str(T_NEWEST), "60200.00", "60250.00", "60150.00", "60220.00", "1.0", "60220.00"]
            ]
        }
        klines = parse_klines(result, Symbol.BTCUSDT, Timeframe.M5)
        assert len(klines) == 1
        assert klines[0].is_closed is False

    def test_missing_list_raises(self):
        with pytest.raises(BybitParseError, match="list"):
            parse_klines({"symbol": "BTCUSDT"}, Symbol.BTCUSDT, Timeframe.M5)

    def test_entry_with_too_few_fields_raises(self):
        bad = {"list": [[str(T_NEWEST), "60000.00"]]}  # Only 2 fields; need ≥ 6.
        with pytest.raises(BybitParseError, match="insufficient"):
            parse_klines(bad, Symbol.BTCUSDT, Timeframe.M5)

    def test_non_numeric_price_raises(self):
        bad = {
            "list": [
                [str(T_NEWEST), "NOT_A_NUMBER", "60250.00", "60150.00", "60220.00", "1.0", "1.0"]
            ]
        }
        with pytest.raises(BybitParseError, match="parse kline"):
            parse_klines(bad, Symbol.BTCUSDT, Timeframe.M5)

    def test_works_for_different_timeframes(self):
        klines_h1 = parse_klines(KLINE_RESULT, Symbol.ETHUSDT, Timeframe.H1)
        assert all(k.timeframe == Timeframe.H1 for k in klines_h1)
        assert all(k.symbol == Symbol.ETHUSDT for k in klines_h1)


class TestParseTicker:
    def test_last_price(self):
        ticker = parse_ticker(TICKER_RESULT, Symbol.BTCUSDT)
        assert ticker.last_price == Decimal("60215.00")

    def test_bid_price(self):
        ticker = parse_ticker(TICKER_RESULT, Symbol.BTCUSDT)
        assert ticker.bid == Decimal("60210.00")

    def test_ask_price(self):
        ticker = parse_ticker(TICKER_RESULT, Symbol.BTCUSDT)
        assert ticker.ask == Decimal("60220.00")

    def test_ask_greater_than_bid(self):
        ticker = parse_ticker(TICKER_RESULT, Symbol.BTCUSDT)
        assert ticker.ask > ticker.bid

    def test_symbol_assigned(self):
        ticker = parse_ticker(TICKER_RESULT, Symbol.BTCUSDT)
        assert ticker.symbol == Symbol.BTCUSDT

    def test_timestamp_is_utc(self):
        ticker = parse_ticker(TICKER_RESULT, Symbol.BTCUSDT)
        assert ticker.timestamp.tzinfo == timezone.utc

    def test_empty_list_raises(self):
        with pytest.raises(BybitParseError, match="empty"):
            parse_ticker({"category": "spot", "list": []}, Symbol.BTCUSDT)

    def test_missing_list_raises(self):
        with pytest.raises(BybitParseError, match="list"):
            parse_ticker({"category": "spot"}, Symbol.BTCUSDT)

    def test_missing_last_price_raises(self):
        bad = {"list": [{"bid1Price": "60210.00", "ask1Price": "60220.00"}]}
        with pytest.raises(BybitParseError):
            parse_ticker(bad, Symbol.BTCUSDT)

    def test_missing_bid_price_raises(self):
        bad = {"list": [{"lastPrice": "60215.00", "ask1Price": "60220.00"}]}
        with pytest.raises(BybitParseError):
            parse_ticker(bad, Symbol.BTCUSDT)

    def test_non_numeric_price_raises(self):
        bad = {
            "list": [{
                "lastPrice": "INVALID",
                "bid1Price": "60210.00",
                "ask1Price": "60220.00",
            }]
        }
        with pytest.raises(BybitParseError, match="parse ticker"):
            parse_ticker(bad, Symbol.BTCUSDT)


class TestParseInstrumentFilter:
    def test_tick_size(self):
        f = parse_instrument_filter(INSTRUMENT_RESULT, Symbol.BTCUSDT)
        assert f.tick_size == Decimal("0.01")

    def test_qty_step_from_base_precision(self):
        f = parse_instrument_filter(INSTRUMENT_RESULT, Symbol.BTCUSDT)
        assert f.qty_step == Decimal("0.000001")

    def test_min_order_qty(self):
        f = parse_instrument_filter(INSTRUMENT_RESULT, Symbol.BTCUSDT)
        assert f.min_order_qty == Decimal("0.000048")

    def test_min_order_usdt_from_min_order_amt(self):
        f = parse_instrument_filter(INSTRUMENT_RESULT, Symbol.BTCUSDT)
        assert f.min_order_usdt == Decimal("1")

    def test_symbol_assigned(self):
        f = parse_instrument_filter(INSTRUMENT_RESULT, Symbol.BTCUSDT)
        assert f.symbol == Symbol.BTCUSDT

    def test_all_values_positive(self):
        f = parse_instrument_filter(INSTRUMENT_RESULT, Symbol.BTCUSDT)
        assert f.tick_size > 0
        assert f.qty_step > 0
        assert f.min_order_qty > 0
        assert f.min_order_usdt > 0

    def test_empty_list_raises(self):
        with pytest.raises(BybitParseError, match="empty"):
            parse_instrument_filter({"list": []}, Symbol.BTCUSDT)

    def test_missing_list_raises(self):
        with pytest.raises(BybitParseError, match="list"):
            parse_instrument_filter({}, Symbol.BTCUSDT)

    def test_missing_price_filter_raises(self):
        bad = {
            "list": [{
                "symbol": "BTCUSDT",
                "lotSizeFilter": {
                    "basePrecision": "0.000001",
                    "minOrderQty": "0.000048",
                    "minOrderAmt": "1",
                },
                # No priceFilter
            }]
        }
        with pytest.raises(BybitParseError, match="instrument filter"):
            parse_instrument_filter(bad, Symbol.BTCUSDT)

    def test_missing_lot_size_filter_raises(self):
        bad = {
            "list": [{
                "symbol": "BTCUSDT",
                "priceFilter": {"tickSize": "0.01"},
                # No lotSizeFilter
            }]
        }
        with pytest.raises(BybitParseError, match="instrument filter"):
            parse_instrument_filter(bad, Symbol.BTCUSDT)
