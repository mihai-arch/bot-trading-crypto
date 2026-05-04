"""
Tests for RunnerStateStore — runner lifecycle state persistence.

Verifies:
- write() creates a JSON file
- write() creates parent directories
- write() overwrites existing cleanly
- write() leaves no .tmp on success
- All RunnerState fields serialise correctly
- read() returns not_found when file absent
- read() returns ok with correct state
- read() returns corrupt on invalid JSON
- read() returns corrupt on invalid schema
- read() includes file_mtime when file exists
- read() does not raise on corrupt file
- Round-trip: all fields preserved
- RunnerStatus enum values serialise as strings
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bit.services.runner_state import RunnerState, RunnerStateStore, RunnerStatus


_TS = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)


def _minimal_state(status: RunnerStatus = RunnerStatus.RUNNING) -> RunnerState:
    return RunnerState(
        updated_at=_TS,
        status=status,
    )


def _full_state() -> RunnerState:
    return RunnerState(
        updated_at=_TS,
        status=RunnerStatus.RUNNING,
        startup_validated=True,
        startup_error=None,
        last_heartbeat=_TS,
        last_cycle_start=_TS,
        last_cycle_end=_TS,
        last_successful_cycle=_TS,
        last_error=None,
        processed_symbols=["BTCUSDT", "ETHUSDT"],
    )


# ── write() ────────────────────────────────────────────────────────────────────

class TestWrite:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(), path)
        assert path.exists()

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "runner_state.json"
        RunnerStateStore.write(_minimal_state(), path)
        assert path.exists()

    def test_file_is_valid_json(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(), path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_status_field_saved(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(RunnerStatus.STOPPED), path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == "stopped"

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(RunnerStatus.STARTING), path)
        RunnerStateStore.write(_minimal_state(RunnerStatus.RUNNING), path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == "running"

    def test_no_tmp_file_after_success(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(), path)
        assert not path.with_suffix(".tmp").exists()

    def test_all_status_values_serialise(self, tmp_path):
        for status in RunnerStatus:
            path = tmp_path / f"state_{status}.json"
            RunnerStateStore.write(_minimal_state(status), path)
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["status"] == str(status)


# ── read() ─────────────────────────────────────────────────────────────────────

class TestRead:
    def test_not_found_when_absent(self, tmp_path):
        result = RunnerStateStore.read(tmp_path / "missing.json")
        assert result.status == "not_found"
        assert result.state is None

    def test_returns_ok_on_valid_file(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(), path)
        result = RunnerStateStore.read(path)
        assert result.status == "ok"
        assert result.state is not None

    def test_status_round_trips(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(RunnerStatus.ERROR), path)
        result = RunnerStateStore.read(path)
        assert result.state.status == RunnerStatus.ERROR

    def test_corrupt_json_returns_corrupt(self, tmp_path):
        path = tmp_path / "runner_state.json"
        path.write_text("not json", encoding="utf-8")
        result = RunnerStateStore.read(path)
        assert result.status == "corrupt"
        assert result.state is None

    def test_corrupt_has_error_message(self, tmp_path):
        path = tmp_path / "runner_state.json"
        path.write_text("{broken", encoding="utf-8")
        result = RunnerStateStore.read(path)
        assert result.error is not None and len(result.error) > 0

    def test_invalid_schema_returns_corrupt(self, tmp_path):
        path = tmp_path / "runner_state.json"
        path.write_text(json.dumps({"status": "unknown_status_value_xyz", "updated_at": "not-a-date"}),
                        encoding="utf-8")
        result = RunnerStateStore.read(path)
        assert result.status == "corrupt"

    def test_does_not_raise_on_corrupt(self, tmp_path):
        path = tmp_path / "runner_state.json"
        path.write_text("garbage garbage garbage", encoding="utf-8")
        result = RunnerStateStore.read(path)
        assert result.status == "corrupt"

    def test_file_mtime_included(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(), path)
        result = RunnerStateStore.read(path)
        assert result.file_mtime is not None

    def test_file_mtime_is_utc(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(), path)
        result = RunnerStateStore.read(path)
        assert result.file_mtime.tzinfo is not None

    def test_file_mtime_present_even_on_corrupt(self, tmp_path):
        path = tmp_path / "runner_state.json"
        path.write_text("garbage", encoding="utf-8")
        result = RunnerStateStore.read(path)
        assert result.file_mtime is not None

    def test_not_found_has_no_mtime(self, tmp_path):
        result = RunnerStateStore.read(tmp_path / "missing.json")
        assert result.file_mtime is None


# ── Round-trip ─────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_minimal_state_round_trip(self, tmp_path):
        path = tmp_path / "runner_state.json"
        original = _minimal_state(RunnerStatus.STOPPED)
        RunnerStateStore.write(original, path)
        result = RunnerStateStore.read(path)
        assert result.status == "ok"
        assert result.state.status == RunnerStatus.STOPPED
        assert result.state.updated_at == original.updated_at

    def test_full_state_round_trip(self, tmp_path):
        path = tmp_path / "runner_state.json"
        original = _full_state()
        RunnerStateStore.write(original, path)
        result = RunnerStateStore.read(path)
        state = result.state
        assert state.startup_validated is True
        assert state.startup_error is None
        assert state.last_heartbeat == _TS
        assert state.last_cycle_start == _TS
        assert state.last_cycle_end == _TS
        assert state.last_successful_cycle == _TS
        assert state.last_error is None
        assert state.processed_symbols == ["BTCUSDT", "ETHUSDT"]

    def test_error_state_round_trip(self, tmp_path):
        path = tmp_path / "runner_state.json"
        original = RunnerState(
            updated_at=_TS,
            status=RunnerStatus.ERROR,
            startup_validated=True,
            last_error="Connection timeout after 30s",
        )
        RunnerStateStore.write(original, path)
        result = RunnerStateStore.read(path)
        assert result.state.status == RunnerStatus.ERROR
        assert result.state.last_error == "Connection timeout after 30s"

    def test_none_fields_preserved(self, tmp_path):
        path = tmp_path / "runner_state.json"
        original = _minimal_state()
        RunnerStateStore.write(original, path)
        result = RunnerStateStore.read(path)
        assert result.state.last_heartbeat is None
        assert result.state.last_error is None
        assert result.state.startup_error is None

    def test_overwrite_with_updated_state(self, tmp_path):
        path = tmp_path / "runner_state.json"
        RunnerStateStore.write(_minimal_state(RunnerStatus.STARTING), path)
        RunnerStateStore.write(_minimal_state(RunnerStatus.RUNNING), path)
        result = RunnerStateStore.read(path)
        assert result.state.status == RunnerStatus.RUNNING
