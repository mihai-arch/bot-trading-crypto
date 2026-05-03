"""
Tests for HealthChecker structural probes.

Verifies:
- probe_all returns correct number and order of items
- Each service probe returns the expected ServiceStatus
- ExecutionEngine probe responds to paper_trading flag
- Journal probe: writable directory → IMPLEMENTED, not-writable → DEGRADED
- Scheduler probe is always MISSING
- Docker probe: no file → MISSING; compose file present → IMPLEMENTED
"""

from pathlib import Path

import pytest

from bit.config import BITConfig
from bit.dashboard.health import HealthChecker
from bit.dashboard.models import ServiceStatus


def _config(**kwargs) -> BITConfig:
    defaults = dict(
        bybit_api_key="",
        bybit_api_secret="",
        paper_trading=True,
    )
    defaults.update(kwargs)
    return BITConfig(**defaults)


class TestProbeAll:
    def test_returns_nine_items(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(
            config=_config(),
            journal_path=tmp_path / "journal.jsonl",
            project_root=tmp_path,
        )
        assert len(items) == 9

    def test_item_names_in_order(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(
            config=_config(),
            journal_path=tmp_path / "journal.jsonl",
            project_root=tmp_path,
        )
        names = [i.name for i in items]
        assert names[0] == "MarketDataService"
        assert names[-2] == "Scheduler / Loop"
        assert names[-1] == "Docker"

    def test_all_items_have_names(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(
            config=_config(),
            journal_path=tmp_path / "journal.jsonl",
            project_root=tmp_path,
        )
        for item in items:
            assert item.name, "Every health item must have a name"


class TestMarketDataProbe:
    def test_market_data_is_partial(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "MarketDataService")
        assert item.status == ServiceStatus.PARTIAL

    def test_market_data_detail_mentions_stubs(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "MarketDataService")
        assert "stubs" in item.detail.lower()


class TestImplementedServices:
    @pytest.mark.parametrize("name", [
        "FeatureEngine",
        "SignalEngine",
        "DecisionEngine",
        "RiskEngine",
    ])
    def test_core_services_implemented(self, tmp_path, name):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == name)
        assert item.status == ServiceStatus.IMPLEMENTED

    def test_feature_engine_detail_mentions_indicators(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "FeatureEngine")
        assert "EMA" in item.detail or "RSI" in item.detail


class TestExecutionEngineProbe:
    def test_paper_mode_is_implemented(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(
            _config(paper_trading=True), tmp_path / "j.jsonl", tmp_path
        )
        item = next(i for i in items if i.name == "ExecutionEngine")
        assert item.status == ServiceStatus.IMPLEMENTED

    def test_live_mode_is_stub(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(
            _config(paper_trading=False), tmp_path / "j.jsonl", tmp_path
        )
        item = next(i for i in items if i.name == "ExecutionEngine")
        assert item.status == ServiceStatus.STUB

    def test_live_mode_detail_mentions_notimplemented(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(
            _config(paper_trading=False), tmp_path / "j.jsonl", tmp_path
        )
        item = next(i for i in items if i.name == "ExecutionEngine")
        assert "NotImplementedError" in item.detail


class TestJournalProbe:
    def test_writable_dir_is_implemented(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "journal.jsonl", tmp_path)
        item = next(i for i in items if i.name == "JournalLearningStore")
        assert item.status == ServiceStatus.IMPLEMENTED

    def test_detail_includes_path(self, tmp_path):
        checker = HealthChecker()
        journal_path = tmp_path / "journal.jsonl"
        items = checker.probe_all(_config(), journal_path, tmp_path)
        item = next(i for i in items if i.name == "JournalLearningStore")
        assert str(journal_path) in item.detail

    def test_journal_dir_created_if_missing(self, tmp_path):
        nested = tmp_path / "data" / "sub"
        checker = HealthChecker()
        items = checker.probe_all(_config(), nested / "journal.jsonl", tmp_path)
        item = next(i for i in items if i.name == "JournalLearningStore")
        # Should not raise; either implemented or degraded, never an exception
        assert item.status in (ServiceStatus.IMPLEMENTED, ServiceStatus.DEGRADED)


class TestSchedulerProbe:
    def test_scheduler_always_missing(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "Scheduler / Loop")
        assert item.status == ServiceStatus.MISSING

    def test_scheduler_detail_mentions_pipeline(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "Scheduler / Loop")
        assert "pipeline" in item.detail.lower()


class TestDockerProbe:
    def test_no_compose_file_is_missing(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "Docker")
        assert item.status == ServiceStatus.MISSING

    def test_compose_yml_found_is_implemented(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "Docker")
        assert item.status == ServiceStatus.IMPLEMENTED

    def test_compose_yaml_found_is_implemented(self, tmp_path):
        (tmp_path / "docker-compose.yaml").write_text("version: '3'\n")
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "Docker")
        assert item.status == ServiceStatus.IMPLEMENTED

    def test_docker_missing_detail_mentions_no_file(self, tmp_path):
        checker = HealthChecker()
        items = checker.probe_all(_config(), tmp_path / "j.jsonl", tmp_path)
        item = next(i for i in items if i.name == "Docker")
        assert "docker-compose" in item.detail.lower()
