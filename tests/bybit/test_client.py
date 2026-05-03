"""
BybitRestClient tests using httpx.MockTransport.

Tests verify:
- Successful responses return the extracted 'result' dict.
- Non-zero retCode raises BybitAPIError with correct attributes.
- HTTP 4xx/5xx raises BybitNetworkError.
- Timeout raises BybitNetworkError.
- Invalid JSON raises BybitNetworkError.
- Request parameters reach the transport as expected.

No real network calls are made. MockTransport is injected via _transport kwarg.
"""

import httpx
import pytest

from bit.bybit.client import BybitAPIError, BybitNetworkError, BybitRestClient

from .fixtures import (
    ERROR_RESPONSE,
    INSTRUMENT_RESPONSE,
    KLINE_RESPONSE,
    TICKER_RESPONSE,
)


# ── Transport helpers ─────────────────────────────────────────────────────────

def _transport_for(responses: list) -> httpx.MockTransport:
    """
    Create a MockTransport that serves responses from the list in order.
    Each element is either:
      - (int status, dict body) → JSON response
      - (int status, str body) → plain text response
      - An exception class/instance to raise
    """
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(it)
        if isinstance(item, tuple):
            status, body = item
            if isinstance(body, dict):
                return httpx.Response(status, json=body)
            return httpx.Response(status, content=body.encode("utf-8"))
        raise item  # Allow raising exceptions from the transport layer.

    return httpx.MockTransport(handler)


def _capturing_transport() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Return a transport that records all requests it receives."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=KLINE_RESPONSE)

    return httpx.MockTransport(handler), captured


# ── Success cases ─────────────────────────────────────────────────────────────

class TestSuccessfulRequests:
    async def test_returns_result_field(self):
        transport = _transport_for([(200, KLINE_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            result = await client.get("/v5/market/kline", params={"symbol": "BTCUSDT"})
        assert result == KLINE_RESPONSE["result"]

    async def test_ticker_result_returned(self):
        transport = _transport_for([(200, TICKER_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            result = await client.get("/v5/market/tickers", params={"symbol": "BTCUSDT"})
        assert result == TICKER_RESPONSE["result"]

    async def test_instrument_result_returned(self):
        transport = _transport_for([(200, INSTRUMENT_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            result = await client.get("/v5/market/instruments-info", params={"symbol": "BTCUSDT"})
        assert result == INSTRUMENT_RESPONSE["result"]

    async def test_params_reach_transport(self):
        transport, captured = _capturing_transport()
        async with BybitRestClient(_transport=transport) as client:
            await client.get("/v5/market/kline", params={"symbol": "BTCUSDT", "interval": "5"})
        assert len(captured) == 1
        params = dict(captured[0].url.params)
        assert params["symbol"] == "BTCUSDT"
        assert params["interval"] == "5"

    async def test_int_params_sent_as_strings(self):
        """httpx converts int params to strings; verify they arrive correctly."""
        transport, captured = _capturing_transport()
        async with BybitRestClient(_transport=transport) as client:
            await client.get("/v5/market/kline", params={"limit": 50})
        assert dict(captured[0].url.params)["limit"] == "50"

    async def test_testnet_base_url(self):
        transport, captured = _capturing_transport()
        async with BybitRestClient(testnet=True, _transport=transport) as client:
            await client.get("/v5/market/kline", params={})
        assert "testnet" in captured[0].url.host

    async def test_mainnet_base_url(self):
        transport, captured = _capturing_transport()
        async with BybitRestClient(testnet=False, _transport=transport) as client:
            await client.get("/v5/market/kline", params={})
        assert "testnet" not in captured[0].url.host


# ── Bybit API error cases ─────────────────────────────────────────────────────

class TestBybitAPIErrors:
    async def test_non_zero_ret_code_raises(self):
        transport = _transport_for([(200, ERROR_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitAPIError) as exc_info:
                await client.get("/v5/market/kline", params={})
        assert exc_info.value.ret_code == 10001

    async def test_error_message_preserved(self):
        transport = _transport_for([(200, ERROR_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitAPIError) as exc_info:
                await client.get("/v5/market/kline", params={})
        assert "Params errors" in exc_info.value.message

    async def test_endpoint_included_in_error(self):
        transport = _transport_for([(200, ERROR_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitAPIError) as exc_info:
                await client.get("/v5/market/kline", params={})
        assert "/v5/market/kline" in exc_info.value.endpoint


# ── Network / HTTP error cases ────────────────────────────────────────────────

class TestNetworkErrors:
    async def test_http_500_raises_network_error(self):
        transport = _transport_for([(500, '{"error": "internal server error"}')])
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitNetworkError, match="500"):
                await client.get("/v5/market/kline", params={})

    async def test_http_503_raises_network_error(self):
        transport = _transport_for([(503, '{"error": "service unavailable"}')])
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitNetworkError, match="503"):
                await client.get("/v5/market/kline", params={})

    async def test_http_404_raises_network_error(self):
        transport = _transport_for([(404, '{"error": "not found"}')])
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitNetworkError, match="404"):
                await client.get("/v5/market/kline", params={})

    async def test_invalid_json_raises_network_error(self):
        transport = _transport_for([(200, "this is not json at all ]]][")])
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitNetworkError, match="JSON"):
                await client.get("/v5/market/kline", params={})

    async def test_timeout_raises_network_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("simulated timeout", request=request)

        transport = httpx.MockTransport(handler)
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitNetworkError, match="timed out"):
                await client.get("/v5/market/kline", params={})

    async def test_connect_error_raises_network_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        transport = httpx.MockTransport(handler)
        async with BybitRestClient(_transport=transport) as client:
            with pytest.raises(BybitNetworkError, match="Network error"):
                await client.get("/v5/market/kline", params={})


# ── Context manager ───────────────────────────────────────────────────────────

class TestContextManager:
    async def test_async_context_manager_returns_client(self):
        transport = _transport_for([(200, KLINE_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            assert isinstance(client, BybitRestClient)

    async def test_client_closes_on_exit(self):
        transport = _transport_for([(200, KLINE_RESPONSE)])
        async with BybitRestClient(_transport=transport) as client:
            pass
        # Verify the underlying httpx client is closed.
        assert client._http.is_closed
