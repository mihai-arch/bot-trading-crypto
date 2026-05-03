"""
Smoke tests for dashboard HTTP routes.

Verifies:
- GET / returns 200 with HTML content-type
- GET /api/snapshot returns 200 with JSON containing expected keys
- GET /health returns {"status": "ok"}
- Snapshot JSON contains no fake/missing-data silence (explicit N/A markers)
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bit.config import BITConfig
from bit.dashboard.app import create_app
from bit.services.journal import JournalLearningStore


def _make_client(tmp_path: Path) -> TestClient:
    config = BITConfig(bybit_api_key="", bybit_api_secret="", paper_trading=True)
    journal = JournalLearningStore(config, journal_path=tmp_path / "journal.jsonl")
    app = create_app(config=config, journal=journal, portfolio=None, project_root=tmp_path)
    return TestClient(app)


class TestDashboardRoute:
    def test_get_root_returns_200(self, tmp_path):
        client = _make_client(tmp_path)
        response = client.get("/")
        assert response.status_code == 200

    def test_get_root_content_type_is_html(self, tmp_path):
        client = _make_client(tmp_path)
        response = client.get("/")
        assert "text/html" in response.headers["content-type"]

    def test_html_contains_bit_title(self, tmp_path):
        client = _make_client(tmp_path)
        html = client.get("/").text
        assert "BIT" in html

    def test_html_contains_paper_badge(self, tmp_path):
        client = _make_client(tmp_path)
        html = client.get("/").text
        assert "PAPER" in html

    def test_html_contains_loop_not_running(self, tmp_path):
        client = _make_client(tmp_path)
        html = client.get("/").text
        assert "NOT RUNNING" in html

    def test_html_contains_readiness_section(self, tmp_path):
        client = _make_client(tmp_path)
        html = client.get("/").text
        assert "Readiness" in html

    def test_html_contains_health_section(self, tmp_path):
        client = _make_client(tmp_path)
        html = client.get("/").text
        assert "Health" in html

    def test_html_contains_runtime_gaps(self, tmp_path):
        client = _make_client(tmp_path)
        html = client.get("/").text
        assert "Runtime Gaps" in html

    def test_html_portfolio_not_injected_shows_na(self, tmp_path):
        client = _make_client(tmp_path)
        html = client.get("/").text
        assert "NOT INJECTED" in html or "N/A" in html


class TestSnapshotRoute:
    def test_get_snapshot_returns_200(self, tmp_path):
        client = _make_client(tmp_path)
        response = client.get("/api/snapshot")
        assert response.status_code == 200

    def test_snapshot_is_json(self, tmp_path):
        client = _make_client(tmp_path)
        response = client.get("/api/snapshot")
        data = response.json()
        assert isinstance(data, dict)

    def test_snapshot_has_required_keys(self, tmp_path):
        client = _make_client(tmp_path)
        data = client.get("/api/snapshot").json()
        required = {
            "mode", "symbols", "as_of", "loop_running",
            "journal_entry_count", "portfolio", "risk_config",
            "open_positions", "recent_decisions", "recent_fills",
            "health", "readiness", "runtime_gaps",
        }
        assert required <= set(data.keys())

    def test_snapshot_mode_is_paper(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.get("/api/snapshot").json()["mode"] == "PAPER"

    def test_snapshot_portfolio_is_null_without_tracker(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.get("/api/snapshot").json()["portfolio"] is None

    def test_snapshot_loop_running_is_false(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.get("/api/snapshot").json()["loop_running"] is False

    def test_snapshot_journal_count_zero(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.get("/api/snapshot").json()["journal_entry_count"] == 0

    def test_snapshot_health_has_items(self, tmp_path):
        client = _make_client(tmp_path)
        health = client.get("/api/snapshot").json()["health"]
        assert len(health) > 0

    def test_snapshot_readiness_has_items(self, tmp_path):
        client = _make_client(tmp_path)
        readiness = client.get("/api/snapshot").json()["readiness"]
        assert len(readiness) > 0

    def test_snapshot_runtime_gaps_has_items(self, tmp_path):
        client = _make_client(tmp_path)
        gaps = client.get("/api/snapshot").json()["runtime_gaps"]
        assert len(gaps) > 0


class TestHealthRoute:
    def test_health_endpoint_returns_200(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.get("/health").status_code == 200

    def test_health_endpoint_returns_ok(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.get("/health").json() == {"status": "ok"}
