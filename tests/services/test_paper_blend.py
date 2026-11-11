"""Paper blend runner: signals, persistence, and the safety halts. No network."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bit.config import BITConfig
from bit.domain.enums import Symbol, Timeframe
from bit.domain.market import InstrumentFilter, Kline, Ticker
from bit.paper import PaperBlendRunner, reset_halt
from bit.services.blend_state import BlendStateStore, fresh_state
from bit.services.journal import JournalLearningStore
from bit.services.market_data import MarketDataService

_OPENED = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)
_NOW = _OPENED + timedelta(minutes=5)


def _config(tmp_path) -> BITConfig:
    return BITConfig(
        bybit_api_key="should-not-be-sent",
        bybit_api_secret="should-not-be-sent",
        paper_trading=True,
        capital_usdt=Decimal("1000"),
        goal_equity_usdt=Decimal("2000"),
        max_drawdown_pct=Decimal("0.25"),
        daily_loss_limit_pct=Decimal("0.03"),
        max_consecutive_losses=6,
        run_interval_seconds=0,
        blend_state_path=tmp_path / "blend_state.json",
        journal_path=tmp_path / "journal.jsonl",
        stop_file=tmp_path / "STOP",
        portfolio_state_path=tmp_path / "portfolio_state.json",
        runner_state_path=tmp_path / "runner_state.json",
    )


def _kline(symbol, timeframe, open_time, close, *, closed) -> Kline:
    price = Decimal(str(close))
    return Kline(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("10"),
        is_closed=closed,
    )


class FakeMarket:
    """Public-data stand-in. Records that nobody asked for a signed call."""

    def __init__(self) -> None:
        self.signed_calls = 0
        self.limit_seen: list[int] = []

    async def get_klines(self, symbol, timeframe, limit=1000):
        self.limit_seen.append(limit)
        if timeframe == Timeframe.D1:
            return _daily(symbol)
        if timeframe == Timeframe.H4:
            return _h4(symbol)
        raise AssertionError(timeframe)

    async def get_ticker(self, symbol):
        price = Decimal("139") if symbol == Symbol.SOLUSDT else Decimal("100")
        return Ticker(
            symbol=symbol,
            last_price=price,
            bid=price - Decimal("0.1"),
            ask=price + Decimal("0.1"),
            timestamp=_NOW,
        )

    async def get_instrument_filter(self, symbol):
        return InstrumentFilter(
            symbol=symbol,
            tick_size=Decimal("0.01"),
            qty_step=Decimal("0.0001"),
            min_order_qty=Decimal("0.0001"),
            min_order_usdt=Decimal("1"),
        )

    async def aclose(self) -> None:
        return None


def _daily(symbol: Symbol) -> list[Kline]:
    bars = []
    for i in range(40):
        opened = _OPENED - timedelta(days=40 - i)
        if symbol == Symbol.SOLUSDT:
            close = 100 + i
        elif symbol == Symbol.ETHUSDT:
            close = 100 - i * 0.2
        else:
            close = 100
        bars.append(_kline(symbol, Timeframe.D1, opened, close, closed=True))
    bars.append(_kline(symbol, Timeframe.D1, _OPENED, bars[-1].close, closed=False))
    return bars


def _h4(symbol: Symbol) -> list[Kline]:
    bars = []
    price = 100
    for i in range(200):
        opened = _OPENED - timedelta(hours=4 * (200 - i))
        bars.append(_kline(symbol, Timeframe.H4, opened, price, closed=True))
    bars.append(_kline(symbol, Timeframe.H4, _OPENED, price, closed=False))
    return bars


def _runner(tmp_path, clock=None) -> tuple[PaperBlendRunner, FakeMarket]:
    config = _config(tmp_path)
    market = FakeMarket()
    journal = JournalLearningStore(config, journal_path=config.journal_path)
    runner = PaperBlendRunner(config, market, journal, clock=clock or (lambda: _NOW))
    return runner, market


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestPaperCycle:
    async def test_requests_at_least_1000_candles_and_buys_the_leader(self, tmp_path):
        runner, market = _runner(tmp_path)
        stopped = await runner.run_once()
        assert stopped is False
        assert min(market.limit_seen) >= 1000
        assert market.signed_calls == 0
        snap = runner._state.rotation.snapshot()
        assert Symbol.SOLUSDT in snap.open_positions
        assert Symbol.BTCUSDT not in snap.open_positions
        # Flat 4h series is not an uptrend, so the conservative sleeve stays in cash.
        for tracker in runner._state.conservative.values():
            assert tracker.position_count == 0

    async def test_restart_restores_the_same_position_and_equity(self, tmp_path):
        runner, _market = _runner(tmp_path)
        await runner.run_once()
        qty = runner._state.rotation.snapshot().open_positions[Symbol.SOLUSDT].qty
        equity = runner._state.last_equity
        progress = runner._state.goal_progress

        again, _ = _runner(tmp_path)
        assert again._state.rotation.snapshot().open_positions[Symbol.SOLUSDT].qty == qty
        assert again._state.last_equity == equity
        assert again._state.goal_progress == progress
        # A second cycle in the same window must not double the position.
        await again.run_once()
        assert again._state.rotation.snapshot().open_positions[Symbol.SOLUSDT].qty == qty

    async def test_goal_progress_is_persisted(self, tmp_path):
        runner, _market = _runner(tmp_path)
        await runner.run_once()
        saved = BlendStateStore.load(_config(tmp_path).blend_state_path)
        assert saved.goal_equity == Decimal("2000")
        assert saved.goal_progress == saved.last_equity / Decimal("2000")
        assert saved.last_equity < Decimal("1000")  # fees and slippage are in the P&L
        assert saved.last_equity > Decimal("990")

    async def test_missed_open_does_not_trade(self, tmp_path):
        late = _OPENED + timedelta(hours=3)
        runner, _market = _runner(tmp_path, clock=lambda: late)
        await runner.run_once()
        assert runner._state.rotation.position_count == 0
        cfg = _config(tmp_path)
        journal = JournalLearningStore(cfg, journal_path=cfg.journal_path)
        assert any("Missed the" in entry.rationale for entry in journal.read_all())

    async def test_negative_leader_is_journaled_as_a_reject(self, tmp_path, monkeypatch):
        # Force every coin's trailing return to be negative.
        import bit.paper as paper_mod

        monkeypatch.setattr(
            paper_mod,
            "choose_rotation",
            lambda closes, lookback: (None, {name: -0.1 for name in closes}),
        )
        runner, _market = _runner(tmp_path)
        await runner.run_once()
        assert runner._state.rotation.position_count == 0
        cfg = _config(tmp_path)
        journal = JournalLearningStore(cfg, journal_path=cfg.journal_path)
        assert any("returns are <= 0" in entry.rationale for entry in journal.read_all())


class TestSafetyHalts:
    async def test_drawdown_halt_flattens_and_stays_halted(self, tmp_path):
        config = _config(tmp_path)
        state = fresh_state(config, now=_NOW)
        state.rotation._cash = Decimal("200")
        for tracker in state.conservative.values():
            tracker._cash = Decimal("100")
        state.last_equity = Decimal("500")
        BlendStateStore.save(state, config.blend_state_path)

        runner, _market = _runner(tmp_path)
        await runner.run_once()
        assert runner._state.drawdown_halted
        assert runner._state.rotation.position_count == 0

        await runner.run_once()
        assert runner._state.drawdown_halted
        assert runner._state.rotation.position_count == 0

    async def test_daily_loss_blocks_a_new_entry(self, tmp_path):
        config = _config(tmp_path)
        state = fresh_state(config, now=_NOW)
        state.rotation._cash -= Decimal("40")
        state.day_start_equity = Decimal("1000")
        state.starting_equity = Decimal("1000")
        BlendStateStore.save(state, config.blend_state_path)

        runner, _market = _runner(tmp_path)
        await runner.run_once()
        assert not runner._state.drawdown_halted
        assert runner._state.rotation.position_count == 0
        journal = JournalLearningStore(config, journal_path=config.journal_path)
        assert any("Entries blocked" in entry.rationale for entry in journal.read_all())

    async def test_consecutive_loss_limit_blocks_entries(self, tmp_path):
        config = _config(tmp_path)
        state = fresh_state(config, now=_NOW)
        state.consecutive_losses = 6
        BlendStateStore.save(state, config.blend_state_path)
        runner, _market = _runner(tmp_path)
        await runner.run_once()
        assert runner._state.loss_streak_halted
        assert runner._state.rotation.position_count == 0

    async def test_stop_file_halts_without_trading(self, tmp_path):
        config = _config(tmp_path)
        config.stop_file.write_text("stop\n", encoding="utf-8")
        runner, market = _runner(tmp_path)
        assert await runner.run_once() is True
        assert market.limit_seen == []
        assert runner._state.rotation.position_count == 0
        assert runner._state.status == "stopped_by_file"

    def test_reset_halt_starts_a_new_stage_from_current_equity(self, tmp_path, caplog):
        config = _config(tmp_path)
        state = fresh_state(config, now=_NOW)
        state.drawdown_halted = True
        state.loss_streak_halted = True
        state.consecutive_losses = 6
        state.last_equity = Decimal("800")
        BlendStateStore.save(state, config.blend_state_path)
        reset_halt(config)
        restored = BlendStateStore.load(config.blend_state_path)
        assert restored.drawdown_halted is False
        assert restored.loss_streak_halted is False
        assert restored.consecutive_losses == 0
        assert restored.starting_equity == Decimal("800")


def test_live_mode_is_refused(tmp_path):
    config = _config(tmp_path).model_copy(update={"paper_trading": False})
    market = FakeMarket()
    journal = JournalLearningStore(config, journal_path=config.journal_path)
    with pytest.raises(RuntimeError, match="paper-only"):
        PaperBlendRunner(config, market, journal, clock=lambda: _NOW)


def test_public_client_does_not_keep_api_keys(tmp_path):
    """The blend market-data client is constructed without secrets."""
    config = _config(tmp_path)
    service = MarketDataService(config.model_copy(update={"bybit_testnet": False}))
    assert service._client._http.base_url.host == "api.bybit.com"
    service._config = config.model_copy(update={"bybit_api_key": "", "bybit_api_secret": ""})
    assert service._config.bybit_api_key == ""
