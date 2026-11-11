"""A risk rejection is journaled as REJECT before any fill is applied."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from bit.config import BITConfig
from bit.domain.decisions import Decision
from bit.domain.enums import DecisionState, StrategyId, Symbol, Timeframe
from bit.domain.market import InstrumentFilter, Kline, Ticker
from bit.domain.risk import SizingResult
from bit.domain.signals import AggregatedSignal, Signal
from bit.pipeline import Pipeline
from bit.services.paper_portfolio import PaperPortfolioTracker

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _pipeline(config, portfolio, sizing, journal):
    market = AsyncMock()
    market.get_klines.return_value = [
        Kline(
            symbol=Symbol.BTCUSDT,
            timeframe=Timeframe.M5,
            open_time=_NOW,
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            is_closed=True,
        )
    ]
    market.get_ticker.return_value = Ticker(
        symbol=Symbol.BTCUSDT,
        last_price=Decimal("100"),
        bid=Decimal("99"),
        ask=Decimal("101"),
        timestamp=_NOW,
    )
    market.get_instrument_filter.return_value = InstrumentFilter(
        symbol=Symbol.BTCUSDT,
        tick_size=Decimal("0.01"),
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        min_order_usdt=Decimal("1"),
    )
    signal = Signal(
        strategy_id=StrategyId.TREND_CONTINUATION,
        symbol=Symbol.BTCUSDT,
        timestamp=_NOW,
        score=Decimal("0.9"),
        rationale="enter",
    )
    agg = AggregatedSignal(
        symbol=Symbol.BTCUSDT,
        timestamp=_NOW,
        all_signals=[signal],
        selected=signal,
        candidate_count=1,
        rationale="selected",
    )
    signals = MagicMock()
    signals.evaluate.return_value = agg
    decisions = MagicMock()
    decisions.decide.return_value = Decision(
        symbol=Symbol.BTCUSDT,
        timestamp=_NOW,
        state=DecisionState.ENTER,
        composite_score=Decimal("0.9"),
        contributing_strategies=[StrategyId.TREND_CONTINUATION],
        rationale="signal says enter",
        suggested_entry_price=Decimal("100"),
    )
    risk = MagicMock()
    risk.approve.return_value = sizing
    execution = AsyncMock()
    return Pipeline(
        config=config,
        market_data=market,
        feature_engine=MagicMock(),
        signal_engine=signals,
        decision_engine=decisions,
        risk_engine=risk,
        execution_engine=execution,
        journal=journal,
        portfolio_tracker=portfolio,
    )


async def test_risk_rejection_is_journaled_and_not_filled(tmp_path):
    config = BITConfig(
        bybit_api_key="",
        bybit_api_secret="",
        paper_trading=True,
        portfolio_state_path=tmp_path / "p.json",
        runner_state_path=tmp_path / "r.json",
    )
    portfolio = PaperPortfolioTracker(Decimal("1000"))
    journal = MagicMock()
    sizing = SizingResult(
        symbol=Symbol.BTCUSDT,
        approved=False,
        qty=Decimal("0"),
        notional_usdt=Decimal("0"),
        entry_price=Decimal("100"),
        rejection_reason="daily loss limit",
    )
    pipeline = _pipeline(config, portfolio, sizing, journal)
    entry = await pipeline.run(Symbol.BTCUSDT)
    assert entry.decision_state == DecisionState.REJECT
    assert "daily loss limit" in entry.rationale
    pipeline._execution.execute.assert_not_called()
    assert portfolio.position_count == 0
    journal.record.assert_called_once()
    # History request is at least 1000 candles.
    limit = pipeline._market_data.get_klines.await_args.kwargs["limit"]
    assert limit >= 1000
