"""
BITConfig — central configuration for all services.

Loaded from environment variables (or .env file).
All secrets must be provided via environment — never hardcoded.
All thresholds and limits are configurable without code changes.
"""

from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain.enums import Symbol, Timeframe


class BITConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Exchange credentials ───────────────────────────────────────────────────
    bybit_api_key: str = Field(default="", description="Bybit API key. Required for live mode.")
    bybit_api_secret: str = Field(default="", description="Bybit API secret. Required for live mode.")
    bybit_testnet: bool = Field(default=True, description="Use Bybit testnet endpoints.")

    # ── Trading mode ──────────────────────────────────────────────────────────
    paper_trading: bool = Field(
        default=True,
        description="Paper trading mode. No real orders placed. Switch to False only after validation.",
    )

    # ── Symbols and timeframes ─────────────────────────────────────────────────
    symbols: list[Symbol] = Field(
        default_factory=lambda: [Symbol.BTCUSDT, Symbol.ETHUSDT, Symbol.SOLUSDT],
    )
    timeframes: list[Timeframe] = Field(
        default_factory=lambda: [Timeframe.M5, Timeframe.M15, Timeframe.H1],
    )

    # ── Capital ───────────────────────────────────────────────────────────────
    capital_usdt: Decimal = Field(default=Decimal("500"), description="Total capital in USDT.")
    max_position_pct: Decimal = Field(
        default=Decimal("0.20"),
        description="Max fraction of available capital per position (0.20 = 20%).",
    )
    max_open_positions: int = Field(default=3, description="Maximum concurrent open positions.")

    # ── Risk controls ─────────────────────────────────────────────────────────
    max_drawdown_pct: Decimal = Field(
        default=Decimal("0.10"),
        description="Portfolio drawdown fraction that triggers a trading halt (0.10 = 10%).",
    )

    # ── Paper trading simulation ───────────────────────────────────────────────
    paper_fee_rate: Decimal = Field(
        default=Decimal("0.001"),
        description="Taker fee rate applied to paper fills (0.001 = 0.1%).",
    )
    paper_slippage_pct: Decimal = Field(
        default=Decimal("0.0005"),
        description="Adverse price slippage applied to paper fills (0.0005 = 0.05%).",
    )

    # ── Persistence paths ─────────────────────────────────────────────────────
    portfolio_state_path: Path = Field(
        default=Path("data/portfolio_state.json"),
        description=(
            "Path to the paper portfolio state JSON file. "
            "Written after each fill; read at startup to restore positions."
        ),
    )
    runner_state_path: Path = Field(
        default=Path("data/runner_state.json"),
        description=(
            "Path to the runner state JSON file. "
            "Written by the run loop; read by the dashboard to show loop health."
        ),
    )

    # ── Run loop ──────────────────────────────────────────────────────────────
    run_interval_seconds: int = Field(
        default=300,
        description=(
            "Seconds to wait between pipeline cycles. "
            "Default 300 matches the 5m candle cadence."
        ),
    )

    # ── Signal thresholds ─────────────────────────────────────────────────────
    enter_threshold: Decimal = Field(
        default=Decimal("0.65"),
        description="Composite score at or above which DecisionEngine emits ENTER.",
    )
    monitor_threshold: Decimal = Field(
        default=Decimal("0.40"),
        description="Composite score at or above which DecisionEngine emits MONITOR.",
    )

    # ── Exit thresholds ───────────────────────────────────────────────────────
    stop_loss_pct: Decimal = Field(
        default=Decimal("0.05"),
        description="Price decline fraction from avg entry that triggers a stop-loss exit (0.05 = 5%).",
    )
    take_profit_pct: Decimal = Field(
        default=Decimal("0.10"),
        description="Price gain fraction from avg entry that triggers a take-profit exit (0.10 = 10%).",
    )
    exit_score_threshold: Decimal = Field(
        default=Decimal("0.30"),
        description="Signal score at or below which a signal-deterioration exit is triggered.",
    )
