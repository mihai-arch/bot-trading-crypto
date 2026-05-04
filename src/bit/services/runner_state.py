"""
RunnerState — persisted runtime state for the BIT run loop.

Provides a small JSON file the runner writes during operation so that:
  - The dashboard can show loop status even when the runner is not in-process.
  - Restart behavior is visible and inspectable by hand.

File format: JSON (Pydantic model serialised via model_dump_json).
Write strategy: atomic (write .tmp then rename).

Schema (v1):
  {
    "version": 1,
    "updated_at": "<ISO datetime>",
    "status": "running" | "stopped" | "starting" | "error",
    "startup_validated": true | false,
    "startup_error": null | "<message>",
    "last_heartbeat": "<ISO datetime>" | null,
    "last_cycle_start": "<ISO datetime>" | null,
    "last_cycle_end": "<ISO datetime>" | null,
    "last_successful_cycle": "<ISO datetime>" | null,
    "last_error": null | "<message>",
    "processed_symbols": ["BTCUSDT", ...]
  }

Usage (runner):
    state = RunnerState(
        updated_at=datetime.now(tz=timezone.utc),
        status=RunnerStatus.RUNNING,
        startup_validated=True,
        last_heartbeat=datetime.now(tz=timezone.utc),
        ...
    )
    RunnerStateStore.write(state, path=config.runner_state_path)

Usage (dashboard):
    result = RunnerStateStore.read(path=config.runner_state_path)
    if result.status == "ok":
        # result.state is a RunnerState
    elif result.status == "not_found":
        # runner has never written state
    else:  # "corrupt"
        # file exists but unreadable; show error
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

_VERSION = 1


class RunnerStatus(StrEnum):
    """Lifecycle status of the BIT run loop."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class RunnerState(BaseModel):
    """
    Persisted snapshot of the BIT runner's runtime state.

    Written by the runner on startup, each heartbeat, and on shutdown.
    Read by the dashboard to surface loop health without a live runner
    being in-process.
    """

    version: int = _VERSION

    updated_at: datetime
    """When this state was last written (UTC)."""

    status: RunnerStatus
    """Current lifecycle status of the run loop."""

    startup_validated: bool = False
    """True if startup validation (config, journal writable, etc.) passed."""

    startup_error: str | None = None
    """Error message if startup validation failed; None otherwise."""

    last_heartbeat: datetime | None = None
    """Timestamp of the last successful heartbeat tick (UTC)."""

    last_cycle_start: datetime | None = None
    """Timestamp when the most recent pipeline cycle began."""

    last_cycle_end: datetime | None = None
    """Timestamp when the most recent pipeline cycle completed (or failed)."""

    last_successful_cycle: datetime | None = None
    """Timestamp of the last cycle that completed without error."""

    last_error: str | None = None
    """Most recent error message if any cycle or heartbeat raised; None if clean."""

    processed_symbols: list[str] = Field(default_factory=list)
    """Symbols processed in the most recent cycle."""

    credential_check: str | None = None
    """
    Result of the startup credential check.
    "ok"      — key validated against Bybit.
    "skipped" — no credentials configured; public endpoints only.
    "failed"  — validation attempted but failed (see startup_error).
    None      — runner has not started yet or field was not written.
    """


@dataclass
class RunnerStateReadResult:
    """
    Result of RunnerStateStore.read().

    status:
      "ok"         — state read and parsed successfully.
      "not_found"  — no state file; runner has never written state.
      "corrupt"    — file exists but cannot be parsed.

    file_mtime: wall-clock time of the file on disk; used to detect stale state.
    """

    status: Literal["ok", "not_found", "corrupt"]
    state: RunnerState | None
    error: str | None = None
    file_mtime: datetime | None = None


class RunnerStateStore:
    """Write and read runner state to/from a JSON file. All methods are static."""

    @staticmethod
    def write(state: RunnerState, path: Path) -> None:
        """
        Write state to path atomically.

        Steps: serialize → write to <path>.tmp → rename to path.
        Cleans up .tmp on failure and re-raises.

        Args:
            state: RunnerState to persist.
            path:  Target path. Parent dir is created if absent.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                state.model_dump_json(indent=2), encoding="utf-8"
            )
            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def read(path: Path) -> RunnerStateReadResult:
        """
        Read and parse runner state from path.

        Returns:
            RunnerStateReadResult with status "ok", "not_found", or "corrupt".
            file_mtime is always populated when the file exists (even if corrupt).
        """
        if not path.exists():
            return RunnerStateReadResult(status="not_found", state=None)

        file_mtime: datetime | None = None
        try:
            file_mtime = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            pass

        try:
            text = path.read_text(encoding="utf-8")
            state = RunnerState.model_validate_json(text)
            return RunnerStateReadResult(
                status="ok", state=state, file_mtime=file_mtime
            )
        except Exception as exc:
            return RunnerStateReadResult(
                status="corrupt",
                state=None,
                error=f"Cannot parse runner state from {path}: {exc}",
                file_mtime=file_mtime,
            )
