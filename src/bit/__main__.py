"""
BIT runner entry point.

Run with:
    python -m bit.runner

Assembles all services from config/env, restores or creates the portfolio
tracker, then runs the main loop until interrupted (Ctrl+C).
"""

import asyncio
import logging
import sys

from .bybit.client import BybitRestClient
from .config import BITConfig
from .pipeline import Pipeline
from .runner import BotRunner
from .services.credential_check import check_credentials
from .services.decision_engine import DecisionEngine
from .services.execution_engine import ExecutionEngine
from .services.exit_evaluator import ExitEvaluator
from .services.feature_engine import FeatureEngine
from .services.journal import JournalLearningStore
from .services.market_data import MarketDataService
from .services.paper_portfolio import PaperPortfolioTracker
from .services.portfolio_store import PortfolioStateStore
from .services.risk_engine import RiskEngine
from .services.signal_engine import SignalEngine
from .strategies.breakout_confirmation import BreakoutConfirmationStrategy
from .strategies.trend_continuation import TrendContinuationStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bit.main")


def _build_portfolio(config: BITConfig) -> PaperPortfolioTracker:
    """
    Load portfolio from disk if available, otherwise start fresh.

    Halts on corrupt state — never silently continue with unknown account state.
    """
    result = PortfolioStateStore.load(
        config.portfolio_state_path,
        starting_cash=config.capital_usdt,
    )
    if result.status == "ok":
        assert result.tracker is not None
        logger.info(
            "Portfolio restored from %s (saved_at=%s)",
            config.portfolio_state_path,
            result.saved_at,
        )
        return result.tracker
    elif result.status == "corrupt":
        logger.critical(
            "Portfolio state file is corrupt: %s\n"
            "Inspect or delete %s to start fresh.",
            result.error,
            config.portfolio_state_path,
        )
        sys.exit(1)
    else:  # not_found
        logger.info(
            "No portfolio state file found at %s — starting fresh with %.2f USDT.",
            config.portfolio_state_path,
            config.capital_usdt,
        )
        return PaperPortfolioTracker(starting_cash=config.capital_usdt)


def _build_runner(config: BITConfig) -> BotRunner:
    """Wire all services together and return a ready-to-start BotRunner."""
    portfolio = _build_portfolio(config)

    market_data = MarketDataService(config=config)
    feature_engine = FeatureEngine()
    strategies = [TrendContinuationStrategy(), BreakoutConfirmationStrategy()]
    signal_engine = SignalEngine(strategies=strategies)
    decision_engine = DecisionEngine(config=config)
    risk_engine = RiskEngine(config=config)
    execution_engine = ExecutionEngine(config=config)
    journal = JournalLearningStore(config=config)

    exit_evaluator = ExitEvaluator(config=config)

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
        exit_evaluator=exit_evaluator,
    )

    # Credential checker: called once at startup if credentials are configured.
    # Uses a fresh client scoped to this check only.
    async def _check_creds():
        async with BybitRestClient(testnet=config.bybit_testnet) as client:
            return await check_credentials(config, client)

    return BotRunner(
        config=config,
        pipeline=pipeline,
        portfolio=portfolio,
        credential_checker=_check_creds,
    )


async def _main() -> None:
    config = BITConfig()
    runner = _build_runner(config)
    try:
        await runner.start()
    except KeyboardInterrupt:
        await runner.stop()


if __name__ == "__main__":
    asyncio.run(_main())
