"""
Tests for bit.healthcheck — Docker container health check for the bot runner.

Verifies every exit path of main():
  exit(1) — no state file
  exit(1) — corrupt state file
  exit(1) — runner status is not "running"  (stopped / error / starting)
  exit(1) — runner state file is stale (age > run_interval_seconds * 3)
  exit(0) — runner is "running" and file is fresh
  exit(0) — runner is "running" and file_mtime is None (mtime unavailable, skip stale check)

The module calls BITConfig() internally. We monkeypatch it to point at a tmp_path
so tests are fully isolated and never touch real disk state.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from bit.config import BITConfig
from bit.healthcheck import main
from bit.services.runner_state import RunnerState, RunnerStateStore, RunnerStatus

_NOW = datetime.now(tz=timezone.utc)


def _config(tmp_path) -> BITConfig:
    return BITConfig(
        bybit_api_key="",
        bybit_api_secret="",
        runner_state_path=tmp_path / "runner_state.json",
        run_interval_seconds=300,
    )


def _write_state(path, status: RunnerStatus) -> None:
    state = RunnerState(updated_at=_NOW, status=status)
    RunnerStateStore.write(state, path)


# ── No file ────────────────────────────────────────────────────────────────

class TestNoStateFile:
    def test_exits_1_when_file_missing(self, tmp_path, monkeypatch):
        cfg = _config(tmp_path)
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


# ── Corrupt file ───────────────────────────────────────────────────────────

class TestCorruptStateFile:
    def test_exits_1_when_file_corrupt(self, tmp_path, monkeypatch):
        cfg = _config(tmp_path)
        (tmp_path / "runner_state.json").write_text("not valid json", encoding="utf-8")
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


# ── Wrong status ──────────────────────────────────────────────────────────

class TestWrongStatus:
    @pytest.mark.parametrize("status", [
        RunnerStatus.STOPPED,
        RunnerStatus.ERROR,
        RunnerStatus.STARTING,
        RunnerStatus.STOPPING,
    ])
    def test_exits_1_for_non_running_status(self, tmp_path, monkeypatch, status):
        cfg = _config(tmp_path)
        _write_state(tmp_path / "runner_state.json", status)
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


# ── Stale file ────────────────────────────────────────────────────────────

class TestStaleFile:
    def test_exits_1_when_state_is_stale(self, tmp_path, monkeypatch):
        """File age > run_interval_seconds * 3 → unhealthy."""
        cfg = _config(tmp_path)
        _write_state(tmp_path / "runner_state.json", RunnerStatus.RUNNING)
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)

        # Make RunnerStateStore.read() return a result with an old mtime.
        stale_mtime = _NOW - timedelta(seconds=cfg.run_interval_seconds * 3 + 1)

        from bit.services.runner_state import RunnerStateReadResult
        state = RunnerState(updated_at=_NOW, status=RunnerStatus.RUNNING)
        fake_result = RunnerStateReadResult(
            status="ok", state=state, file_mtime=stale_mtime
        )
        monkeypatch.setattr("bit.healthcheck.RunnerStateStore.read", lambda _path: fake_result)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    def test_exits_0_when_just_within_threshold(self, tmp_path, monkeypatch):
        """Age one second below the threshold is still healthy."""
        cfg = _config(tmp_path)
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)

        # 1 second below threshold: 300 * 3 - 1 = 899s
        fresh_enough_mtime = datetime.now(tz=timezone.utc) - timedelta(
            seconds=cfg.run_interval_seconds * 3 - 1
        )
        from bit.services.runner_state import RunnerStateReadResult
        state = RunnerState(updated_at=_NOW, status=RunnerStatus.RUNNING)
        fake_result = RunnerStateReadResult(
            status="ok", state=state, file_mtime=fresh_enough_mtime
        )
        monkeypatch.setattr("bit.healthcheck.RunnerStateStore.read", lambda _path: fake_result)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0


# ── Healthy ───────────────────────────────────────────────────────────────

class TestHealthy:
    def test_exits_0_when_running_and_fresh(self, tmp_path, monkeypatch):
        """Running + recently-written file → healthy."""
        cfg = _config(tmp_path)
        _write_state(tmp_path / "runner_state.json", RunnerStatus.RUNNING)
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)

        # File was written 5 seconds ago — well within the 900s window.
        fresh_mtime = datetime.now(tz=timezone.utc) - timedelta(seconds=5)
        from bit.services.runner_state import RunnerStateReadResult
        state = RunnerState(updated_at=_NOW, status=RunnerStatus.RUNNING)
        fake_result = RunnerStateReadResult(
            status="ok", state=state, file_mtime=fresh_mtime
        )
        monkeypatch.setattr("bit.healthcheck.RunnerStateStore.read", lambda _path: fake_result)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_exits_0_when_mtime_unavailable(self, tmp_path, monkeypatch):
        """If file_mtime is None, skip stale check and report healthy."""
        cfg = _config(tmp_path)
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)

        from bit.services.runner_state import RunnerStateReadResult
        state = RunnerState(updated_at=_NOW, status=RunnerStatus.RUNNING)
        fake_result = RunnerStateReadResult(
            status="ok", state=state, file_mtime=None
        )
        monkeypatch.setattr("bit.healthcheck.RunnerStateStore.read", lambda _path: fake_result)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_healthy_output_mentions_running(self, tmp_path, monkeypatch, capsys):
        """Healthy path prints something that says healthy."""
        cfg = _config(tmp_path)
        monkeypatch.setattr("bit.healthcheck.BITConfig", lambda: cfg)

        from bit.services.runner_state import RunnerStateReadResult
        state = RunnerState(updated_at=_NOW, status=RunnerStatus.RUNNING)
        fake_result = RunnerStateReadResult(
            status="ok", state=state, file_mtime=None
        )
        monkeypatch.setattr("bit.healthcheck.RunnerStateStore.read", lambda _path: fake_result)
        with pytest.raises(SystemExit):
            main()
        captured = capsys.readouterr()
        assert "healthy" in captured.out.lower() or "running" in captured.out.lower()
