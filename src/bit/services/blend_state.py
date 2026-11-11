"""
Persisted state for the paper blend.

A restart reads this file and continues with the same sleeve cash, positions,
drawdown halt, and loss streak. The file is written atomically.

The book is split once, at the first start:

- rotation sleeve: ``rotation_sleeve_pct`` of the starting bankroll
- conservative sleeve: the rest, divided equally across BTC, ETH and SOL

Those sub-accounts are not rebalanced against each other afterwards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from ..config import BITConfig
from ..domain.enums import Symbol
from .paper_portfolio import PaperPortfolioTracker, _PaperPosition

_VERSION = 1
_CASH_QUANTUM = Decimal("0.00000001")


@dataclass
class BlendState:
    starting_equity: Decimal
    goal_equity: Decimal
    rotation: PaperPortfolioTracker
    conservative: dict[Symbol, PaperPortfolioTracker]
    drawdown_halted: bool = False
    loss_streak_halted: bool = False
    consecutive_losses: int = 0
    utc_day: date = field(default_factory=lambda: datetime.now(tz=timezone.utc).date())
    day_start_equity: Decimal = Decimal("0")
    last_rotation_open: datetime | None = None
    last_conservative_open: dict[Symbol, datetime] = field(default_factory=dict)
    last_equity: Decimal = Decimal("0")
    goal_progress: Decimal = Decimal("0")
    goal_remaining: Decimal = Decimal("0")
    status: str = "new"


def _tracker_to_dict(tracker: PaperPortfolioTracker) -> dict:
    return {
        "cash": str(tracker.cash),
        "realized_pnl": str(tracker.realized_pnl),
        "positions": {
            str(symbol): {"qty": str(pos.qty), "avg_entry_price": str(pos.avg_entry_price)}
            for symbol, pos in tracker._positions.items()
        },
    }


def _tracker_from_dict(data: dict) -> PaperPortfolioTracker:
    tracker = object.__new__(PaperPortfolioTracker)
    tracker._cash = Decimal(data["cash"])
    tracker._realized_pnl = Decimal(data["realized_pnl"])
    tracker._positions = {
        Symbol(sym): _PaperPosition(
            qty=Decimal(pos["qty"]),
            avg_entry_price=Decimal(pos["avg_entry_price"]),
        )
        for sym, pos in data.get("positions", {}).items()
    }
    return tracker


def fresh_state(config: BITConfig, *, now: datetime | None = None) -> BlendState:
    """Split the starting bankroll into sleeves. Cash sums back to the bankroll."""
    moment = now or datetime.now(tz=timezone.utc)
    total = config.capital_usdt
    if total <= 0:
        raise ValueError("capital_usdt must be positive.")
    symbols = list(config.symbols)
    if not symbols:
        raise ValueError("At least one symbol is required.")
    conservative_pool = total * (Decimal("1") - config.rotation_sleeve_pct)
    each = (conservative_pool / Decimal(len(symbols))).quantize(_CASH_QUANTUM, rounding=ROUND_DOWN)
    conservative_cash = each * len(symbols)
    rotation_cash = total - conservative_cash
    state = BlendState(
        starting_equity=total,
        goal_equity=config.goal_equity_usdt,
        rotation=PaperPortfolioTracker(rotation_cash),
        conservative={symbol: PaperPortfolioTracker(each) for symbol in symbols},
        utc_day=moment.astimezone(timezone.utc).date(),
        day_start_equity=total,
        last_equity=total,
        goal_remaining=config.goal_equity_usdt - total,
        goal_progress=(
            (total / config.goal_equity_usdt) if config.goal_equity_usdt else Decimal("0")
        ),
        status="running",
    )
    return state


def equity_of(state: BlendState, marks: dict[Symbol, Decimal]) -> Decimal:
    total = state.rotation.snapshot(marks).total_equity_usdt
    for tracker in state.conservative.values():
        total += tracker.snapshot(marks).total_equity_usdt
    return total


class BlendStateStore:
    """Atomic JSON persistence for BlendState."""

    @staticmethod
    def save(state: BlendState, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _VERSION,
            "saved_at": datetime.now(tz=timezone.utc).isoformat(),
            "starting_equity": str(state.starting_equity),
            "goal_equity": str(state.goal_equity),
            "last_equity": str(state.last_equity),
            "goal_progress": str(state.goal_progress),
            "goal_remaining": str(state.goal_remaining),
            "status": state.status,
            "drawdown_halted": state.drawdown_halted,
            "loss_streak_halted": state.loss_streak_halted,
            "consecutive_losses": state.consecutive_losses,
            "utc_day": state.utc_day.isoformat(),
            "day_start_equity": str(state.day_start_equity),
            "last_rotation_open": (
                state.last_rotation_open.isoformat() if state.last_rotation_open else None
            ),
            "last_conservative_open": {
                str(symbol): ts.isoformat() for symbol, ts in state.last_conservative_open.items()
            },
            "rotation": _tracker_to_dict(state.rotation),
            "conservative": {
                str(symbol): _tracker_to_dict(tracker)
                for symbol, tracker in state.conservative.items()
            },
        }
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def load(path: Path) -> BlendState:
        """
        Load state. Raises FileNotFoundError when the file is absent and
        ValueError when it is present but unusable.
        """
        if not path.exists():
            raise FileNotFoundError(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Cannot parse blend state {path}: {exc}") from exc
        if data.get("version") != _VERSION:
            raise ValueError(
                f"Unknown blend state version {data.get('version')!r} (expected {_VERSION})."
            )
        try:
            last_rot = data.get("last_rotation_open")
            conservative_open = {
                Symbol(sym): datetime.fromisoformat(ts)
                for sym, ts in data.get("last_conservative_open", {}).items()
            }
            return BlendState(
                starting_equity=Decimal(data["starting_equity"]),
                goal_equity=Decimal(data["goal_equity"]),
                rotation=_tracker_from_dict(data["rotation"]),
                conservative={
                    Symbol(sym): _tracker_from_dict(book)
                    for sym, book in data.get("conservative", {}).items()
                },
                drawdown_halted=bool(data.get("drawdown_halted", False)),
                loss_streak_halted=bool(data.get("loss_streak_halted", False)),
                consecutive_losses=int(data.get("consecutive_losses", 0)),
                utc_day=date.fromisoformat(data["utc_day"]),
                day_start_equity=Decimal(data["day_start_equity"]),
                last_rotation_open=datetime.fromisoformat(last_rot) if last_rot else None,
                last_conservative_open=conservative_open,
                last_equity=Decimal(data.get("last_equity", data["starting_equity"])),
                goal_progress=Decimal(data.get("goal_progress", "0")),
                goal_remaining=Decimal(data.get("goal_remaining", "0")),
                status=str(data.get("status", "restored")),
            )
        except Exception as exc:
            raise ValueError(f"Cannot restore blend state {path}: {exc}") from exc
