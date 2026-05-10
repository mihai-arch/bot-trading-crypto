"""
Pipeline

Orchestrates one complete evaluation cycle for a single symbol:

  1. Fetch market data (klines, ticker, instrument filter, portfolio state)
  2. Compute features (FeatureEngine)
  3. Evaluate strategies → signals (SignalEngine)
  4. Aggregate signals → decision (DecisionEngine)
  5. If ENTER: risk check → sizing (RiskEngine)
  6. If approved: execute order (ExecutionEngine)
  7. Record to journal (JournalLearningStore) — always, regardless of outcome

Call `pipeline.run(symbol)` once per tick or on a schedule.
"""

from datetime import datetime, timezone
from uuid import uuid4

from decimal import Decimal

from .config import BITConfig
from .domain.enums import DecisionState, Symbol
from .domain.journal import JournalEntry
from .services.decision_engine import DecisionEngine
from .services.execution_engine import ExecutionEngine
from .services.exit_evaluator import ExitEvaluator
from .services.feature_engine import FeatureEngine
from .services.journal import JournalLearningStore
from .services.market_data import MarketDataService
from .services.paper_portfolio import PaperPortfolioTracker
from .services.risk_engine import RiskEngine
from .services.signal_engine import SignalEngine


class Pipeline:
    """
    Wires all services together for one symbol evaluation cycle.

    Instantiate once at startup with all services injected.
    Call run(symbol) on each evaluation tick.
    """

    def __init__(
        self,
        config: BITConfig,
        market_data: MarketDataService,
        feature_engine: FeatureEngine,
        signal_engine: SignalEngine,
        decision_engine: DecisionEngine,
        risk_engine: RiskEngine,
        execution_engine: ExecutionEngine,
        journal: JournalLearningStore,
        portfolio_tracker: PaperPortfolioTracker,
        exit_evaluator: ExitEvaluator | None = None,
    ) -> None:
        self._config = config
        self._market_data = market_data
        self._features = feature_engine
        self._signals = signal_engine
        self._decisions = decision_engine
        self._risk = risk_engine
        self._execution = execution_engine
        self._journal = journal
        self._portfolio = portfolio_tracker
        self._exit_evaluator = exit_evaluator

    async def run(self, symbol: Symbol) -> JournalEntry:
        """
        Run one evaluation cycle for the given symbol.

        Returns the JournalEntry that was recorded for this cycle.
        The entry reflects the final decision and fill (if any).
        """
        from .domain.enums import Timeframe

        # ── Step 1: Fetch market data ─────────────────────────────────────────
        klines_5m = await self._market_data.get_klines(symbol, Timeframe.M5)
        klines_15m = await self._market_data.get_klines(symbol, Timeframe.M15)
        klines_1h = await self._market_data.get_klines(symbol, Timeframe.H1)
        ticker = await self._market_data.get_ticker(symbol)
        instrument = await self._market_data.get_instrument_filter(symbol)

        # Portfolio state from the paper tracker, marked to current ticker price.
        # In live mode this would call MarketDataService.get_portfolio_state() instead.
        portfolio = self._portfolio.snapshot({symbol: ticker.last_price})

        # ── Step 2: Compute features ──────────────────────────────────────────
        features = self._features.compute(symbol, klines_5m, klines_15m, klines_1h, ticker)

        # ── Step 3: Evaluate strategies → aggregated signal ──────────────────
        agg = self._signals.evaluate(features)
        signal_score = agg.selected.score if agg.selected else Decimal("0")

        # ── Step 3.5: Exit check (open positions only) ────────────────────────
        if self._exit_evaluator and symbol in portfolio.open_positions:
            position = portfolio.open_positions[symbol]
            exit_dec = self._exit_evaluator.evaluate(
                position, ticker.last_price, signal_score
            )
            if exit_dec:
                fill = self._execution.execute_exit_paper(
                    symbol, position.qty, ticker.last_price
                )
                self._portfolio.apply_fill(fill)
                entry = JournalEntry(
                    entry_id=str(uuid4()),
                    symbol=symbol,
                    cycle_timestamp=datetime.now(tz=timezone.utc),
                    decision_state=DecisionState.EXIT,
                    contributing_strategies=[
                        s.strategy_id for s in agg.all_signals if s.score > 0
                    ],
                    composite_score=signal_score,
                    rationale=f"EXIT:{exit_dec.reason} price={ticker.last_price}",
                    fill_price=fill.avg_fill_price,
                    fill_qty=fill.filled_qty,
                    fee_usdt=fill.fee_usdt,
                    is_paper=self._config.paper_trading,
                    raw_signal_scores={
                        s.strategy_id: float(s.score) for s in agg.all_signals
                    },
                    exit_reason=exit_dec.reason,
                    order_side="Sell",
                )
                self._journal.record(entry)
                return entry

        # ── Step 4: Select candidate → decision ───────────────────────────────
        decision = self._decisions.decide(agg)

        # Attach current price as suggested entry price for ENTER decisions.
        if decision.state == DecisionState.ENTER:
            decision = decision.model_copy(
                update={"suggested_entry_price": ticker.last_price}
            )

        # ── Steps 5–6: Risk check and execution (ENTER only) ─────────────────
        fill = None
        sizing = None
        if decision.state == DecisionState.ENTER:
            sizing = self._risk.approve(decision, portfolio, instrument)
            if sizing.approved:
                fill = await self._execution.execute(sizing, decision)
                # Apply the fill to the portfolio tracker so state is current
                # before the next cycle evaluates risk.
                self._portfolio.apply_fill(fill)

        # ── Step 7: Journal ───────────────────────────────────────────────────
        entry = JournalEntry(
            entry_id=str(uuid4()),
            symbol=symbol,
            cycle_timestamp=datetime.now(tz=timezone.utc),
            decision_state=decision.state,
            contributing_strategies=decision.contributing_strategies,
            composite_score=decision.composite_score,
            rationale=decision.rationale,
            fill_price=fill.avg_fill_price if fill else None,
            fill_qty=fill.filled_qty if fill else None,
            fee_usdt=fill.fee_usdt if fill else None,
            is_paper=self._config.paper_trading,
            raw_signal_scores={s.strategy_id: float(s.score) for s in agg.all_signals},
        )
        self._journal.record(entry)
        return entry
