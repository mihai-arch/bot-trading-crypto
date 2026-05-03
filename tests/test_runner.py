"""
Tests for BotRunner, RunnerState, and dashboard integration.

All pipeline calls are mocked — no real network I/O.
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bit.config import BITConfig
from bit.domain.enums import DecisionState, Symbol
from bit.domain.journal import JournalEntry
from bit.runner import BotRunner, RunnerState, RunnerStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(**overrides) -> BITConfig:
    """Return a BITConfig suitable for testing (paper mode, fast interval)."""
    defaults = dict(
        paper_trading=True,
        run_interval_seconds=1,
        symbols=[Symbol.BTCUSDT, Symbol.ETHUSDT],
        bybit_api_key="",
        bybit_testnet=True,
    )
    defaults.update(overrides)
    return BITConfig(**defaults)


def _make_journal_entry(symbol: Symbol = Symbol.BTCUSDT) -> JournalEntry:
    """Minimal JournalEntry for mocking pipeline.run() returns."""
    return JournalEntry(
        entry_id="test-id",
        symbol=symbol,
        cycle_timestamp=datetime.now(tz=timezone.utc),
        decision_state=DecisionState.REJECT,
        contributing_strategies=[],
        composite_score=Decimal("0.0"),
        rationale="test",
        is_paper=True,
        raw_signal_scores={},
    )


def _make_pipeline(side_effects: dict[Symbol, Exception | None] | None = None) -> MagicMock:
    """
    Return a mock Pipeline.

    side_effects: {Symbol: Exception} means that symbol raises;
                  {Symbol: None} means it returns a JournalEntry normally.
                  Symbols not in the dict return a JournalEntry normally.
    """
    pipeline = MagicMock()
    side_effects = side_effects or {}

    async def _run(symbol: Symbol) -> JournalEntry:
        exc = side_effects.get(symbol)
        if exc is not None:
            raise exc
        return _make_journal_entry(symbol)

    pipeline.run = AsyncMock(side_effect=_run)
    return pipeline


# ── RunnerState tests ─────────────────────────────────────────────────────────

class TestRunnerState:
    def test_initial_defaults(self):
        state = RunnerState(mode="PAPER")
        assert state.mode == "PAPER"
        assert state.status == RunnerStatus.STOPPED
        assert state.last_heartbeat is None
        assert state.last_cycle_start is None
        assert state.last_cycle_end is None
        assert state.last_successful_cycle is None
        assert state.last_error_message is None
        assert state.last_error_time is None
        assert state.symbols_last_cycle == []

    def test_to_dict_all_none(self):
        state = RunnerState(mode="PAPER")
        d = state.to_dict()
        assert d["mode"] == "PAPER"
        assert d["status"] == "stopped"
        assert d["last_heartbeat"] is None
        assert d["last_cycle_start"] is None
        assert d["symbols_last_cycle"] == []

    def test_to_dict_with_timestamps(self):
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        state = RunnerState(
            mode="PAPER",
            status=RunnerStatus.RUNNING,
            last_heartbeat=now,
            symbols_last_cycle=["BTCUSDT"],
        )
        d = state.to_dict()
        assert d["status"] == "running"
        assert d["last_heartbeat"] == now.isoformat()
        assert d["symbols_last_cycle"] == ["BTCUSDT"]

    def test_to_dict_is_json_serializable(self):
        state = RunnerState(
            mode="PAPER",
            status=RunnerStatus.ERROR,
            last_error_message="Test error",
            last_error_time=datetime.now(tz=timezone.utc),
            last_heartbeat=datetime.now(tz=timezone.utc),
        )
        # Should not raise
        json.dumps(state.to_dict())


# ── BotRunner construction ────────────────────────────────────────────────────

class TestBotRunnerConstruction:
    def test_refuses_live_mode(self):
        config = _make_config(paper_trading=False)
        pipeline = _make_pipeline()
        with pytest.raises(ValueError, match="paper_trading=True"):
            BotRunner(config=config, pipeline=pipeline)

    def test_defaults_symbols_from_config(self):
        config = _make_config(symbols=[Symbol.BTCUSDT, Symbol.SOLUSDT])
        pipeline = _make_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline)
        assert runner.state.mode == "PAPER"
        assert runner.state.status == RunnerStatus.STOPPED

    def test_custom_symbols_override(self):
        config = _make_config()
        pipeline = _make_pipeline()
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            symbols=[Symbol.SOLUSDT],
        )
        # The internal symbols list should only contain SOLUSDT
        assert runner._symbols == [Symbol.SOLUSDT]

    def test_custom_interval_override(self):
        config = _make_config(run_interval_seconds=60)
        pipeline = _make_pipeline()
        runner = BotRunner(config=config, pipeline=pipeline, run_interval_seconds=5)
        assert runner._interval == 5

    def test_injected_state_is_used(self):
        config = _make_config()
        pipeline = _make_pipeline()
        state = RunnerState(mode="PAPER", status=RunnerStatus.ERROR)
        runner = BotRunner(config=config, pipeline=pipeline, state=state)
        assert runner.state is state

    def test_heartbeat_path_override(self, tmp_path):
        config = _make_config()
        pipeline = _make_pipeline()
        hb = tmp_path / "custom_hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=hb)
        assert runner._heartbeat_path == hb


# ── BotRunner._run_cycle ──────────────────────────────────────────────────────

class TestRunnerCycle:
    @pytest.mark.asyncio
    async def test_successful_cycle_updates_state(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT, Symbol.ETHUSDT])
        pipeline = _make_pipeline()
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            heartbeat_path=tmp_path / "hb.json",
        )
        runner._running = True  # pretend running so status stays RUNNING after cycle

        await runner._run_cycle()

        assert runner.state.last_cycle_start is not None
        assert runner.state.last_cycle_end is not None
        assert runner.state.last_successful_cycle is not None
        assert runner.state.last_error_message is None
        assert set(runner.state.symbols_last_cycle) == {"BTCUSDT", "ETHUSDT"}
        assert runner.state.status == RunnerStatus.RUNNING

    @pytest.mark.asyncio
    async def test_pipeline_called_per_symbol(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT, Symbol.ETHUSDT, Symbol.SOLUSDT])
        pipeline = _make_pipeline()
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            heartbeat_path=tmp_path / "hb.json",
        )
        runner._running = True

        await runner._run_cycle()

        assert pipeline.run.call_count == 3
        called_symbols = {call.args[0] for call in pipeline.run.call_args_list}
        assert called_symbols == {Symbol.BTCUSDT, Symbol.ETHUSDT, Symbol.SOLUSDT}

    @pytest.mark.asyncio
    async def test_per_symbol_error_does_not_abort_others(self, tmp_path):
        """An error on BTCUSDT should not prevent ETHUSDT from running."""
        config = _make_config(symbols=[Symbol.BTCUSDT, Symbol.ETHUSDT])
        pipeline = _make_pipeline(side_effects={Symbol.BTCUSDT: RuntimeError("network fail")})
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            heartbeat_path=tmp_path / "hb.json",
        )
        runner._running = True

        await runner._run_cycle()

        # ETHUSDT should still have run
        assert "ETHUSDT" in runner.state.symbols_last_cycle
        assert "BTCUSDT" not in runner.state.symbols_last_cycle

    @pytest.mark.asyncio
    async def test_per_symbol_error_records_state(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT])
        pipeline = _make_pipeline(side_effects={Symbol.BTCUSDT: ValueError("bad data")})
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            heartbeat_path=tmp_path / "hb.json",
        )
        runner._running = True

        await runner._run_cycle()

        assert runner.state.status == RunnerStatus.ERROR
        assert "ValueError" in runner.state.last_error_message
        assert "bad data" in runner.state.last_error_message
        assert runner.state.last_error_time is not None

    @pytest.mark.asyncio
    async def test_error_does_not_update_last_successful_cycle(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT])
        pipeline = _make_pipeline(side_effects={Symbol.BTCUSDT: RuntimeError("fail")})
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            heartbeat_path=tmp_path / "hb.json",
        )
        runner._running = True

        await runner._run_cycle()

        assert runner.state.last_successful_cycle is None

    @pytest.mark.asyncio
    async def test_cycle_timestamps_ordered(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT])
        pipeline = _make_pipeline()
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            heartbeat_path=tmp_path / "hb.json",
        )
        runner._running = True

        await runner._run_cycle()

        assert runner.state.last_cycle_start <= runner.state.last_cycle_end
        assert runner.state.last_cycle_end <= runner.state.last_heartbeat

    @pytest.mark.asyncio
    async def test_stop_mid_cycle_skips_remaining(self, tmp_path):
        """If stop() is called, symbols after the current one are skipped."""
        call_log: list[Symbol] = []

        async def _run_with_stop(symbol: Symbol) -> JournalEntry:
            call_log.append(symbol)
            # Stop after the first symbol
            runner.stop()
            return _make_journal_entry(symbol)

        config = _make_config(symbols=[Symbol.BTCUSDT, Symbol.ETHUSDT, Symbol.SOLUSDT])
        pipeline = MagicMock()
        pipeline.run = AsyncMock(side_effect=_run_with_stop)
        runner = BotRunner(
            config=config,
            pipeline=pipeline,
            heartbeat_path=tmp_path / "hb.json",
        )
        runner._running = True

        await runner._run_cycle()

        assert len(call_log) == 1


# ── Heartbeat file ────────────────────────────────────────────────────────────

class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_written_after_cycle(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT])
        pipeline = _make_pipeline()
        hb_path = tmp_path / "hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=hb_path)
        runner._running = True

        await runner._run_cycle()

        assert hb_path.exists()
        data = json.loads(hb_path.read_text())
        assert data["mode"] == "PAPER"
        assert data["status"] == "running"
        assert "BTCUSDT" in data["symbols_last_cycle"]

    @pytest.mark.asyncio
    async def test_heartbeat_on_error_cycle(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT])
        pipeline = _make_pipeline(side_effects={Symbol.BTCUSDT: RuntimeError("x")})
        hb_path = tmp_path / "hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=hb_path)
        runner._running = True

        await runner._run_cycle()

        data = json.loads(hb_path.read_text())
        assert data["status"] == "error"
        assert data["last_error_message"] is not None

    def test_heartbeat_never_raises_on_bad_path(self):
        """_write_heartbeat must not raise even if the path is unwritable."""
        config = _make_config()
        pipeline = _make_pipeline()
        # Use a path that can't be created (file as parent)
        bad_path = Path("/dev/null/cannot/exist/hb.json")
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=bad_path)
        # Should not raise
        runner._write_heartbeat()

    @pytest.mark.asyncio
    async def test_heartbeat_creates_parent_dirs(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT])
        pipeline = _make_pipeline()
        nested = tmp_path / "a" / "b" / "c" / "hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=nested)
        runner._running = True

        await runner._run_cycle()

        assert nested.exists()


# ── start() / stop() lifecycle ────────────────────────────────────────────────

class TestRunnerLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_status_running_then_stopped(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT], run_interval_seconds=0)
        pipeline = _make_pipeline()
        hb_path = tmp_path / "hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=hb_path)

        run_count = 0

        async def _run(symbol: Symbol) -> JournalEntry:
            nonlocal run_count
            run_count += 1
            runner.stop()  # stop after first cycle
            return _make_journal_entry(symbol)

        pipeline.run = AsyncMock(side_effect=_run)

        await runner.start()

        assert runner.state.status == RunnerStatus.STOPPED
        assert run_count == 1

    @pytest.mark.asyncio
    async def test_start_writes_final_heartbeat_on_stop(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT], run_interval_seconds=0)
        pipeline = _make_pipeline()
        hb_path = tmp_path / "hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=hb_path)

        async def _stop_after_first(symbol: Symbol) -> JournalEntry:
            runner.stop()
            return _make_journal_entry(symbol)

        pipeline.run = AsyncMock(side_effect=_stop_after_first)

        await runner.start()

        data = json.loads(hb_path.read_text())
        assert data["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_cancelled_error_stops_cleanly(self, tmp_path):
        config = _make_config(symbols=[Symbol.BTCUSDT], run_interval_seconds=100)
        pipeline = _make_pipeline()
        hb_path = tmp_path / "hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=hb_path)

        async def _run_and_cancel():
            task = asyncio.create_task(runner.start())
            # Let the first cycle start
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await _run_and_cancel()

        assert runner.state.status == RunnerStatus.STOPPED

    @pytest.mark.asyncio
    async def test_multiple_cycles_run(self, tmp_path):
        """Runner executes multiple cycles before stop() is called."""
        config = _make_config(symbols=[Symbol.BTCUSDT], run_interval_seconds=0)
        pipeline = _make_pipeline()
        hb_path = tmp_path / "hb.json"
        runner = BotRunner(config=config, pipeline=pipeline, heartbeat_path=hb_path)

        cycle_count = 0

        async def _run(symbol: Symbol) -> JournalEntry:
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count >= 3:
                runner.stop()
            return _make_journal_entry(symbol)

        pipeline.run = AsyncMock(side_effect=_run)

        await runner.start()

        assert cycle_count == 3


# ── Config-driven behavior ────────────────────────────────────────────────────

class TestConfigDriven:
    def test_interval_from_config(self):
        config = _make_config(run_interval_seconds=42)
        runner = BotRunner(config=config, pipeline=_make_pipeline())
        assert runner._interval == 42

    def test_symbols_from_config(self):
        config = _make_config(symbols=[Symbol.SOLUSDT])
        runner = BotRunner(config=config, pipeline=_make_pipeline())
        assert runner._symbols == [Symbol.SOLUSDT]

    def test_heartbeat_path_from_config(self, tmp_path):
        hb = tmp_path / "custom.json"
        config = _make_config(heartbeat_path=hb)
        runner = BotRunner(config=config, pipeline=_make_pipeline())
        assert runner._heartbeat_path == hb


# ── Dashboard integration ─────────────────────────────────────────────────────

from bit.dashboard.service import DashboardService
from bit.services.journal import JournalLearningStore as _JournalStore


class TestDashboardIntegration:
    """Verify that DashboardService correctly reflects RunnerState."""

    def _make_service(self, runner_state=None):
        config = _make_config()
        journal = MagicMock(spec=_JournalStore)
        journal.read_all.return_value = []
        journal.path = Path("data/journal.jsonl")

        return DashboardService(
            config=config,
            journal=journal,
            runner_state=runner_state,
        )

    def test_loop_running_false_without_runner(self):
        service = self._make_service(runner_state=None)
        snap = service.build_snapshot()
        assert snap.loop_running is False
        assert snap.runner is None

    def test_loop_running_false_when_runner_stopped(self):
        state = RunnerState(mode="PAPER", status=RunnerStatus.STOPPED)
        service = self._make_service(runner_state=state)
        snap = service.build_snapshot()
        assert snap.loop_running is False

    def test_loop_running_true_when_runner_active(self):
        state = RunnerState(mode="PAPER", status=RunnerStatus.RUNNING)
        service = self._make_service(runner_state=state)
        snap = service.build_snapshot()
        assert snap.loop_running is True

    def test_runner_info_populated(self):
        now = datetime.now(tz=timezone.utc)
        state = RunnerState(
            mode="PAPER",
            status=RunnerStatus.RUNNING,
            last_heartbeat=now,
            symbols_last_cycle=["BTCUSDT"],
        )
        service = self._make_service(runner_state=state)
        snap = service.build_snapshot()

        assert snap.runner is not None
        assert snap.runner.status == "running"
        assert snap.runner.mode == "PAPER"
        assert snap.runner.last_heartbeat == now
        assert "BTCUSDT" in snap.runner.symbols_last_cycle

    def test_scheduler_health_item_implemented_when_running(self):
        from bit.dashboard.models import ServiceStatus

        state = RunnerState(mode="PAPER", status=RunnerStatus.RUNNING)
        service = self._make_service(runner_state=state)
        snap = service.build_snapshot()

        scheduler_item = next(h for h in snap.health if "Scheduler" in h.name)
        assert scheduler_item.status == ServiceStatus.IMPLEMENTED

    def test_scheduler_health_item_missing_without_runner(self):
        from bit.dashboard.models import ServiceStatus

        service = self._make_service(runner_state=None)
        snap = service.build_snapshot()

        scheduler_item = next(h for h in snap.health if "Scheduler" in h.name)
        assert scheduler_item.status == ServiceStatus.MISSING

    def test_scheduler_readiness_ready_when_running(self):
        from bit.dashboard.models import ReadinessStatus

        state = RunnerState(mode="PAPER", status=RunnerStatus.RUNNING)
        service = self._make_service(runner_state=state)
        snap = service.build_snapshot()

        scheduler_item = next(r for r in snap.readiness if r.key == "scheduler")
        assert scheduler_item.status == ReadinessStatus.READY

    def test_runtime_gaps_exclude_scheduler_when_running(self):
        state = RunnerState(mode="PAPER", status=RunnerStatus.RUNNING)
        service = self._make_service(runner_state=state)
        snap = service.build_snapshot()

        gap_labels = [g.label for g in snap.runtime_gaps]
        assert not any("scheduler" in label.lower() for label in gap_labels)

    def test_runtime_gaps_include_scheduler_when_stopped(self):
        service = self._make_service(runner_state=None)
        snap = service.build_snapshot()

        gap_labels = [g.label for g in snap.runtime_gaps]
        assert any("scheduler" in label.lower() for label in gap_labels)
