"""
Paper-only runner for the 50/50 blend.

Sleeve A (half the bankroll): once per daily close, at the next daily open,
hold that sleeve in the BTC/ETH/SOL coin with the highest 30-day return.
Cash when every return is <= 0.

Sleeve B (the other half): three sub-accounts that are not rebalanced against
each other. Each is long its coin when the 4h EMA(20) is above EMA(100),
sized by min(1, 0.40 / 30-day realized vol), and flat otherwise. Signals use
closed candles. Fills happen at the next candle's open, with 0.1% fee and
0.05% adverse slippage per side.

This process never reads API secrets and never submits an order. Market data
comes from the public Bybit spot endpoints on mainnet. Create ``data/STOP``
(or pass the configured path) to halt it. Ctrl+C stops it as well.

Restarting the process reloads ``data/blend_state.json`` and keeps the same
positions and equity.

Usage (Windows PowerShell, from the repo directory):

    py -m bit.paper
    py -m bit.paper --reset-halt
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from .config import BITConfig
from .domain.enums import DecisionState, StrategyId, Symbol, Timeframe
from .domain.journal import JournalEntry
from .domain.market import InstrumentFilter, Kline, Ticker
from .services.blend_state import BlendState, BlendStateStore, equity_of, fresh_state
from .services.journal import JournalLearningStore
from .services.market_data import MarketDataService
from .services.paper_portfolio import PaperPortfolioTracker
from .services.rebalance import TradeLeg, rebalance_to_targets
from .services.risk_guard import GuardView, RiskGuard
from .strategies.blend_signals import choose_rotation, conservative_target_weight

logger = logging.getLogger("bit.paper")

_ZERO = Decimal("0")


def _require_paper(config: BITConfig) -> None:
    if not config.paper_trading:
        raise RuntimeError(
            "The blend runner is paper-only. Live order placement is disabled. "
            "Set PAPER_TRADING=true."
        )
    if config.bybit_api_key or config.bybit_api_secret:
        logger.info(
            "API keys are loaded from the environment and will not be sent. "
            "This process uses public market data only."
        )


class PaperBlendRunner:
    """One paper book, two sleeves, public klines only."""

    def __init__(
        self,
        config: BITConfig,
        market_data: MarketDataService,
        journal: JournalLearningStore,
        *,
        clock=None,
    ) -> None:
        _require_paper(config)
        self._config = config
        self._market = market_data
        self._journal = journal
        self._guard = RiskGuard(config)
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))
        self._stop = asyncio.Event()
        self._state = self._load_or_create()
        self._kline_limit = max(int(config.kline_limit), 1000)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _load_or_create(self) -> BlendState:
        path = self._config.blend_state_path
        try:
            state = BlendStateStore.load(path)
        except FileNotFoundError:
            state = fresh_state(self._config, now=self._clock())
            logger.info(
                "No blend state at %s. Starting a new paper book at %s USDT "
                "(goal %s USDT).",
                path,
                self._config.capital_usdt,
                self._config.goal_equity_usdt,
            )
            return state
        except ValueError as exc:
            logger.critical("Blend state is unusable: %s", exc)
            raise SystemExit(1) from exc
        logger.info(
            "Restored blend state from %s (equity %s, drawdown_halted=%s).",
            path,
            state.last_equity,
            state.drawdown_halted,
        )
        return state

    async def start(self) -> None:
        """Run until stop(), a STOP file, or cancellation."""
        logger.info(
            "BIT paper blend running. strategy=blend paper_only=true "
            "bankroll=%s goal=%s symbols=%s interval=%ss klines=%d "
            "market=bybit-mainnet-public orders=disabled",
            self._state.starting_equity,
            self._config.goal_equity_usdt,
            [s.value for s in self._config.symbols],
            self._config.run_interval_seconds,
            self._kline_limit,
        )
        try:
            while not self._stop.is_set():
                try:
                    should_stop = await self.run_once()
                except Exception as exc:
                    logger.error("Paper cycle failed: %s", exc, exc_info=True)
                    should_stop = False
                if should_stop or self._stop.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._config.run_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._persist("stopped")
            logger.info("BIT paper blend stopped. equity=%s", self._state.last_equity)

    def stop(self) -> None:
        self._stop.set()

    # ── One cycle ────────────────────────────────────────────────────────────

    async def run_once(self) -> bool:
        """
        Evaluate the latest closed candles and paper-trade if a new open is due.

        Returns True when the process should exit (STOP file).
        """
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        if self._config.stop_file.exists():
            self._journal_note(
                symbol=self._config.symbols[0],
                strategy=StrategyId.CORE_ROTATION,
                state=DecisionState.REJECT,
                score=_ZERO,
                rationale=f"STOP file at {self._config.stop_file} — bot halted, no orders sent.",
                now=now,
            )
            self._persist("stopped_by_file")
            logger.warning(
                "STOP file found at %s. Halting. Positions are left as they are. "
                "Delete the file before starting again.",
                self._config.stop_file,
            )
            return True

        try:
            bundle = await self._fetch()
        except Exception as exc:
            logger.error("Market data fetch failed; positions unchanged: %s", exc)
            return False

        marks = {symbol: ticker.last_price for symbol, ticker in bundle.tickers.items()}
        equity = equity_of(self._state, marks)
        verdict = self._guard.evaluate(
            GuardView(
                starting_equity=self._state.starting_equity,
                equity=equity,
                day_start_equity=self._state.day_start_equity,
                utc_day=self._state.utc_day,
                now=now,
                consecutive_losses=self._state.consecutive_losses,
                drawdown_halted=self._state.drawdown_halted,
                loss_streak_halted=self._state.loss_streak_halted,
                stop_file_present=False,
                goal_equity=self._config.goal_equity_usdt,
            )
        )
        self._state.utc_day = verdict.utc_day
        self._state.day_start_equity = verdict.day_start_equity
        self._state.drawdown_halted = verdict.drawdown_halted
        self._state.loss_streak_halted = verdict.loss_streak_halted
        self._state.goal_equity = self._config.goal_equity_usdt

        self._log_goal(equity, verdict.goal_progress, verdict.goal_remaining, verdict.reasons)

        if verdict.flatten or self._state.drawdown_halted:
            self._flatten(marks, bundle.instruments, now, verdict.reasons)
            equity = equity_of(self._state, marks)
            self._log_goal(
                equity,
                equity / self._config.goal_equity_usdt,
                self._config.goal_equity_usdt - equity,
                (),
            )
            self._persist("drawdown_halt")
            logger.error(
                "Drawdown halt is active at equity %s (floor %s). "
                "The bot will stay halted until you run: py -m bit.paper --reset-halt",
                equity,
                self._state.starting_equity * (Decimal("1") - self._config.max_drawdown_pct),
            )
            return False

        allow_buys = not verdict.block_entries
        block_reason = "; ".join(verdict.reasons) if verdict.reasons else ""

        self._run_rotation(bundle, marks, now, allow_buys, block_reason)
        self._run_conservative(bundle, marks, now, allow_buys, block_reason)

        equity = equity_of(self._state, marks)
        progress = equity / self._config.goal_equity_usdt
        self._log_goal(equity, progress, self._config.goal_equity_usdt - equity, ())
        self._persist("loss_streak_halt" if self._state.loss_streak_halted else "running")
        return False

    # ── Sleeves ──────────────────────────────────────────────────────────────

    def _run_rotation(self, bundle, marks, now, allow_buys: bool, block_reason: str) -> None:
        closed, forming = _split(bundle.daily)
        lookback = self._config.rotation_lookback_days
        short = any(
            forming.get(symbol) is None or len(closed.get(symbol, [])) <= lookback
            for symbol in self._config.symbols
        )
        if short:
            logger.warning(
                "Rotation skipped: need more than %d closed daily candles for every symbol.",
                self._config.rotation_lookback_days,
            )
            return

        aligned_times = _common_times(closed)
        if len(aligned_times) <= self._config.rotation_lookback_days:
            logger.warning("Rotation skipped: aligned daily history is too short.")
            return
        latest_open = aligned_times[-1]
        if self._state.last_rotation_open == latest_open:
            return

        series = {
            s.value: [float(_close_at(closed[s], t)) for t in aligned_times]
            for s in self._config.symbols
        }
        winner, returns = choose_rotation(series, self._config.rotation_lookback_days)
        target_symbol = Symbol(winner) if winner else None
        forming_open = forming[self._config.symbols[0]].open_time
        # Every symbol's forming daily candle opens together; use the winner's open when long.
        price_symbol = target_symbol or self._config.symbols[0]
        in_window = _in_grace(now, forming[price_symbol].open_time, self._config.fill_grace_minutes)
        detail = ", ".join(
            f"{name}={('n/a' if ret is None else f'{ret:.2%}')}" for name, ret in returns.items()
        )
        rationale = f"rotation 30d returns [{detail}]; target={winner or 'CASH'}"

        if not in_window:
            self._state.last_rotation_open = latest_open
            self._journal_note(
                symbol=price_symbol,
                strategy=StrategyId.CORE_ROTATION,
                state=DecisionState.REJECT,
                score=_ZERO,
                rationale=(
                    f"{rationale}. Missed the {forming_open.isoformat()} open "
                    f"(grace {self._config.fill_grace_minutes}m). Holding the current position "
                    "until the next daily close."
                ),
                now=now,
            )
            logger.info("Rotation window missed. %s", rationale)
            return

        fill_prices = {
            symbol: forming[symbol].open for symbol in self._config.symbols
        }
        targets = {symbol: _ZERO for symbol in self._config.symbols}
        if target_symbol is not None:
            targets[target_symbol] = Decimal("1")
        result = rebalance_to_targets(
            self._state.rotation,
            targets,
            fill_prices,
            fee_rate=self._config.paper_fee_rate,
            slippage=self._config.paper_slippage_pct,
            band=self._config.rotation_rebalance_band,
            allow_buys=allow_buys and target_symbol is not None,
            instruments=bundle.instruments,
            now=now,
        )
        self._state.last_rotation_open = latest_open
        self._record_legs(result.legs, StrategyId.CORE_ROTATION, now)
        if result.held and not result.legs:
            state = DecisionState.REJECT if target_symbol is None else DecisionState.MONITOR
            reason = rationale
            if target_symbol is None:
                reason = f"REJECT: {rationale}. All 30-day returns are <= 0, sleeve stays in cash."
            elif not allow_buys:
                reason = f"REJECT: {rationale}. Entries blocked ({block_reason})."
            elif result.blocked_buys:
                reason = f"REJECT: {rationale}. Entries blocked ({block_reason})."
            else:
                reason = f"MONITOR: {rationale}. Position already matches the target."
            self._journal_note(
                symbol=target_symbol or self._config.symbols[0],
                strategy=StrategyId.CORE_ROTATION,
                state=state,
                score=Decimal("1") if target_symbol else _ZERO,
                rationale=reason,
                now=now,
            )
        elif result.blocked_buys and not any(leg.fill.side.value == "Buy" for leg in result.legs):
            self._journal_note(
                symbol=result.blocked_buys[0],
                strategy=StrategyId.CORE_ROTATION,
                state=DecisionState.REJECT,
                score=Decimal("1"),
                rationale=(
                    f"REJECT: {rationale}. Buy blocked ({block_reason}). Reductions were applied."
                ),
                now=now,
            )
        logger.info("Rotation %s", rationale)

    def _run_conservative(self, bundle, marks, now, allow_buys: bool, block_reason: str) -> None:
        for symbol in self._config.symbols:
            closed, forming_map = _split({symbol: bundle.h4[symbol]})
            bars = closed.get(symbol, [])
            forming = forming_map.get(symbol)
            needed = max(self._config.ema_slow, self._config.vol_lookback_bars)
            if forming is None or len(bars) < needed:
                logger.warning(
                    "%s conservative skipped: %d closed 4h candles, need %d.",
                    symbol.value,
                    len(bars),
                    needed,
                )
                continue
            latest_open = bars[-1].open_time
            if self._state.last_conservative_open.get(symbol) == latest_open:
                continue
            closes = [float(bar.close) for bar in bars]
            weight = conservative_target_weight(
                closes,
                fast=self._config.ema_fast,
                slow=self._config.ema_slow,
                vol_target=float(self._config.vol_target),
                vol_n=self._config.vol_lookback_bars,
                bars_per_year=float(self._config.vol_bars_per_year),
            )
            target = Decimal(str(round(weight, 8)))
            in_window = _in_grace(now, forming.open_time, self._config.fill_grace_minutes)
            rationale = (
                f"{symbol.value} 4h EMA {self._config.ema_fast}/{self._config.ema_slow} "
                f"vol-target {self._config.vol_target} → weight {target}"
            )
            if not in_window:
                self._state.last_conservative_open[symbol] = latest_open
                self._journal_note(
                    symbol=symbol,
                    strategy=StrategyId.TREND_EMA_4H,
                    state=DecisionState.REJECT,
                    score=target,
                    rationale=(
                        f"{rationale}. Missed the {forming.open_time.isoformat()} open. "
                        "Waiting for the next 4h close."
                    ),
                    now=now,
                )
                continue
            result = rebalance_to_targets(
                self._state.conservative[symbol],
                {symbol: target},
                {symbol: forming.open},
                fee_rate=self._config.paper_fee_rate,
                slippage=self._config.paper_slippage_pct,
                band=self._config.rebalance_band,
                allow_buys=allow_buys,
                instruments=bundle.instruments,
                now=now,
            )
            self._state.last_conservative_open[symbol] = latest_open
            self._record_legs(result.legs, StrategyId.TREND_EMA_4H, now)
            if not result.legs:
                if target == _ZERO:
                    state = DecisionState.REJECT
                    text = (
                        f"REJECT: {rationale}. Trend is off or vol is undefined; "
                        "sub-account stays flat."
                    )
                elif not allow_buys or result.blocked_buys:
                    state = DecisionState.REJECT
                    text = f"REJECT: {rationale}. Entries blocked ({block_reason})."
                else:
                    state = DecisionState.MONITOR
                    text = (
                        f"MONITOR: {rationale}. Within the "
                        f"{self._config.rebalance_band} rebalance band."
                    )
                self._journal_note(
                    symbol=symbol,
                    strategy=StrategyId.TREND_EMA_4H,
                    state=state,
                    score=target,
                    rationale=text,
                    now=now,
                )
            logger.info("Conservative %s", rationale)

    def _flatten(self, marks, instruments, now, reasons: tuple[str, ...]) -> None:
        reason = "; ".join(reasons) or "drawdown halt"
        books: list[tuple[str, PaperPortfolioTracker]] = [("rotation", self._state.rotation)]
        books.extend(
            (symbol.value, tracker) for symbol, tracker in self._state.conservative.items()
        )
        for _name, tracker in books:
            held = list(tracker._positions.keys())
            if not held:
                continue
            targets = {symbol: _ZERO for symbol in held}
            prices = {symbol: marks[symbol] for symbol in held}
            result = rebalance_to_targets(
                tracker,
                targets,
                prices,
                fee_rate=self._config.paper_fee_rate,
                slippage=self._config.paper_slippage_pct,
                band=_ZERO,
                allow_buys=False,
                instruments=instruments,
                now=now,
                ignore_band=True,
            )
            self._record_legs(result.legs, StrategyId.CORE_ROTATION, now)
            for symbol in held:
                self._journal_note(
                    symbol=symbol,
                    strategy=StrategyId.CORE_ROTATION,
                    state=DecisionState.EXIT,
                    score=_ZERO,
                    rationale=f"FLATTEN: {reason}",
                    now=now,
                )

    def _record_legs(
        self,
        legs: tuple[TradeLeg, ...] | list[TradeLeg],
        strategy: StrategyId,
        now: datetime,
    ) -> None:
        for leg in legs:
            if leg.closed_symbol:
                updated = self._guard.register_round_trip(
                    consecutive_losses=self._state.consecutive_losses,
                    loss_streak_halted=self._state.loss_streak_halted,
                    realized_pnl=leg.realized_delta,
                )
                self._state.consecutive_losses = updated.consecutive_losses
                self._state.loss_streak_halted = updated.loss_streak_halted
            state = DecisionState.ENTER if leg.fill.side.value == "Buy" else DecisionState.EXIT
            self._journal_note(
                symbol=leg.fill.symbol,
                strategy=strategy,
                state=state,
                score=_ZERO,
                rationale=(
                    f"{state.value}: {strategy.value} {leg.fill.side.value} "
                    f"qty={leg.fill.filled_qty} price={leg.fill.avg_fill_price} "
                    f"fee={leg.fill.fee_usdt} realized_delta={leg.realized_delta}"
                ),
                now=now,
                fill=leg.fill,
            )

    # ── Market data ──────────────────────────────────────────────────────────

    def _warn_history(self, symbol: Symbol, label: str, rows: list[Kline]) -> None:
        closed = [row for row in rows if row.is_closed]
        if len(rows) < self._kline_limit:
            logger.warning(
                "%s %s returned %d candles (asked for %d).",
                symbol.value,
                label,
                len(rows),
                self._kline_limit,
            )
        if len(closed) < self._config.ema_slow:
            logger.warning(
                "%s %s has only %d closed candles.", symbol.value, label, len(closed)
            )

    async def _fetch(self) -> "_Bundle":
        symbols = list(self._config.symbols)
        daily_rows = await asyncio.gather(
            *[
                self._market.get_klines(symbol, Timeframe.D1, limit=self._kline_limit)
                for symbol in symbols
            ]
        )
        h4_rows = await asyncio.gather(
            *[
                self._market.get_klines(symbol, Timeframe.H4, limit=self._kline_limit)
                for symbol in symbols
            ]
        )
        tickers = await asyncio.gather(*[self._market.get_ticker(symbol) for symbol in symbols])
        instruments = await asyncio.gather(
            *[self._market.get_instrument_filter(symbol) for symbol in symbols]
        )
        for symbol, rows in zip(symbols, daily_rows, strict=True):
            self._warn_history(symbol, "1D", rows)
        for symbol, rows in zip(symbols, h4_rows, strict=True):
            self._warn_history(symbol, "4h", rows)
        return _Bundle(
            daily={symbol: rows for symbol, rows in zip(symbols, daily_rows, strict=True)},
            h4={symbol: rows for symbol, rows in zip(symbols, h4_rows, strict=True)},
            tickers={ticker.symbol: ticker for ticker in tickers},
            instruments={item.symbol: item for item in instruments},
        )

    # ── Journal, log, disk ───────────────────────────────────────────────────

    def _journal_note(
        self,
        *,
        symbol: Symbol,
        strategy: StrategyId,
        state: DecisionState,
        score: Decimal,
        rationale: str,
        now: datetime,
        fill=None,
    ) -> None:
        entry = JournalEntry(
            entry_id=str(uuid4()),
            symbol=symbol,
            cycle_timestamp=now,
            decision_state=state,
            contributing_strategies=[strategy],
            composite_score=score,
            rationale=rationale,
            fill_price=fill.avg_fill_price if fill else None,
            fill_qty=fill.filled_qty if fill else None,
            fee_usdt=fill.fee_usdt if fill else None,
            is_paper=True,
            raw_signal_scores={strategy.value: float(score)},
            order_side=fill.side.value if fill else None,
            exit_reason="drawdown_halt" if "FLATTEN" in rationale else None,
        )
        self._journal.record(entry)

    def _log_goal(
        self,
        equity: Decimal,
        progress: Decimal,
        remaining: Decimal,
        reasons: tuple[str, ...],
    ) -> None:
        self._state.last_equity = equity
        self._state.goal_progress = progress
        self._state.goal_remaining = remaining
        logger.info(
            "goal progress equity=%s USDT goal=%s USDT progress=%.1f%% remaining=%s USDT "
            "drawdown_halted=%s loss_streak_halted=%s consecutive_losses=%s%s",
            equity,
            self._config.goal_equity_usdt,
            float(progress * 100),
            remaining,
            self._state.drawdown_halted,
            self._state.loss_streak_halted,
            self._state.consecutive_losses,
            ("; " + "; ".join(reasons)) if reasons else "",
        )

    def _persist(self, status: str) -> None:
        self._state.status = status
        self._state.goal_equity = self._config.goal_equity_usdt
        try:
            BlendStateStore.save(self._state, self._config.blend_state_path)
        except Exception as exc:
            logger.warning("Failed to save blend state: %s", exc)


class _Bundle:
    def __init__(self, daily, h4, tickers, instruments) -> None:
        self.daily: dict[Symbol, list[Kline]] = daily
        self.h4: dict[Symbol, list[Kline]] = h4
        self.tickers: dict[Symbol, Ticker] = tickers
        self.instruments: dict[Symbol, InstrumentFilter] = instruments


def _split(
    rows: dict[Symbol, list[Kline]],
) -> tuple[dict[Symbol, list[Kline]], dict[Symbol, Kline | None]]:
    closed: dict[Symbol, list[Kline]] = {}
    forming: dict[Symbol, Kline | None] = {}
    for symbol, klines in rows.items():
        closed[symbol] = [bar for bar in klines if bar.is_closed]
        open_bars = [bar for bar in klines if not bar.is_closed]
        forming[symbol] = open_bars[-1] if open_bars else None
    return closed, forming


def _common_times(closed: dict[Symbol, list[Kline]]) -> list[datetime]:
    sets = [{bar.open_time for bar in bars} for bars in closed.values()]
    if not sets:
        return []
    common = set.intersection(*sets)
    return sorted(common)


def _close_at(bars: list[Kline], open_time: datetime) -> Decimal:
    for bar in bars:
        if bar.open_time == open_time:
            return bar.close
    raise KeyError(open_time)


def _in_grace(now: datetime, opened_at: datetime, grace_minutes: int) -> bool:
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - opened_at
    return timedelta(0) <= delta <= timedelta(minutes=grace_minutes)


def _public_market_data(config: BITConfig) -> MarketDataService:
    """Public mainnet market data. The client is not given API keys."""
    public = config.model_copy(update={"bybit_testnet": False, "paper_trading": True})
    service = MarketDataService(public)
    # MarketDataService only calls unsigned GETs. Drop any key material the
    # settings object may still be holding so it cannot be logged later.
    service._config = public.model_copy(update={"bybit_api_key": "", "bybit_api_secret": ""})
    return service


def reset_halt(config: BITConfig) -> None:
    """Clear a latched halt and start a new -25% stage from current equity."""
    _require_paper(config)
    path = config.blend_state_path
    try:
        state = BlendStateStore.load(path)
    except FileNotFoundError:
        logger.error("No blend state at %s. Nothing to reset.", path)
        raise SystemExit(1)
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    state.starting_equity = state.last_equity
    state.day_start_equity = state.last_equity
    state.drawdown_halted = False
    state.loss_streak_halted = False
    state.consecutive_losses = 0
    state.utc_day = datetime.now(tz=timezone.utc).date()
    state.status = "reset"
    BlendStateStore.save(state, path)
    logger.info(
        "Halt cleared. New stage starts at equity %s USDT. "
        "The -25%% line is measured from this equity. Positions were not changed.",
        state.last_equity,
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="BIT paper-only blend runner")
    parser.add_argument(
        "--reset-halt",
        action="store_true",
        help="Clear a drawdown or consecutive-loss halt and start a new stage from current equity.",
    )
    args = parser.parse_args(argv)
    config = BITConfig()
    if args.reset_halt:
        reset_halt(config)
        return
    _require_paper(config)
    market = _public_market_data(config)
    journal = JournalLearningStore(config, journal_path=config.journal_path)
    runner = PaperBlendRunner(config, market, journal)

    async def _run() -> None:
        try:
            await runner.start()
        finally:
            await market.aclose()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        runner.stop()
        logger.info("Interrupted. State has been saved.")


if __name__ == "__main__":
    main()
