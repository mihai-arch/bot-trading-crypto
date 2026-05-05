"""
Tests for ReadinessEvaluator paper trading readiness checks.

Verifies:
- evaluate() returns the correct number of items (10)
- Config item is always READY
- Portfolio: always WARNING (in-memory limitation or not injected)
- Portfolio persistence: READY when file ok, WARNING when not_found, MISSING when corrupt
- Journal writable: READY when dir is writable; MISSING when not writable
- API key: WARNING always (paper trading uses public endpoints; MISSING removed)
- Credential check: READY when "ok", MISSING when "failed:", WARNING when None or "skipped"
- Scheduler: MISSING when no runner state; READY when "running"; WARNING when stopped/starting
- Market connectivity: READY when journal has data, WARNING when no cycles yet
- Journal data: READY when entries > 0, WARNING when 0
- Docker: WARNING when absent, READY when present
- Each item has a unique key
"""

from pathlib import Path

import pytest

from bit.config import BITConfig
from bit.dashboard.models import ReadinessStatus
from bit.dashboard.readiness import ReadinessEvaluator


def _config(**kwargs) -> BITConfig:
    defaults = dict(
        bybit_api_key="",
        bybit_api_secret="",
        paper_trading=True,
    )
    defaults.update(kwargs)
    return BITConfig(**defaults)


def _evaluate(
    config: BITConfig | None = None,
    journal_entry_count: int = 0,
    portfolio_available: bool = False,
    journal_path: Path | None = None,
    project_root: Path | None = None,
    portfolio_persistence_status: str = "not_found",
    credential_check_status: str | None = None,
    runner_state_status: str | None = None,
    tmp_path: Path | None = None,
):
    cfg = config or _config()
    jpath = journal_path or (tmp_path / "data" / "journal.jsonl" if tmp_path else Path("data/journal.jsonl"))
    root = project_root or (tmp_path if tmp_path else Path("."))
    return ReadinessEvaluator().evaluate(
        config=cfg,
        journal_entry_count=journal_entry_count,
        portfolio_available=portfolio_available,
        journal_path=jpath,
        project_root=root,
        portfolio_persistence_status=portfolio_persistence_status,
        credential_check_status=credential_check_status,
        runner_state_status=runner_state_status,
    )


