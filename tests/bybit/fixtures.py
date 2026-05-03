"""
Bybit API response fixtures for tests.

Two layers:
  RESULT_*  — the 'result' field that BybitRestClient returns after envelope extraction.
              Used directly by parser tests and service mock tests.

  RESPONSE_* — the full Bybit envelope. Used by client-level tests that verify
               the envelope parsing logic itself.

All values are realistic approximations of live Bybit API responses.
Timestamps: 2024-04-25 around 09:25–09:35 UTC in milliseconds.
Prices: BTCUSDT ≈ 60,000 USDT, ETHUSDT ≈ 3,000 USDT.
"""

# ── Timestamps ────────────────────────────────────────────────────────────────
# Three 5-minute kline timestamps, newest-first (as Bybit returns them).
T_NEWEST = 1714000500000   # 2024-04-25 09:35:00 UTC  (potentially open)
T_MID    = 1714000200000   # 2024-04-25 09:30:00 UTC  (closed)
T_OLDEST = 1713999900000   # 2024-04-25 09:25:00 UTC  (closed)

# ── Kline result (what BybitRestClient.get returns) ───────────────────────────
# Format per entry: [startTime_ms, open, high, low, close, volume, turnover]
# List is newest-first as returned by the Bybit API.
KLINE_RESULT = {
    "symbol": "BTCUSDT",
    "category": "spot",
    "list": [
        [str(T_NEWEST), "60200.00", "60250.00", "60150.00", "60220.00", "1.23456", "74271.12"],
        [str(T_MID),    "60050.00", "60210.00", "59980.00", "60200.00", "2.10000", "126210.00"],
        [str(T_OLDEST), "59900.00", "60070.00", "59880.00", "60050.00", "1.50000", "90075.00"],
    ],
}

# ── Ticker result ─────────────────────────────────────────────────────────────
TICKER_RESULT = {
    "category": "spot",
    "list": [
        {
            "symbol": "BTCUSDT",
            "bid1Price": "60210.00",
            "bid1Size": "0.50000",
            "ask1Price": "60220.00",
            "ask1Size": "0.30000",
            "lastPrice": "60215.00",
            "prevPrice24h": "58000.00",
            "price24hPcnt": "0.0382",
            "highPrice24h": "60500.00",
            "lowPrice24h": "57800.00",
            "turnover24h": "1234567.00",
            "volume24h": "20.56",
            "usdIndexPrice": "60215.00",
        }
    ],
}

# ── Instrument filter result ───────────────────────────────────────────────────
# Real Bybit BTCUSDT spot instrument constraints (approximate).
INSTRUMENT_RESULT = {
    "category": "spot",
    "list": [
        {
            "symbol": "BTCUSDT",
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "status": "Trading",
            "lotSizeFilter": {
                "basePrecision": "0.000001",
                "quotePrecision": "0.00000001",
                "minOrderQty": "0.000048",
                "maxOrderQty": "71.73956243",
                "minOrderAmt": "1",
                "maxOrderAmt": "2000000",
            },
            "priceFilter": {
                "tickSize": "0.01",
            },
            "riskParameters": {
                "limitParameter": "0.05",
                "marketParameter": "0.05",
            },
        }
    ],
}

# ── Full API envelopes (for client-level tests) ───────────────────────────────

def wrap_result(result: dict, ret_code: int = 0, ret_msg: str = "OK") -> dict:
    """Wrap a result dict in the standard Bybit API envelope."""
    return {
        "retCode": ret_code,
        "retMsg": ret_msg,
        "result": result,
        "retExtInfo": {},
        "time": T_NEWEST,
    }


KLINE_RESPONSE    = wrap_result(KLINE_RESULT)
TICKER_RESPONSE   = wrap_result(TICKER_RESULT)
INSTRUMENT_RESPONSE = wrap_result(INSTRUMENT_RESULT)

# Error response example (invalid symbol).
ERROR_RESPONSE = wrap_result({}, ret_code=10001, ret_msg="Params errors")
