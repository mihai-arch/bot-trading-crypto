"""
MarketDataService tests — service-level behaviour with mocked HTTP client.

Strategy: mock BybitRestClient.get() with AsyncMock so tests never hit the network.
The mock returns the 'result' dict (what the real client returns after envelope extraction).

These tests verify:
- Service calls client with correct endpoint and parameters.
- Service passes parsed domain models through correctly.
- Instrument filter caching works.
- Unimplemented methods still raise NotImplementedError.
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from bit.bybit.client import BybitAPIError, BybitNetworkError
from bit.bybit.parsers import BybitParseError
from bit.domain.enums import Symbol, Timeframe
from bit.domain.market import InstrumentFilter, Kline, Ticker
from bit.services.market_data import MarketDataService

from tests.bybit.fixtures import INSTRUMENT_RESULT, KLINE_RESULT, TICKER_RESULT


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _service_with_mock(config, mock_result: dict | None = None) -> tuple[MarketDataService, AsyncMock]:
    """Create a MarketDataService with its HTTP client mocked."""
    service = MarketDataService(config)
    mock_get = AsyncMock(return_value=mock_result or {})
    service._client.get = mock_get
    return service, mock_get


# ── get_klines ────────────────────────────────────────────────────────────────

class TestGetKlines:
    async def test_returns_list_of_klines(self, config):
        service, _ = _service_with_mock(config, KLINE_RESULT)
        result = await service.get_klines(Symbol.BTCUSDT, Timeframe.M5, limit=3)
        assert isinstance(result, list)
        assert all(isinstance(k, Kline) for k in result)

    async def test_correct_count(self, config):
        service, _ = _service_with_mock(config, KLINE_RESULT)
        result = await service.get_klines(Symbol.BTCUSDT, Timeframe.M5, limit=3)
        assert len(result) == 3

    async def test_calls_correct_endpoint(self, config):
        service, mock_get = _service_with_mock(config, KLINE_RESULT)
        await service.get_klines(Symbol.BTCUSDT, Timeframe.M5, limit=50)
        mock_get.assert_called_once()
        endpoint = mock_get.call_args[0][0]
        assert endpoint == "/v5/market/kline"

    async def test_sends_spot_category(self, config):
        service, mock_get = _service_with_mock(config, KLINE_RESULT)
        await service.get_klines(Symbol.BTCUSDT, Timeframe.M5)
        params = mock_get.call_args[1]["params"]
        assert params["category"] == "spot"

    async def test_sends_correct_symbol(self, config):
        service, mock_get = _service_with_mock(config, KLINE_RESULT)
        await service.get_klines(Symbol.ETHUSDT, Timeframe.H1)
        params = mock_get.call_args[1]["params"]
        assert params["symbol"] == "ETHUSDT"

    async def test_sends_correct_interval(self, config):
        service, mock_get = _service_with_mock(config, KLINE_RESULT)
        await service.get_klines(Symbol.BTCUSDT, Timeframe.H1)
        params = mock_get.call_args[1]["params"]
        assert params["interval"] == "60"

    async def test_sends_limit(self, config):
        service, mock_get = _service_with_mock(config, KLINE_RESULT)
        await service.get_klines(Symbol.BTCUSDT, Timeframe.M5, limit=100)
        params = mock_get.call_args[1]["params"]
        assert params["limit"] == 100

    async def test_oldest_to_newest_order(self, config):
        service, _ = _service_with_mock(config, KLINE_RESULT)
        klines = await service.get_klines(Symbol.BTCUSDT, Timeframe.M5)
        timestamps = [k.open_time.timestamp() for k in klines]
        assert timestamps == sorted(timestamps)

    async def test_last_kline_marked_open(self, config):
        service, _ = _service_with_mock(config, KLINE_RESULT)
        klines = await service.get_klines(Symbol.BTCUSDT, Timeframe.M5)
        assert klines[-1].is_closed is False

    async def test_propagates_api_error(self, config):
        service, mock_get = _service_with_mock(config)
        mock_get.side_effect = BybitAPIError(10001, "Params error", "/v5/market/kline")
        with pytest.raises(BybitAPIError):
            await service.get_klines(Symbol.BTCUSDT, Timeframe.M5)

    async def test_propagates_network_error(self, config):
        service, mock_get = _service_with_mock(config)
        mock_get.side_effect = BybitNetworkError("timeout")
        with pytest.raises(BybitNetworkError):
            await service.get_klines(Symbol.BTCUSDT, Timeframe.M5)


# ── get_ticker ────────────────────────────────────────────────────────────────

class TestGetTicker:
    async def test_returns_ticker(self, config):
        service, _ = _service_with_mock(config, TICKER_RESULT)
        result = await service.get_ticker(Symbol.BTCUSDT)
        assert isinstance(result, Ticker)

    async def test_last_price_correct(self, config):
        service, _ = _service_with_mock(config, TICKER_RESULT)
        ticker = await service.get_ticker(Symbol.BTCUSDT)
        assert ticker.last_price == Decimal("60215.00")

    async def test_bid_ask_correct(self, config):
        service, _ = _service_with_mock(config, TICKER_RESULT)
        ticker = await service.get_ticker(Symbol.BTCUSDT)
        assert ticker.bid == Decimal("60210.00")
        assert ticker.ask == Decimal("60220.00")

    async def test_calls_correct_endpoint(self, config):
        service, mock_get = _service_with_mock(config, TICKER_RESULT)
        await service.get_ticker(Symbol.BTCUSDT)
        endpoint = mock_get.call_args[0][0]
        assert endpoint == "/v5/market/tickers"

    async def test_sends_spot_category(self, config):
        service, mock_get = _service_with_mock(config, TICKER_RESULT)
        await service.get_ticker(Symbol.ETHUSDT)
        params = mock_get.call_args[1]["params"]
        assert params["category"] == "spot"

    async def test_sends_correct_symbol(self, config):
        service, mock_get = _service_with_mock(config, TICKER_RESULT)
        await service.get_ticker(Symbol.SOLUSDT)
        params = mock_get.call_args[1]["params"]
        assert params["symbol"] == "SOLUSDT"

    async def test_symbol_on_returned_ticker(self, config):
        service, _ = _service_with_mock(config, TICKER_RESULT)
        ticker = await service.get_ticker(Symbol.BTCUSDT)
        assert ticker.symbol == Symbol.BTCUSDT


# ── get_instrument_filter ─────────────────────────────────────────────────────

class TestGetInstrumentFilter:
    async def test_returns_instrument_filter(self, config):
        service, _ = _service_with_mock(config, INSTRUMENT_RESULT)
        result = await service.get_instrument_filter(Symbol.BTCUSDT)
        assert isinstance(result, InstrumentFilter)

    async def test_tick_size_correct(self, config):
        service, _ = _service_with_mock(config, INSTRUMENT_RESULT)
        f = await service.get_instrument_filter(Symbol.BTCUSDT)
        assert f.tick_size == Decimal("0.01")

    async def test_qty_step_correct(self, config):
        service, _ = _service_with_mock(config, INSTRUMENT_RESULT)
        f = await service.get_instrument_filter(Symbol.BTCUSDT)
        assert f.qty_step == Decimal("0.000001")

    async def test_min_order_usdt_correct(self, config):
        service, _ = _service_with_mock(config, INSTRUMENT_RESULT)
        f = await service.get_instrument_filter(Symbol.BTCUSDT)
        assert f.min_order_usdt == Decimal("1")

    async def test_calls_correct_endpoint(self, config):
        service, mock_get = _service_with_mock(config, INSTRUMENT_RESULT)
        await service.get_instrument_filter(Symbol.BTCUSDT)
        endpoint = mock_get.call_args[0][0]
        assert endpoint == "/v5/market/instruments-info"

    async def test_sends_spot_category(self, config):
        service, mock_get = _service_with_mock(config, INSTRUMENT_RESULT)
        await service.get_instrument_filter(Symbol.BTCUSDT)
        params = mock_get.call_args[1]["params"]
        assert params["category"] == "spot"

    async def test_result_cached_on_second_call(self, config):
        """Second call for same symbol must not hit the client again."""
        service, mock_get = _service_with_mock(config, INSTRUMENT_RESULT)
        await service.get_instrument_filter(Symbol.BTCUSDT)
        await service.get_instrument_filter(Symbol.BTCUSDT)
        assert mock_get.call_count == 1

    async def test_different_symbols_not_cached_together(self, config):
        """Cache is per-symbol — different symbols require separate fetches."""
        service, mock_get = _service_with_mock(config, INSTRUMENT_RESULT)
        await service.get_instrument_filter(Symbol.BTCUSDT)
        await service.get_instrument_filter(Symbol.ETHUSDT)
        assert mock_get.call_count == 2

    async def test_cached_value_is_correct_type(self, config):
        service, _ = _service_with_mock(config, INSTRUMENT_RESULT)
        first  = await service.get_instrument_filter(Symbol.BTCUSDT)
        second = await service.get_instrument_filter(Symbol.BTCUSDT)
        assert first is second  # Same object returned from cache.


# ── Unimplemented stubs ───────────────────────────────────────────────────────

class TestUnimplementedMethods:
    async def test_get_orderbook_top_raises(self, config):
        service = MarketDataService(config)
        with pytest.raises(NotImplementedError):
            await service.get_orderbook_top(Symbol.BTCUSDT)

    async def test_get_recent_trades_raises(self, config):
        service = MarketDataService(config)
        with pytest.raises(NotImplementedError):
            await service.get_recent_trades(Symbol.BTCUSDT)

    async def test_get_portfolio_state_raises(self, config):
        service = MarketDataService(config)
        with pytest.raises(NotImplementedError):
            await service.get_portfolio_state()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

class TestLifecycle:
    async def test_context_manager_closes_client(self, config):
        async with MarketDataService(config) as service:
            assert isinstance(service, MarketDataService)
        assert service._client._http.is_closed
