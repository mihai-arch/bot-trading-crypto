"""
Entry point: python -m bit

Starts the BIT v1 paper trading runner.

Usage:
    python -m bit                             # Reads .env for config
    BYBIT_API_KEY=xxx python -m bit           # Inline env var override
    BIT_RUN_INTERVAL_SECONDS=30 python -m bit # Override cycle interval

The runner loops forever — press Ctrl+C to stop cleanly.

To run the dashboard alongside the runner (separate terminal):
    python -m bit.dashboard

The dashboard will show live runner state when both share the same
PaperPortfolioTracker instance. For integrated startup with shared state,
use the create_app() factory from bit.dashboard.app and wire the shared
portfolio and runner.state objects manually.
"""

import asyncio
import logging
import sys

from .config import BITConfig
from .pipeline import Pipeline
from .runner import BotRunner
from .services.decision_engine import DecisionEngine
from .services.execution_engine import ExecutionEngine
from .services.feature_engine import FeatureEngine
from .services.journal import JournalLearningStore
from .services.market_data import MarketDataService
from .services.paper_portfolio import PaperPortfolioTracker
from .services.risk_engine import RiskEngine
from .services.signal_engine import SignalEngine
from .strategies.breakout_confirmation import BreakoutConfirmationStrategy
from .strategies.trend_continuation import TrendContinuationStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bit")


def build_pipeline(
    config: BITConfig,
) -> tuple[Pipeline, PaperPortfolioTracker, MarketDataService]:
    """
    Instantiate and wire all services for one bot session.

    Returns (Pipeline, PaperPortfolioTracker, MarketDataService).
    Pass the tracker to create_app() to see live portfolio state on the dashboard.
    Pass the MarketDataService to BotRunner for startup connectivity validation.
    """
    market_data = MarketDataService(config)
    feature_engine = FeatureEngine()
    signal_engine = SignalEngine(
        strategies=[
            TrendContinuationStrategy(),
            BreakoutConfirmationStrategy(),
        ]
    )
    decision_engine = DecisionEngine(config)
    risk_engine = RiskEngine(config)
    execution_engine = ExecutionEngine(config)
    journal = JournalLearningStore(config)
    portfolio = PaperPortfolioTracker(starting_cash=config.capital_usdt)

    pipeline = Pipeline(
        config=config,
        market_data=market_data,
        feature_engine=feature_engine,
        signal_engine=signal_engine,
        decision_engine=decision_engine,
        risk_engine=risk_engine,
        execution_engine=execution_engine,
        journal=journal,
        portfolio_tracker=portfolio,
    )
    return pipeline, portfolio, market_data


async def _run() -> None:
    config = BITConfig()

    if not config.paper_trading:
        logger.error(
            "paper_trading=False detected. BotRunner v1 is paper-only. "
            "Set PAPER_TRADING=true in .env and restart."
        )
        sys.exit(1)

    logger.info(
        "BIT v1 paper runner. symbols=%s interval=%ds heartbeat=%s",
        [str(s) for s in config.symbols],
        config.run_interval_seconds,
        config.heartbeat_path,
    )

    pipeline, _portfolio, market_data = build_pipeline(config)
    runner = BotRunner(config=config, pipeline=pipeline, market_data=market_data)
    try:
        await runner.start()
    except RuntimeError as exc:
        logger.error("Runner failed to start: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_run())
