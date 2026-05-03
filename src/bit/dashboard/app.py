"""
BIT Dashboard — FastAPI application.

Run with:
    python -m bit.dashboard                       (default host 127.0.0.1:8765)
    uvicorn bit.dashboard.app:app --port 8765     (module-level default app)

For integrated use (shared PaperPortfolioTracker with the pipeline):
    from bit.dashboard.app import create_app
    app = create_app(config=config, journal=journal, portfolio=portfolio)

The module-level `app` is created with default BITConfig (reads .env).
It has no access to the pipeline's in-memory portfolio tracker — the portfolio
section will show N/A unless create_app() is used with an injected tracker.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import BITConfig
from ..services.journal import JournalLearningStore
from ..services.paper_portfolio import PaperPortfolioTracker
from .service import DashboardService

if TYPE_CHECKING:
    from ..runner import RunnerState

_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ── Jinja2 display filters ─────────────────────────────────────────────────

def _fmt_decimal(value, places: int = 2) -> str:
    """Format a Decimal or float to a fixed number of decimal places."""
    if value is None:
        return "N/A"
    return f"{float(value):.{places}f}"


def _fmt_datetime(value) -> str:
    """Format a datetime to 'YYYY-MM-DD HH:MM:SS UTC'. Returns 'N/A' for None."""
    if value is None:
        return "N/A"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_score(value) -> str:
    """Format a composite score (0–1 range) to 3 decimal places."""
    if value is None:
        return "N/A"
    return f"{float(value):.3f}"


def _fmt_pct(value, places: int = 1) -> str:
    """Format a decimal fraction as a percentage. 0.20 → '20.0%'."""
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.{places}f}%"


def _register_filters(templates: Jinja2Templates) -> None:
    templates.env.filters["fmt_decimal"] = _fmt_decimal
    templates.env.filters["fmt_datetime"] = _fmt_datetime
    templates.env.filters["fmt_score"] = _fmt_score
    templates.env.filters["fmt_pct"] = _fmt_pct


# ── App factory ────────────────────────────────────────────────────────────

def create_app(
    config: BITConfig,
    journal: JournalLearningStore,
    portfolio: PaperPortfolioTracker | None = None,
    project_root: Path | None = None,
    runner_state: RunnerState | None = None,
) -> FastAPI:
    """
    Create and configure the dashboard FastAPI app.

    Args:
        config:        BITConfig instance. Share with the pipeline when possible.
        journal:       JournalLearningStore instance. Share with the pipeline.
        portfolio:     Optional PaperPortfolioTracker. Must be the SAME instance
                       used by the pipeline to see live in-memory state.
                       Pass None if running the dashboard standalone.
        project_root:  Directory to search for docker-compose files.
                       Defaults to Path(".").
        runner_state:  Optional RunnerState from BotRunner. Must be the SAME
                       instance used by the runner for live state visibility.
                       When provided, loop_running and scheduler items reflect
                       actual runner status instead of hardcoded MISSING.
    """
    service = DashboardService(
        config=config,
        journal=journal,
        portfolio=portfolio,
        project_root=project_root,
        runner_state=runner_state,
    )

    app = FastAPI(
        title="BIT Dashboard",
        description="Operator dashboard for the BIT crypto trading bot.",
        docs_url=None,
        redoc_url=None,
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    _register_filters(templates)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request) -> HTMLResponse:
        snapshot = service.build_snapshot()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"s": snapshot},
        )

    @app.get("/api/snapshot", summary="Dashboard snapshot as JSON")
    async def api_snapshot() -> dict:
        """
        Returns the full DashboardSnapshot as JSON.
        Same data as the HTML dashboard. Use for scripting or external polling.
        """
        return service.build_snapshot().model_dump(mode="json")

    @app.get("/health", include_in_schema=False)
    async def liveness() -> dict:
        """Liveness probe — confirms the dashboard server is reachable."""
        return {"status": "ok"}

    return app


# ── Default module-level app ───────────────────────────────────────────────
# Used by: uvicorn bit.dashboard.app:app
# No portfolio tracker injected — portfolio section shows N/A.

def _make_default_app() -> FastAPI:
    config = BITConfig()
    journal = JournalLearningStore(config)
    return create_app(config=config, journal=journal, portfolio=None)


app = _make_default_app()
