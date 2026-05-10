"""
RiskEngine tests.

The RiskEngine is fully deterministic — no I/O, no state.
Tests verify sizing logic, rejection cases, and exchange constraint enforcement.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bit.domain.decisions import Decision
from bit.domain.enums import DecisionState, StrategyId, Symbol
from bit.domain.market import InstrumentFilter, Position, PortfolioState
from bit.services.risk_engine import RiskEngine


_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _enter_decision(
    symbol: Symbol = Symbol.BTCUSDT,
    price: Decimal = Decimal("60000"),
) -> Decision:
    return Decision(
        symbol=symbol,
        timestamp=_NOW,
        state=DecisionState.ENTER,
        composite_score=Decimal("0.75"),
        contributing_strategies=[StrategyId.TREND_CONTINUATION],
        rationale="test",
        suggested_entry_price=price,
    )


def _decision_no_price(symbol: Symbol = Symbol.BTCUSDT) -> Decision:
    return Decision(
        symbol=symbol,
        timestamp=_NOW,
        state=DecisionState.ENTER,
        composite_score=Decimal("0.75"),
        contributing_strategies=[StrategyId.TREND_CONTINUATION],
        rationale="test",
        suggested_entry_price=None,
    )


class TestRiskEngineApprovals:
    def test_approves_valid_entry(self, config, btc_instrument, empty_portfolio):
        engine = RiskEngine(config)
        result = engine.approve(_enter_decision(), empty_portfolio, btc_instrument)
        assert result.approved
        assert result.qty > 0
        assert result.notional_usdt > 0

    def test_approved_notional_within_budget(self, config, btc_instrument, empty_portfolio):
        engine = RiskEngine(config)
        result = engine.approve(_enter_decision(), empty_portfolio, btc_instrument)
        max_allowed = empty_portfolio.available_usdt * config.max_position_pct
        assert result.notional_usdt <= max_allowed * Decimal("1.001")  # allow tiny rounding margin

    def test_qty_is_multiple_of_qty_step(self, config, btc_instrument, empty_portfolio):
        """Quantity must be snapped to qty_step (floored)."""
        engine = RiskEngine(config)
        result = engine.approve(_enter_decision(), empty_portfolio, btc_instrument)
        if result.approved:
            remainder = result.qty % btc_instrument.qty_step
            assert remainder == Decimal("0")


class TestRiskEngineRejections:
    def test_rejects_no_entry_price(self, config, btc_instrument, empty_portfolio):
        engine = RiskEngine(config)
        result = engine.approve(_decision_no_price(), empty_portfolio, btc_instrument)
        assert not result.approved
        assert result.rejection_reason is not None

    def test_rejects_enter_when_symbol_already_open(self, config, btc_instrument):
        """ENTER for a symbol that already has an open position must be rejected."""
        portfolio = PortfolioState(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("400"),
            open_positions={
                Symbol.BTCUSDT: Position(
                    symbol=Symbol.BTCUSDT,
                    qty=Decimal("0.001"),
                    avg_entry_price=Decimal("60000"),
                    unrealized_pnl_usdt=Decimal("0"),
                )
            },
        )
        engine = RiskEngine(config)
        result = engine.approve(_enter_decision(Symbol.BTCUSDT), portfolio, btc_instrument)
        assert not result.approved
        assert "already open" in result.rejection_reason

    def test_allows_enter_for_different_symbol_when_other_open(self, config, btc_instrument):
        """ENTER for a symbol with no open position is allowed even if another symbol is open."""
        portfolio = PortfolioState(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("400"),
            open_positions={
                Symbol.ETHUSDT: Position(
                    symbol=Symbol.ETHUSDT,
                    qty=Decimal("0.05"),
                    avg_entry_price=Decimal("3000"),
                    unrealized_pnl_usdt=Decimal("0"),
                )
            },
        )
        engine = RiskEngine(config)
        result = engine.approve(_enter_decision(Symbol.BTCUSDT), portfolio, btc_instrument)
        assert result.approved

    def test_rejects_max_positions_reached(self, config, btc_instrument):
        """Fill all 3 position slots — next approval should be rejected."""
        positions = {
            Symbol.BTCUSDT: Position(
                symbol=Symbol.BTCUSDT,
                qty=Decimal("0.001"),
                avg_entry_price=Decimal("60000"),
                unrealized_pnl_usdt=Decimal("0"),
            ),
            Symbol.ETHUSDT: Position(
                symbol=Symbol.ETHUSDT,
                qty=Decimal("0.01"),
                avg_entry_price=Decimal("3000"),
                unrealized_pnl_usdt=Decimal("0"),
            ),
            Symbol.SOLUSDT: Position(
                symbol=Symbol.SOLUSDT,
                qty=Decimal("0.5"),
                avg_entry_price=Decimal("150"),
                unrealized_pnl_usdt=Decimal("0"),
            ),
        }
        full_portfolio = PortfolioState(
            total_equity_usdt=Decimal("500"),
            available_usdt=Decimal("200"),
            open_positions=positions,
        )
        engine = RiskEngine(config)
        result = engine.approve(_enter_decision(), full_portfolio, btc_instrument)
        assert not result.approved
        assert "Max open positions" in result.rejection_reason

    def test_rejects_insufficient_capital_for_min_notional(self, config, depleted_portfolio):
        """Portfolio with $0.50 available cannot meet min_order_usdt of $1."""
        instrument = InstrumentFilter(
            symbol=Symbol.BTCUSDT,
            tick_size=Decimal("0.01"),
            qty_step=Decimal("0.000001"),
            min_order_qty=Decimal("0.000048"),
            min_order_usdt=Decimal("1"),
        )
        engine = RiskEngine(config)
        # max_notional = 0.50 * 0.20 = 0.10 USDT < min_order_usdt (1 USDT)
        result = engine.approve(_enter_decision(), depleted_portfolio, instrument)
        assert not result.approved
        assert result.rejection_reason is not None

    def test_rejection_reason_is_populated(self, config, btc_instrument, empty_portfolio):
        engine = RiskEngine(config)
        result = engine.approve(_decision_no_price(), empty_portfolio, btc_instrument)
        assert not result.approved
        assert isinstance(result.rejection_reason, str)
        assert len(result.rejection_reason) > 0
