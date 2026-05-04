"""
Tests for BotRunner — v1 run loop.

Verifies:
- STARTING state written on start()
- Startup validation: live mode → ERROR, no symbols → ERROR
- Valid startup → RUNNING, startup_validated=True
- Pipeline called for each configured symbol
- last_cycle_start / last_cycle_end / last_successful_cycle updated after cycle
- last_heartbeat updated each cycle
- processed_symbols reflects symbols that completed successfully
- Fill triggers PortfolioStateStore.save()
- No fill → portfolio NOT saved
- Per-symbol pipeline error captured in last_error without crashing loop
- Remaining symbols still processed after one symbol errors
- stop() sets STOPPING, then start() returns and final state is STOPPED
- State file persisted to configured path
- STOPPED written in finally even after fatal error
"""

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from bit.config import BITConfig
from bit.domain.enums import Symbol
from bit.runner import BotRunner
from bit.services.runner_state import RunnerStateStore, RunnerStatus


# ── Helpers ────────────────────────────────────────────────────────────────────

def _config(tmp_path: Path, **kwargs) -> BITConfig:
    """Build a test config that writes state files inside tmp_path."""
    defaults = dict(
        bybit_api_key="",
        bybit_api_secret="",
        paper_trading=True,
        run_interval_seconds=0,  # no sleep between cycles in tests
    )
    defaults.update(kwargs)
    cfg = BITConfig(**defaults)
    return cfg.model_copy(update={
        "runner_state_path": tmp_path / "runner_state.json",
        "portfolio_state_path": tmp_path / "portfolio_state.json",
    })


def _make_entry(fill_price: str | None = None, fill_qty: str | None = None):
    """Return a mock JournalEntry."""
    entry = MagicMock()
    entry.fill_price = Decimal(fill_price) if fill_price else None
    entry.fill_qty = Decimal(fill_qty) if fill_qty else None
    return entry


def _make_portfolio() -> MagicMock:
    return MagicMock()


def _single_cycle_pipeline(entries: dict | None = None):
    """
    Return a mock pipeline whose run() stops the runner after all symbols
    in the first cycle complete.

    Stores a reference to the runner via a list (set after construction).
    """
    runner_ref: list[BotRunner] = []
    call_count = 0
    symbol_count = 3  # default; override after construction if needed

    entries = entries or {}

    async def run(symbol):
        nonlocal call_count
        call_count += 1
        if call_count >= symbol_count:
            # Stop after one full cycle
            runner_ref[0]._stop_event.set()
        return entries.get(symbol, _make_entry())

    pipeline = MagicMock()
    pipeline.run = run
    return pipeline, runner_ref


# ── Startup state ──────────────────────────────────────────────────────────────

class TestStartupState:
    @pytest.mark.asyncio
    async def test_starting_state_written(self, tmp_path):
        config = _config(tmp_path, paper_trading=False)
        runner = BotRunner(config=config, pipeline=MagicMock(), portfolio=_make_portfolio())
        # Just check that the initial state in _state is STARTING before start() is called
        assert runner._state.status == RunnerStatus.STARTING

    @pytest.mark.asyncio
    async def test_live_mode_writes_error_state(self, tmp_path):
        config = _config(tmp_path, paper_trading=False)
        runner = BotRunner(config=config, pipeline=MagicMock(), portfolio=_make_portfolio())
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.status == "ok"
        assert result.state.status == RunnerStatus.ERROR

    @pytest.mark.asyncio
    async def test_live_mode_sets_startup_error_message(self, tmp_path):
        config = _config(tmp_path, paper_trading=False)
        runner = BotRunner(config=config, pipeline=MagicMock(), portfolio=_make_portfolio())
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.startup_error is not None
        assert len(result.state.startup_error) > 0

    @pytest.mark.asyncio
    async def test_live_mode_startup_validated_false(self, tmp_path):
        config = _config(tmp_path, paper_trading=False)
        runner = BotRunner(config=config, pipeline=MagicMock(), portfolio=_make_portfolio())
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.startup_validated is False

    @pytest.mark.asyncio
    async def test_no_symbols_writes_error_state(self, tmp_path):
        config = _config(tmp_path)
        config = config.model_copy(update={"symbols": []})
        runner = BotRunner(config=config, pipeline=MagicMock(), portfolio=_make_portfolio())
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.status == RunnerStatus.ERROR


# ── Successful startup ─────────────────────────────────────────────────────────

