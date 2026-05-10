"""
Pipeline exit integration tests.

Tests verify that the Pipeline correctly:
  - Evaluates exit conditions when the ExitEvaluator is wired in and a position is open
  - Records the EXIT journal entry with correct fields
  - Skips exit evaluation when exit_evaluator=None
  - Runs normal ENTER/MONITOR/REJECT flow when there is no open position for the symbol

All upstream services (MarketDataService, FeatureEngine, SignalEngine, DecisionEngine,
RiskEngine, JournalLearningStore) are mocked so the tests are fast and isolated.
PaperPortfolioTracker is real so we can verify portfolio mutations.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bit.config import BITConfig
from bit.domain.enums import DecisionState, OrderSide, StrategyId, Symbol, Timeframe
from bit.domain.execution import Fill
from bit.domain.journal import JournalEntry
from bit.domain.market import (
    InstrumentFilter,
    Kline,
    Position,
    PortfolioState,
    Ticker,
)
from bit.domain.signals import AggregatedSignal, Signal
from bit.pipeline import Pipeline
from bit.services.exit_evaluator import ExitEvaluator
from bit.services.paper_portfolio import PaperPortfolioTracker


# ── Shared fixtures / helpers ─────────────────────────────────────────────────

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_SYMBOL = Symbol.BTCUSDT
_ENTRY_PRICE = Decimal("60000")
_CURRENT_PRICE = Decimal("55000")   # 8.3% below entry → triggers stop-loss with 5% threshold
_QTY = Decimal("0.001")


@pytest.fixture
def config() -> BITConfig:
    return BITConfig(
        paper_trading=True,
        stop_loss_pct=Decimal("0.05"),
        take_profit_pct=Decimal("0.10"),
        exit_score_threshold=Decimal("0.30"),
    )


def _make_ticker(price: Decimal = _CURRENT_PRICE) -> Ticker:
    return Ticker(
        symbol=_SYMBOL,
        last_price=price,
        bid=price - Decimal("1"),
        ask=price + Decimal("1"),
        timestamp=_NOW,
    )


def _make_klines(symbol: Symbol = _SYMBOL, timeframe: Timeframe = Timeframe.M5) -> list[Kline]:
    return [
        Kline(
            symbol=symbol,
            timeframe=timeframe,
            open_time=_NOW,
            open=Decimal("59000"),
            high=Decimal("60000"),
            low=Decimal("58000"),
            close=Decimal("59500"),
            volume=Decimal("100"),
            is_closed=True,
        )
    ]


def _make_instrument() -> InstrumentFilter:
    return InstrumentFilter(
        symbol=_SYMBOL,
        tick_size=Decimal("0.01"),
        qty_step=Decimal("0.000001"),
        min_order_qty=Decimal("0.000048"),
        min_order_usdt=Decimal("1"),
    )


def _make_aggregated_signal(score: Decimal = Decimal("0.20")) -> AggregatedSignal:
    """Low-score signal to trigger signal-deterioration exit."""
    sig = Signal(
        strategy_id=StrategyId.TREND_CONTINUATION,
        symbol=_SYMBOL,
        timestamp=_NOW,
        score=score,
        rationale="test",
    )
    return AggregatedSignal(
        symbol=_SYMBOL,
        timestamp=_NOW,
        all_signals=[sig],
        selected=sig if score > 0 else None,
        candidate_count=1 if score > 0 else 0,
        rationale="test",
    )


def _portfolio_with_open_position(
    avg_entry: Decimal = _ENTRY_PRICE,
    qty: Decimal = _QTY,
    cash: Decimal = Decimal("400"),
) -> PaperPortfolioTracker:
    """Pre-populate a tracker with an open BTC position."""
    # Build the tracker with enough cash to accommodate the buy
    tracker = PaperPortfolioTracker(starting_cash=Decimal("500"))
    buy_fill = Fill(
        order_id="seed-buy",
        symbol=_SYMBOL,
        side=OrderSide.BUY,
        filled_qty=qty,
        avg_fill_price=avg_entry,
        fee_usdt=qty * avg_entry * Decimal("0.001"),
        slippage_usdt=Decimal("0"),
        filled_at=_NOW,
        is_paper=True,
    )
    tracker.apply_fill(buy_fill)
    return tracker


def _build_pipeline(config: BITConfig, portfolio: PaperPortfolioTracker,
                    exit_evaluator: ExitEvaluator | None,
                    agg_signal: AggregatedSignal) -> Pipeline:
    """Wire all mocked services + real portfolio + optional ExitEvaluator into a Pipeline."""
    market_data = AsyncMock()
    market_data.get_klines.return_value = _make_klines()
    market_data.get_ticker.return_value = _make_ticker()
    market_data.get_instrument_filter.return_value = _make_instrument()

    feature_engine = MagicMock()
    feature_engine.compute.return_value = MagicMock()

    signal_engine = MagicMock()
    signal_engine.evaluate.return_value = agg_signal

    decision_engine = MagicMock()
    # DecisionEngine.decide won't be called for EXIT cycles (return early)
    decision_engine.decide.return_value = MagicMock(
        state=DecisionState.MONITOR,
        contributing_strategies=[],
        composite_score=Decimal("0.20"),
        rationale="monitor",
        suggested_entry_price=None,
    )

    risk_engine = MagicMock()
    journal = MagicMock()

    from bit.services.execution_engine import ExecutionEngine
    execution_engine = ExecutionEngine(config=config)

    return Pipeline(
        config=config,
        market_data=market_data,
        feature_engine=feature_engine,
        signal_engine=signal_engine,
        decision_engine=decision_engine,
        risk_engine=risk_engine,
        execution_engine=execution_engine,
        journal=journal,
        portfolio_tracker=portfolio,
        exit_evaluator=exit_evaluator,
    )


# ── EXIT entry tests ──────────────────────────────────────────────────────────

class TestPipelineStopLossExit:
    async def test_exit_entry_decision_state(self, config):
        """Stop-loss hit → JournalEntry.decision_state == EXIT."""
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        ev = ExitEvaluator(config)
        # Current price 55000 is 8.3% below 60000 → triggers 5% stop-loss
        agg = _make_aggregated_signal(score=Decimal("0.70"))  # strong signal, but stop-loss wins
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        entry = await pipeline.run(_SYMBOL)
        assert entry.decision_state == DecisionState.EXIT

    async def test_exit_entry_reason_stop_loss(self, config):
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        entry = await pipeline.run(_SYMBOL)
        assert entry.exit_reason == "stop_loss"

    async def test_exit_entry_order_side_sell(self, config):
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        entry = await pipeline.run(_SYMBOL)
        assert entry.order_side == "Sell"

    async def test_exit_entry_fill_price_populated(self, config):
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        entry = await pipeline.run(_SYMBOL)
        assert entry.fill_price is not None
        assert entry.fill_price > 0

    async def test_exit_entry_fill_qty_populated(self, config):
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE, qty=_QTY)
        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        entry = await pipeline.run(_SYMBOL)
        assert entry.fill_qty == _QTY

    async def test_exit_entry_fee_populated(self, config):
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        entry = await pipeline.run(_SYMBOL)
        assert entry.fee_usdt is not None
        assert entry.fee_usdt > 0

    async def test_exit_closes_position_in_tracker(self, config):
        """After exit, portfolio tracker should have no position for the symbol."""
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        await pipeline.run(_SYMBOL)
        # After SELL fill, position should be gone
        assert portfolio.position_count == 0

    async def test_exit_apply_fill_called_once(self, config):
        """Pipeline calls apply_fill exactly once with a SELL fill on exit."""
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        original_apply_fill = portfolio.apply_fill
        calls = []
        def tracking_apply_fill(fill):
            calls.append(fill)
            return original_apply_fill(fill)
        portfolio.apply_fill = tracking_apply_fill

        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        await pipeline.run(_SYMBOL)
        assert len(calls) == 1
        assert calls[0].side == OrderSide.SELL


# ── No exit when no open position ─────────────────────────────────────────────

class TestNoExitWithoutPosition:
    async def test_no_exit_when_no_open_position(self, config):
        """No position for symbol → exit check is skipped, normal flow runs."""
        # Empty portfolio — no position
        portfolio = PaperPortfolioTracker(starting_cash=Decimal("500"))
        ev = ExitEvaluator(config)
        agg = _make_aggregated_signal(score=Decimal("0.20"))
        pipeline = _build_pipeline(config, portfolio, ev, agg)
        entry = await pipeline.run(_SYMBOL)
        # Should be MONITOR or REJECT, not EXIT
        assert entry.decision_state != DecisionState.EXIT


# ── No exit when exit_evaluator=None ─────────────────────────────────────────

class TestNoExitEvaluatorWired:
    async def test_exit_step_skipped_when_evaluator_none(self, config):
        """exit_evaluator=None → exit step skipped entirely."""
        # Open position at entry with current price at stop-loss level
        portfolio = _portfolio_with_open_position(avg_entry=_ENTRY_PRICE)
        agg = _make_aggregated_signal(score=Decimal("0.70"))
        pipeline = _build_pipeline(config, portfolio, exit_evaluator=None, agg_signal=agg)
        entry = await pipeline.run(_SYMBOL)
        # EXIT should not be triggered
        assert entry.decision_state != DecisionState.EXIT
        # Position should still be open
        assert portfolio.position_count == 1