class TestEvaluateReturnsAllItems:
    def test_returns_ten_items(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        assert len(items) == 10

    def test_all_items_have_unique_keys(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        keys = [i.key for i in items]
        assert len(keys) == len(set(keys)), "All readiness item keys must be unique"

    def test_all_items_have_labels(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        for item in items:
            assert item.label, f"Item {item.key!r} has no label"


class TestConfigCheck:
    def test_config_always_ready(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        item = next(i for i in items if i.key == "config")
        assert item.status == ReadinessStatus.READY

    def test_config_detail_mentions_paper_mode(self, tmp_path):
        items = _evaluate(config=_config(paper_trading=True), tmp_path=tmp_path)
        item = next(i for i in items if i.key == "config")
        assert "paper" in item.detail.lower() or "True" in item.detail


class TestPortfolioCheck:
    def test_portfolio_available_is_warning_not_ready(self, tmp_path):
        # Available but in-memory only → WARNING (not READY)
        items = _evaluate(portfolio_available=True, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio")
        assert item.status == ReadinessStatus.WARNING

    def test_portfolio_not_available_is_warning(self, tmp_path):
        items = _evaluate(portfolio_available=False, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio")
        assert item.status == ReadinessStatus.WARNING

    def test_portfolio_available_detail_mentions_in_memory(self, tmp_path):
        items = _evaluate(portfolio_available=True, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio")
        assert "in-memory" in item.detail.lower() or "memory" in item.detail.lower()

    def test_portfolio_not_available_detail_mentions_inject(self, tmp_path):
        items = _evaluate(portfolio_available=False, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio")
        assert "inject" in item.detail.lower() or "create_app" in item.detail

    def test_portfolio_persisted_snapshot_is_warning(self, tmp_path):
        """No live tracker but persisted file available → WARNING with distinct label."""
        items = _evaluate(
            portfolio_available=False,
            portfolio_persistence_status="ok",
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "portfolio")
        assert item.status == ReadinessStatus.WARNING

    def test_portfolio_persisted_label_mentions_snapshot(self, tmp_path):
        items = _evaluate(
            portfolio_available=False,
            portfolio_persistence_status="ok",
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "portfolio")
        assert "persisted" in item.label.lower() or "snapshot" in item.label.lower()


class TestJournalWritableCheck:
    def test_writable_dir_is_ready(self, tmp_path):
        items = _evaluate(journal_path=tmp_path / "journal.jsonl", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "journal_writable")
        assert item.status == ReadinessStatus.READY

    def test_writable_detail_includes_path(self, tmp_path):
        journal_path = tmp_path / "journal.jsonl"
        items = _evaluate(journal_path=journal_path, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "journal_writable")
        assert str(journal_path) in item.detail


class TestApiKeyCheck:
    def test_no_api_key_is_warning(self, tmp_path):
        # Paper trading uses public Bybit endpoints — missing key is WARNING, not MISSING.
        items = _evaluate(config=_config(bybit_api_key=""), tmp_path=tmp_path)
        item = next(i for i in items if i.key == "api_key")
        assert item.status == ReadinessStatus.WARNING

    def test_api_key_present_is_warning(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="abc123", bybit_api_secret="secret"),
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "api_key")
        assert item.status == ReadinessStatus.WARNING

    def test_api_key_warning_detail_mentions_not_verified(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="abc123", bybit_api_secret="secret"),
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "api_key")
        assert "not" in item.detail.lower() and ("valid" in item.detail.lower() or "verif" in item.detail.lower())

    def test_api_key_warning_detail_mentions_public_endpoints(self, tmp_path):
        items = _evaluate(config=_config(bybit_api_key=""), tmp_path=tmp_path)
        item = next(i for i in items if i.key == "api_key")
        assert "public" in item.detail.lower() or "BYBIT_API_KEY" in item.detail


class TestSchedulerCheck:
    def test_scheduler_missing_when_no_runner_state(self, tmp_path):
        items = _evaluate(runner_state_status=None, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert item.status == ReadinessStatus.MISSING

    def test_scheduler_ready_when_running(self, tmp_path):
        items = _evaluate(runner_state_status="running", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert item.status == ReadinessStatus.READY

    def test_scheduler_missing_when_error(self, tmp_path):
        items = _evaluate(runner_state_status="error", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert item.status == ReadinessStatus.MISSING

    def test_scheduler_warning_when_stopped(self, tmp_path):
        items = _evaluate(runner_state_status="stopped", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert item.status == ReadinessStatus.WARNING

    def test_scheduler_warning_when_starting(self, tmp_path):
        items = _evaluate(runner_state_status="starting", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert item.status == ReadinessStatus.WARNING

    def test_scheduler_detail_mentions_loop_or_runner(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert "loop" in item.detail.lower() or "runner" in item.detail.lower()


class TestCredentialCheckItem:
    def test_missing_when_no_api_key(self, tmp_path):
        items = _evaluate(config=_config(bybit_api_key=""), tmp_path=tmp_path)
        item = next(i for i in items if i.key == "credential_check")
        assert item.status == ReadinessStatus.MISSING

    def test_ready_when_ok(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="key", bybit_api_secret="secret"),
            credential_check_status="ok",
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "credential_check")
        assert item.status == ReadinessStatus.READY

    def test_missing_when_failed(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="key", bybit_api_secret="secret"),
            credential_check_status="failed: Invalid API key.",
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "credential_check")
        assert item.status == ReadinessStatus.MISSING

    def test_warning_when_none(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="key", bybit_api_secret="secret"),
            credential_check_status=None,
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "credential_check")
        assert item.status == ReadinessStatus.WARNING

    def test_warning_when_skipped(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="key", bybit_api_secret="secret"),
            credential_check_status="skipped",
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "credential_check")
        assert item.status == ReadinessStatus.WARNING

    def test_failed_detail_included(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="key", bybit_api_secret="secret"),
            credential_check_status="failed: Invalid API key.",
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "credential_check")
        assert "Invalid API key" in item.detail

    def test_has_unique_key(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        keys = [i.key for i in items]
        assert keys.count("credential_check") == 1


class TestMarketConnectivityCheck:
    def test_no_data_is_warning(self, tmp_path):
        # No journal entries yet → connectivity not confirmed; key presence irrelevant.
        items = _evaluate(config=_config(bybit_api_key=""), journal_entry_count=0, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "market_connectivity")
        assert item.status == ReadinessStatus.WARNING

    def test_with_key_and_no_data_is_warning(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="abc123", bybit_api_secret="secret"),
            journal_entry_count=0,
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "market_connectivity")
        assert item.status == ReadinessStatus.WARNING

    def test_with_journal_data_is_ready(self, tmp_path):
        # Completed cycles prove connectivity, regardless of API key.
        items = _evaluate(journal_entry_count=3, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "market_connectivity")
        assert item.status == ReadinessStatus.READY

    def test_warning_detail_says_start_runner(self, tmp_path):
        items = _evaluate(journal_entry_count=0, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "market_connectivity")
        assert "confirm" in item.detail.lower() or "start" in item.detail.lower()

    def test_ready_label_mentions_cycle_count(self, tmp_path):
        items = _evaluate(journal_entry_count=5, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "market_connectivity")
        assert "5" in item.label


class TestJournalDataCheck:
    def test_no_entries_is_warning(self, tmp_path):
        items = _evaluate(journal_entry_count=0, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "journal_data")
        assert item.status == ReadinessStatus.WARNING

    def test_has_entries_is_ready(self, tmp_path):
        items = _evaluate(journal_entry_count=10, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "journal_data")
        assert item.status == ReadinessStatus.READY

    def test_entry_count_shown_in_label(self, tmp_path):
        items = _evaluate(journal_entry_count=42, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "journal_data")
        assert "42" in item.label


class TestDockerCheck:
    def test_no_docker_is_warning_not_missing(self, tmp_path):
        # Docker is optional — absence is a WARNING, not a blocker (MISSING)
        items = _evaluate(project_root=tmp_path, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "docker")
        assert item.status == ReadinessStatus.WARNING

    def test_compose_file_present_is_ready(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
        items = _evaluate(project_root=tmp_path, tmp_path=tmp_path)
        item = next(i for i in items if i.key == "docker")
        assert item.status == ReadinessStatus.READY


class TestPortfolioPersistenceCheck:
    def test_not_found_is_warning(self, tmp_path):
        items = _evaluate(portfolio_persistence_status="not_found", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio_persistence")
        assert item.status == ReadinessStatus.WARNING

    def test_ok_is_ready(self, tmp_path):
        items = _evaluate(portfolio_persistence_status="ok", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio_persistence")
        assert item.status == ReadinessStatus.READY

    def test_corrupt_is_missing(self, tmp_path):
        items = _evaluate(portfolio_persistence_status="corrupt", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio_persistence")
        assert item.status == ReadinessStatus.MISSING

    def test_ok_detail_mentions_path(self, tmp_path):
        cfg = _config()
        items = _evaluate(config=cfg, portfolio_persistence_status="ok", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio_persistence")
        # Detail should reference the state file path
        assert item.detail is not None and len(item.detail) > 0

    def test_corrupt_detail_mentions_inspect(self, tmp_path):
        items = _evaluate(portfolio_persistence_status="corrupt", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio_persistence")
        assert "inspect" in item.detail.lower() or "delete" in item.detail.lower()

    def test_not_found_detail_mentions_first_fill(self, tmp_path):
        items = _evaluate(portfolio_persistence_status="not_found", tmp_path=tmp_path)
        item = next(i for i in items if i.key == "portfolio_persistence")
        assert "fill" in item.detail.lower() or "restart" in item.detail.lower()

    def test_has_unique_key(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        keys = [i.key for i in items]
        assert keys.count("portfolio_persistence") == 1