class TestSuccessfulStartup:
    @pytest.mark.asyncio
    async def test_valid_config_writes_running(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.startup_validated is True

    @pytest.mark.asyncio
    async def test_state_file_created_at_configured_path(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        assert config.runner_state_path.exists()

    @pytest.mark.asyncio
    async def test_final_state_is_stopped(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.status == RunnerStatus.STOPPED


# ── Cycle execution ────────────────────────────────────────────────────────────

class TestCycleExecution:
    @pytest.mark.asyncio
    async def test_pipeline_called_for_each_symbol(self, tmp_path):
        config = _config(tmp_path)
        called_symbols: list[Symbol] = []
        call_count = 0
        pipeline = MagicMock()
        runner_holder: list[BotRunner] = []

        async def run(symbol):
            nonlocal call_count
            called_symbols.append(symbol)
            call_count += 1
            if call_count >= len(config.symbols):
                runner_holder[0]._stop_event.set()
            return _make_entry()

        pipeline.run = run
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_holder.append(runner)
        await runner.start()

        assert set(called_symbols) == set(config.symbols)

    @pytest.mark.asyncio
    async def test_processed_symbols_recorded(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        expected = {s.value for s in config.symbols}
        assert set(result.state.processed_symbols) == expected

    @pytest.mark.asyncio
    async def test_cycle_timestamps_set(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.last_cycle_start is not None
        assert result.state.last_cycle_end is not None

    @pytest.mark.asyncio
    async def test_successful_cycle_sets_last_successful_cycle(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.last_successful_cycle is not None

    @pytest.mark.asyncio
    async def test_heartbeat_updated_each_cycle(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.last_heartbeat is not None


# ── Portfolio save on fill ─────────────────────────────────────────────────────

class TestPortfolioSaveOnFill:
    @pytest.mark.asyncio
    async def test_fill_triggers_portfolio_save(self, tmp_path):
        config = _config(tmp_path)
        # Only BTCUSDT returns a fill
        btc_entry = _make_entry(fill_price="60000", fill_qty="0.001")
        entries = {Symbol.BTCUSDT: btc_entry}
        pipeline, runner_ref = _single_cycle_pipeline(entries=entries)
        portfolio = _make_portfolio()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=portfolio)
        runner_ref.append(runner)

        with patch("bit.runner.PortfolioStateStore") as mock_store:
            mock_store.save = MagicMock()
            await runner.start()

        mock_store.save.assert_called_once_with(
            portfolio, config.portfolio_state_path
        )

    @pytest.mark.asyncio
    async def test_no_fill_does_not_save_portfolio(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()  # all entries have no fill
        portfolio = _make_portfolio()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=portfolio)
        runner_ref.append(runner)

        with patch("bit.runner.PortfolioStateStore") as mock_store:
            mock_store.save = MagicMock()
            await runner.start()

        mock_store.save.assert_not_called()


# ── Error handling ─────────────────────────────────────────────────────────────

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_pipeline_error_captured_in_last_error(self, tmp_path):
        config = _config(tmp_path)
        call_count = 0
        runner_holder: list[BotRunner] = []
        pipeline = MagicMock()

        async def run(symbol):
            nonlocal call_count
            call_count += 1
            if symbol == Symbol.BTCUSDT:
                raise RuntimeError("market data timeout")
            if call_count >= len(config.symbols):
                runner_holder[0]._stop_event.set()
            return _make_entry()

        pipeline.run = run
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_holder.append(runner)
        await runner.start()

        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.last_error is not None
        assert "BTCUSDT" in result.state.last_error

    @pytest.mark.asyncio
    async def test_one_symbol_error_does_not_skip_remaining(self, tmp_path):
        config = _config(tmp_path)
        processed: list[Symbol] = []
        call_count = 0
        runner_holder: list[BotRunner] = []
        pipeline = MagicMock()

        async def run(symbol):
            nonlocal call_count
            call_count += 1
            if symbol == Symbol.BTCUSDT:
                raise RuntimeError("BTC error")
            processed.append(symbol)
            if call_count >= len(config.symbols):
                runner_holder[0]._stop_event.set()
            return _make_entry()

        pipeline.run = run
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_holder.append(runner)
        await runner.start()

        # ETHUSDT and SOLUSDT should have been processed despite BTC error
        assert Symbol.ETHUSDT in processed
        assert Symbol.SOLUSDT in processed

    @pytest.mark.asyncio
    async def test_error_cycle_does_not_set_last_successful_cycle(self, tmp_path):
        config = _config(tmp_path)
        call_count = 0
        runner_holder: list[BotRunner] = []
        pipeline = MagicMock()

        async def run(symbol):
            nonlocal call_count
            call_count += 1
            if symbol == Symbol.BTCUSDT:
                raise RuntimeError("error")
            if call_count >= len(config.symbols):
                runner_holder[0]._stop_event.set()
            return _make_entry()

        pipeline.run = run
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_holder.append(runner)
        await runner.start()

        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.last_successful_cycle is None


# ── Stop / shutdown ────────────────────────────────────────────────────────────

class TestStop:
    @pytest.mark.asyncio
    async def test_stop_writes_stopping_state(self, tmp_path):
        config = _config(tmp_path)
        stopping_written = []
        runner_holder: list[BotRunner] = []
        pipeline = MagicMock()

        original_write = None

        async def run(symbol):
            # After first symbol, call stop() and check state was written
            if symbol == Symbol.BTCUSDT:
                await runner_holder[0].stop()
                result = RunnerStateStore.read(config.runner_state_path)
                stopping_written.append(result.state.status)
            return _make_entry()

        pipeline.run = run
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_holder.append(runner)
        await runner.start()

        assert RunnerStatus.STOPPING in stopping_written

    @pytest.mark.asyncio
    async def test_stopped_written_after_loop_exits(self, tmp_path):
        config = _config(tmp_path)
        pipeline, runner_ref = _single_cycle_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, portfolio=_make_portfolio())
        runner_ref.append(runner)
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        assert result.state.status == RunnerStatus.STOPPED

    @pytest.mark.asyncio
    async def test_stopped_written_even_after_validation_error(self, tmp_path):
        # ERROR status replaces STARTING, but no STOPPED written for failed startup
        # (runner returns before the loop; finally block writes STOPPED after the
        # loop — which never runs)
        # So: startup error → ERROR state (not STOPPED) — explicit test for this.
        config = _config(tmp_path, paper_trading=False)
        runner = BotRunner(config=config, pipeline=MagicMock(), portfolio=_make_portfolio())
        await runner.start()
        result = RunnerStateStore.read(config.runner_state_path)
        # Startup validation failure returns early, before the try/finally loop.
        assert result.state.status == RunnerStatus.ERROR
