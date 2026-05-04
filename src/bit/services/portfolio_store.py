"""
PortfolioStateStore

File-based persistence for PaperPortfolioTracker state.

Saves and restores:
  - Available cash
  - Realized PnL
  - Open positions (symbol, qty, avg_entry_price)
  - Last observed mark prices (for unrealized PnL display after restart)

File format: JSON, human-readable, version-tagged.
Write strategy: atomic (write to .tmp then rename) to prevent partial writes.

Usage:
    # Save after each fill:
    PortfolioStateStore.save(tracker, path=config.portfolio_state_path)

    # Restore at startup:
    result = PortfolioStateStore.load(path, starting_cash=config.capital_usdt)
    if result.status == "ok":
        tracker = result.tracker
    elif result.status == "corrupt":
        logger.error("Portfolio state corrupt: %s", result.error)
        # Decide: start fresh or halt — never silently continue
    else:  # not_found
        tracker = PaperPortfolioTracker(starting_cash=config.capital_usdt)

Do NOT call load() and silently fall back on error — the caller must decide.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from ..domain.enums import Symbol
from .paper_portfolio import PaperPortfolioTracker

_VERSION = 1


@dataclass
class PortfolioLoadResult:
    """
    Result of PortfolioStateStore.load().

    status:
      "ok"         — state loaded, tracker is valid and ready.
      "not_found"  — no state file exists; start fresh.
      "corrupt"    — file exists but cannot be parsed; do NOT silently continue.
    """

    status: Literal["ok", "not_found", "corrupt"]
    tracker: PaperPortfolioTracker | None
    error: str | None = None
    saved_at: datetime | None = None
    mark_prices: dict[Symbol, Decimal] = field(default_factory=dict)


class PortfolioStateStore:
    """
    Persist and restore PaperPortfolioTracker state to/from a JSON file.

    All methods are static — no instance state. Instantiate if you prefer
    to bind a path at construction time, but static use is idiomatic here.
    """

    @staticmethod
    def save(
        tracker: PaperPortfolioTracker,
        path: Path,
        mark_prices: dict[Symbol, Decimal] | None = None,
    ) -> None:
        """
        Write current tracker state to path atomically.

        Steps: build state dict → write to <path>.tmp → rename to path.
        If rename fails the .tmp is cleaned up and the exception re-raised.

        Args:
            tracker:     The tracker to persist.
            path:        Target JSON file path. Parent dir is created if absent.
            mark_prices: Latest known mark prices per symbol (optional).
                         Saved alongside state so the dashboard can display
                         unrealized PnL after restart.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "version": _VERSION,
            "saved_at": datetime.now(tz=timezone.utc).isoformat(),
            "cash": str(tracker.cash),
            "realized_pnl": str(tracker.realized_pnl),
            "positions": {
                str(symbol): {
                    "qty": str(pos.qty),
                    "avg_entry_price": str(pos.avg_entry_price),
                }
                for symbol, pos in tracker._positions.items()
            },
            "mark_prices": {
                str(symbol): str(price)
                for symbol, price in (mark_prices or {}).items()
            },
        }

        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def load(path: Path, starting_cash: Decimal) -> PortfolioLoadResult:
        """
        Load tracker state from path.

        On success: returns status="ok" with a fully restored PaperPortfolioTracker.
        On missing: returns status="not_found" with tracker=None.
        On corrupt: returns status="corrupt" with tracker=None and a descriptive error.
                    The caller MUST decide whether to start fresh or halt.

        Args:
            path:          Path to the JSON state file.
            starting_cash: Used only as a fallback if the file is not found;
                           the caller controls what to do in that case.
        """
        if not path.exists():
            return PortfolioLoadResult(status="not_found", tracker=None)

        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except Exception as exc:
            return PortfolioLoadResult(
                status="corrupt",
                tracker=None,
                error=f"Cannot parse {path}: {exc}",
            )

        try:
            version = data.get("version")
            if version != _VERSION:
                return PortfolioLoadResult(
                    status="corrupt",
                    tracker=None,
                    error=(
                        f"Unknown state file version {version!r} "
                        f"(expected {_VERSION}). Cannot safely restore."
                    ),
                )

            saved_at = datetime.fromisoformat(data["saved_at"])
            cash = Decimal(data["cash"])
            realized_pnl = Decimal(data["realized_pnl"])

            # Restore positions without going through __init__ validation.
            from .paper_portfolio import _PaperPosition  # same package

            inner_positions: dict[Symbol, _PaperPosition] = {}
            for sym_str, pos_data in data.get("positions", {}).items():
                symbol = Symbol(sym_str)
                inner_positions[symbol] = _PaperPosition(
                    qty=Decimal(pos_data["qty"]),
                    avg_entry_price=Decimal(pos_data["avg_entry_price"]),
                )

            # Build tracker, bypassing __init__ to restore exact saved state.
            tracker = object.__new__(PaperPortfolioTracker)
            tracker._cash = cash
            tracker._realized_pnl = realized_pnl
            tracker._positions = inner_positions

            mark_prices: dict[Symbol, Decimal] = {
                Symbol(sym_str): Decimal(price_str)
                for sym_str, price_str in data.get("mark_prices", {}).items()
            }

            return PortfolioLoadResult(
                status="ok",
                tracker=tracker,
                saved_at=saved_at,
                mark_prices=mark_prices,
            )

        except Exception as exc:
            return PortfolioLoadResult(
                status="corrupt",
                tracker=None,
                error=f"Cannot restore state from {path}: {exc}",
            )

    @staticmethod
    def status(path: Path) -> Literal["ok", "not_found", "corrupt"]:
        """
        Quick structural check of a state file — does not load the tracker.

        Returns "ok", "not_found", or "corrupt".
        Used by the dashboard to surface persistence health without loading.
        """
        if not path.exists():
            return "not_found"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != _VERSION:
                return "corrupt"
            for key in ("cash", "realized_pnl", "saved_at"):
                if key not in data:
                    return "corrupt"
            return "ok"
        except Exception:
            return "corrupt"
