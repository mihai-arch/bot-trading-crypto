"""
MarketDataService

Responsibility: Fetch and normalize raw market data from Bybit spot API.
Produces typed domain models for consumption by FeatureEngine.

Implemented in v1 (public endpoints, no auth):
    get_klines(symbol, timeframe, limit)     → list[Kline]
    get_ticker(symbol)                       → Ticker
    get_instrument_filter(symbol)            → InstrumentFilter  (cached)

Stubbed for later phases:
    get_orderbook_top(symbol, depth)         → OrderbookTop
    get_recent_trades(symbol, limit)         → list[RecentTrade]
    get_portfolio_state()                    → PortfolioState  (needs paper tracker or auth)
"""

from ..bybit.client import BybitRestClient
from ..bybit.parsers import parse_instrument_filter, parse_klines, parse_ticker
from ..config import BITConfig
from ..domain.enums import Symbol, Timeframe
from ..domain.market import (
    InstrumentFilter,
    Kline,
    OrderbookTop,
    PortfolioState,
    RecentTrade,
    Ticker,
)


class MarketDataService:
    def __init__(self, config: BITConfig) -> None:
        self._config = config
        self._client = BybitRestClient(testnet=config.bybit_testnet)
        # Instrument filters change rarely — cache them for the service lifetime.
        self._instrument_cache: dict[Symbol, InstrumentFilter] = {}

    # ── Implemented ───────────────────────────────────────────────────────────

    async def get_klines(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        limit: int = 200,
    ) -> list[Kline]:
        """
        Fetch recent klines for a symbol and timeframe.

        Returns up to `limit` klines sorted oldest → newest.
        The final entry is marked is_closed=False (it may still be forming).
        Only is_closed=True klines should be used for indicator computation.

        Bybit endpoint: GET /v5/market/kline
        """
        result = await self._client.get(
            "/v5/market/kline",
            params={
                "category": "spot",
                "symbol": str(symbol),
                "interval": str(timeframe),
                "limit": limit,
            },
        )
        return parse_klines(result, symbol, timeframe)

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        """
        Fetch the current best bid/ask and last price.

        Bybit endpoint: GET /v5/market/tickers
        """
        result = await self._client.get(
            "/v5/market/tickers",
            params={
                "category": "spot",
                "symbol": str(symbol),
            },
        )
        return parse_ticker(result, symbol)

    async def get_instrument_filter(self, symbol: Symbol) -> InstrumentFilter:
        """
        Fetch exchange constraints for a symbol.

        Results are cached in-memory for the lifetime of this service instance.
        Call at startup for each symbol and reuse throughout the session.

        Cached fields: tick_size, qty_step, min_order_qty, min_order_usdt.

        Bybit endpoint: GET /v5/market/instruments-info
        """
        if symbol not in self._instrument_cache:
            result = await self._client.get(
                "/v5/market/instruments-info",
                params={
                    "category": "spot",
                    "symbol": str(symbol),
                },
            )
            self._instrument_cache[symbol] = parse_instrument_filter(result, symbol)
        return self._instrument_cache[symbol]

    # ── Stubs (future phases) ─────────────────────────────────────────────────

    async def get_orderbook_top(self, symbol: Symbol, depth: int = 5) -> OrderbookTop:
        """
        Fetch top N bids and asks.

        Not implemented in v1 public endpoint phase.
        Bybit endpoint: GET /v5/market/orderbook
        """
        raise NotImplementedError("get_orderbook_top: not yet implemented")

    async def get_recent_trades(self, symbol: Symbol, limit: int = 50) -> list[RecentTrade]:
        """
        Fetch recent public trades from the exchange tape.

        Not implemented in v1 public endpoint phase.
        Bybit endpoint: GET /v5/market/recent-trade
        """
        raise NotImplementedError("get_recent_trades: not yet implemented")

    async def get_portfolio_state(self) -> PortfolioState:
        """
        Return the current portfolio snapshot.

        Paper mode: requires a paper portfolio tracker (Phase 5).
        Live mode: requires authenticated API access (Phase 7).
        """
        raise NotImplementedError(
            "get_portfolio_state: requires paper tracker (Phase 5) or live auth (Phase 7)"
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call when the service is no longer needed."""
        await self._client.aclose()

    async def __aenter__(self) -> "MarketDataService":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
