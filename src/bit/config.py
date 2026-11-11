"""
BITConfig — central configuration for all services.

Loaded from environment variables (or .env file).
All secrets must be provided via environment — never hardcoded.
All thresholds and limits are configurable without code changes.
"""

from decimal import Decimal
from pathlib import Path
from typing import Literal

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

    # ── Strategy selection ────────────────────────────────────────────────────
    strategy_mode: Literal["blend", "legacy"] = Field(
        default="blend",
        description=(
            "blend: 50% daily core rotation + 50% 4h EMA trend (default). "
            "legacy: the original 15m trend-continuation and breakout checklist."
        ),
    )

    # ── Capital ───────────────────────────────────────────────────────────────
    capital_usdt: Decimal = Field(
        default=Decimal("1000"),
        description="Starting paper bankroll in USDT.",
    )
    goal_equity_usdt: Decimal = Field(
        default=Decimal("2000"),
        description="Equity goal reported in the log and persisted state.",
    )
    max_position_pct: Decimal = Field(
        default=Decimal("0.20"),
        description="Max fraction of available capital per position (0.20 = 20%).",
    )
    max_open_positions: int = Field(default=3, description="Maximum concurrent open positions.")

    # ── Risk controls ─────────────────────────────────────────────────────────
    max_drawdown_pct: Decimal = Field(
        default=Decimal("0.25"),
        description=(
            "Halt when equity falls to this fraction below the stage starting bankroll "
            "(0.25 = -25% from start). The halt stays on until a manual reset."
        ),
    )
    daily_loss_limit_pct: Decimal = Field(
        default=Decimal("0.03"),
        description="Block new entries after a loss of this fraction of start-of-day equity (0.03 = 3%).",
    )
    max_consecutive_losses: int = Field(
        default=6,
        description="Block new entries after this many consecutive losing round-trips until a manual reset.",
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

    # ── Blend strategy (paper) ────────────────────────────────────────────────
    kline_limit: int = Field(
        default=1000,
        description="Closed-candle history requested per symbol and timeframe. Bybit caps a request at 1000.",
    )
    rotation_lookback_days: int = Field(
        default=30,
        description="Trailing return window for the daily core-rotation sleeve.",
    )
    rotation_sleeve_pct: Decimal = Field(
        default=Decimal("0.50"),
        description="Fraction of starting capital allocated to the rotation sleeve. The rest is the conservative sleeve.",
    )
    ema_fast: int = Field(default=20, description="Fast EMA span for the 4h conservative sleeve.")
    ema_slow: int = Field(default=100, description="Slow EMA span for the 4h conservative sleeve.")
    vol_target: Decimal = Field(
        default=Decimal("0.40"),
        description="Annualised volatility target for the conservative sleeve (0.40 = 40%).",
    )
    vol_lookback_bars: int = Field(
        default=180,
        description="4h bars in the realized-vol window (180 bars = 30 days).",
    )
    vol_bars_per_year: int = Field(
        default=2190,
        description="4h bars per year used to annualise realized volatility (365 * 6).",
    )
    rebalance_band: Decimal = Field(
        default=Decimal("0.10"),
        description="Conservative sleeve trades only when the target weight moves by more than this.",
    )
    rotation_rebalance_band: Decimal = Field(
        default=Decimal("0.05"),
        description="Rotation sleeve band. Full switches (0 or 1) always trade.",
    )
    fill_grace_minutes: int = Field(
        default=30,
        description=(
            "A new closed bar is filled at the next candle's open only when the bot sees it "
            "within this many minutes of that open. Later restarts keep the existing position "
            "until the next close."
        ),
    )
    stop_file: Path = Field(
        default=Path("data/STOP"),
        description="If this file exists, the bot halts and does not place further paper orders.",
    )
    blend_state_path: Path = Field(
        default=Path("data/blend_state.json"),
        description="Persisted blend sleeves, equity, and risk-halt flags. Restored on startup.",
    )
    journal_path: Path = Field(
        default=Path("data/journal.jsonl"),
        description="Append-only journal of decisions, fills, and rejected signals.",
    )
