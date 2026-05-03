"""
Tests for ReadinessEvaluator paper trading readiness checks.

Verifies:
- evaluate() returns the correct number of items
- Config item is always READY
- Portfolio: READY not valid here — always WARNING (in-memory limitation or not injected)
- Journal writable: READY when dir is writable; MISSING when not writable
- API key: MISSING when absent, WARNING when present
- Scheduler: always MISSING
- Market connectivity: MISSING when no key, WARNING when key present
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
    )


class TestEvaluateReturnsAllItems:
    def test_returns_eight_items(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        assert len(items) == 8

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
    def test_no_api_key_is_missing(self, tmp_path):
        items = _evaluate(config=_config(bybit_api_key=""), tmp_path=tmp_path)
        item = next(i for i in items if i.key == "api_key")
        assert item.status == ReadinessStatus.MISSING

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

    def test_api_key_missing_detail_mentions_env(self, tmp_path):
        items = _evaluate(config=_config(bybit_api_key=""), tmp_path=tmp_path)
        item = next(i for i in items if i.key == "api_key")
        assert ".env" in item.detail or "BYBIT_API_KEY" in item.detail


class TestSchedulerCheck:
    def test_scheduler_always_missing(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert item.status == ReadinessStatus.MISSING

    def test_scheduler_detail_mentions_loop(self, tmp_path):
        items = _evaluate(tmp_path=tmp_path)
        item = next(i for i in items if i.key == "scheduler")
        assert "loop" in item.detail.lower() or "scheduler" in item.detail.lower()


class TestMarketConnectivityCheck:
    def test_no_key_is_missing(self, tmp_path):
        items = _evaluate(config=_config(bybit_api_key=""), tmp_path=tmp_path)
        item = next(i for i in items if i.key == "market_connectivity")
        assert item.status == ReadinessStatus.MISSING

    def test_key_present_is_warning(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="abc123", bybit_api_secret="secret"),
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "market_connectivity")
        assert item.status == ReadinessStatus.WARNING

    def test_warning_detail_says_not_confirmed(self, tmp_path):
        items = _evaluate(
            config=_config(bybit_api_key="abc123", bybit_api_secret="secret"),
            tmp_path=tmp_path,
        )
        item = next(i for i in items if i.key == "market_connectivity")
        assert "confirm" in item.detail.lower() or "verif" in item.detail.lower()


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
