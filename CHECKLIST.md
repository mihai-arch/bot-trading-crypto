# BIT — Implementation Checklist

Next tasks in priority order. Update this file as work progresses.

---

## Now: Phase 2 — Market Data

### MarketDataService

- [ ] Add `httpx.AsyncClient` to `MarketDataService.__init__`
- [ ] Implement `get_klines()` — GET `/v5/market/kline`, map response to `list[Kline]`
- [ ] Implement `get_ticker()` — GET `/v5/market/tickers`, map to `Ticker`
- [ ] Implement `get_orderbook_top()` — GET `/v5/market/orderbook`, map to `OrderbookTop`
- [ ] Implement `get_recent_trades()` — GET `/v5/market/recent-trade`, map to `list[RecentTrade]`
- [ ] Implement `get_instrument_filter()` — GET `/v5/market/instruments-info`, cache result
- [ ] Implement `get_portfolio_state()` — paper: return in-memory state tracker
- [ ] Add retry logic for 429 (rate limit) and transient 5xx errors
- [ ] Write unit tests with `httpx.MockTransport` or `respx`

---

## Next: Phase 3 — Feature Engine

### FeatureEngine

- [ ] Implement `_compute_timeframe_features()`:
  - EMA(9) and EMA(21) from close prices
  - RSI(14) from close prices
  - ATR(14) from high/low/close
  - Volume SMA(20)
  - 20-bar high and 20-bar low
- [ ] Implement `compute()` — call per timeframe, assemble `FeatureSet`
- [ ] Validate: need at least 21 klines for all indicators (handle insufficient data case)
- [ ] Write unit tests with deterministic kline sequences

---

## Then: Phase 4 — Strategies

### TrendContinuationStrategy

- [ ] Define scoring weights for each condition
- [ ] H1 condition: EMA_fast > EMA_slow (trend direction)
- [ ] M15 condition: RSI > 50 (momentum not weak)
- [ ] M5 condition: close within N% of EMA_fast (pullback zone)
- [ ] M5 condition: volume > volume_ma (participation)
- [ ] Return weighted score and rationale string
- [ ] Unit tests: feature sets designed to hit each condition

### BreakoutConfirmationStrategy

- [ ] Define scoring weights for each condition
- [ ] H1 condition: high_20 - low_20 < X% of price (consolidation)
- [ ] M15 condition: close > high_20 (breakout)
- [ ] M15 condition: volume > 1.5 * volume_ma (confirmation)
- [ ] M15 condition: RSI between 55–75 (not overbought)
- [ ] Return weighted score and rationale string
- [ ] Unit tests: feature sets designed to hit each condition

---

## Then: Phase 5 — Integration

- [ ] Paper portfolio state tracker (tracks simulated balance and positions)
- [ ] Wire `Pipeline` with all real service instances
- [ ] Add symbol iteration loop
- [ ] End-to-end integration test with injected mock data
- [ ] Verify journal writes and reads back correctly

---

## Notes

- All thresholds in `.env` / `BITConfig` — no magic numbers in strategy code
- Every strategy condition should produce a log line (use `rationale` field)
- Do not start Phase 4 until Phase 3 indicators are unit-tested
- Do not start live trading until Phase 6 (30+ days paper) is complete
