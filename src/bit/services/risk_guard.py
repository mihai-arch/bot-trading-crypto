"""
Portfolio risk limits for the paper blend.

Rules, evaluated on mark-to-market equity:

1. Drawdown halt. Equity at or below ``starting_equity * (1 - max_drawdown_pct)``
   flattens the book and stays halted until a manual reset. The reference is
   the stage starting bankroll, not a rolling peak. A later recovery does not
   clear the halt.
2. Daily loss. A loss of ``daily_loss_limit_pct`` versus the first equity
   observed on the current UTC day blocks new entries until the next UTC day.
   Reductions are still allowed. The limit clears itself at 00:00 UTC.
3. Consecutive losing round-trips. After ``max_consecutive_losses`` the book
   blocks new entries until a manual reset. A winning round-trip resets the
   streak only while the breaker is not already latched.
4. STOP file. The presence of the kill file tells the runner to exit. It does
   not by itself flatten positions; the file is how an operator stops the process.

The guard is pure: it does not read the clock or the filesystem. The runner
passes those facts in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from ..config import BITConfig

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class GuardView:
    """Facts the guard needs for one evaluation."""

    starting_equity: Decimal
    equity: Decimal
    day_start_equity: Decimal
    utc_day: date
    now: datetime
    consecutive_losses: int
    drawdown_halted: bool
    loss_streak_halted: bool
    stop_file_present: bool
    goal_equity: Decimal


@dataclass(frozen=True)
class GuardVerdict:
    """What the runner should do after one evaluation."""

    drawdown_halted: bool
    loss_streak_halted: bool
    flatten: bool
    block_entries: bool
    kill_process: bool
    utc_day: date
    day_start_equity: Decimal
    reasons: tuple[str, ...]
    goal_progress: Decimal
    goal_remaining: Decimal


@dataclass(frozen=True)
class StreakUpdate:
    consecutive_losses: int
    loss_streak_halted: bool


class RiskGuard:
    """Deterministic risk limits. Safe to construct once and call from tests."""

    def __init__(self, config: BITConfig) -> None:
        if config.max_drawdown_pct <= _ZERO or config.max_drawdown_pct >= _ONE:
            raise ValueError("max_drawdown_pct must be between 0 and 1.")
        if config.daily_loss_limit_pct <= _ZERO or config.daily_loss_limit_pct >= _ONE:
            raise ValueError("daily_loss_limit_pct must be between 0 and 1.")
        if config.max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses must be at least 1.")
        if config.goal_equity_usdt <= _ZERO:
            raise ValueError("goal_equity_usdt must be positive.")
        self._max_drawdown = config.max_drawdown_pct
        self._daily_loss = config.daily_loss_limit_pct
        self._max_losses = config.max_consecutive_losses

    def evaluate(self, view: GuardView) -> GuardVerdict:
        reasons: list[str] = []
        if view.now.tzinfo is None:
            raise ValueError("GuardView.now must be timezone-aware UTC.")
        today = view.now.astimezone(timezone.utc).date()
        if today != view.utc_day:
            day = today
            day_start = view.equity
        else:
            day = view.utc_day
            day_start = view.day_start_equity

        floor = view.starting_equity * (_ONE - self._max_drawdown)
        drawdown_halted = view.drawdown_halted
        flatten = False
        if view.equity <= floor:
            if not drawdown_halted:
                reasons.append(
                    f"drawdown halt: equity {view.equity} <= floor {floor} "
                    f"(-{self._max_drawdown:.0%} from start {view.starting_equity})"
                )
            else:
                reasons.append("drawdown halt still active; manual reset required")
            drawdown_halted = True
            flatten = True
        elif drawdown_halted:
            reasons.append("drawdown halt still active; manual reset required")
            flatten = True

        loss_streak_halted = view.loss_streak_halted or view.consecutive_losses >= self._max_losses
        if loss_streak_halted:
            reasons.append(
                f"consecutive-loss halt: {view.consecutive_losses} losses "
                f"(limit {self._max_losses}); manual reset required"
            )

        block_entries = drawdown_halted or loss_streak_halted
        if day_start > _ZERO and view.equity <= day_start * (_ONE - self._daily_loss):
            block_entries = True
            reasons.append(
                f"daily loss limit: equity {view.equity} <= "
                f"{day_start * (_ONE - self._daily_loss)} "
                f"(-{self._daily_loss:.0%} from day start {day_start}); "
                "no new entries until 00:00 UTC"
            )

        kill = view.stop_file_present
        if kill:
            block_entries = True
            reasons.append("STOP file present; halting the bot")

        if view.goal_equity > _ZERO:
            progress = view.equity / view.goal_equity
            remaining = view.goal_equity - view.equity
        else:
            progress = _ZERO
            remaining = _ZERO

        return GuardVerdict(
            drawdown_halted=drawdown_halted,
            loss_streak_halted=loss_streak_halted,
            flatten=flatten,
            block_entries=block_entries,
            kill_process=kill,
            utc_day=day,
            day_start_equity=day_start,
            reasons=tuple(reasons),
            goal_progress=progress,
            goal_remaining=remaining,
        )

    def register_round_trip(
        self,
        *,
        consecutive_losses: int,
        loss_streak_halted: bool,
        realized_pnl: Decimal,
    ) -> StreakUpdate:
        """
        Update the loss streak after a round-trip that closed a position.

        A loss increments the streak and latches the breaker at the limit.
        A win resets the streak only when the breaker is not already latched.
        A zero result leaves the streak unchanged.
        """
        if realized_pnl < _ZERO:
            streak = consecutive_losses + 1
            halted = loss_streak_halted or streak >= self._max_losses
            return StreakUpdate(consecutive_losses=streak, loss_streak_halted=halted)
        if realized_pnl > _ZERO and not loss_streak_halted:
            return StreakUpdate(consecutive_losses=0, loss_streak_halted=False)
        return StreakUpdate(
            consecutive_losses=consecutive_losses,
            loss_streak_halted=loss_streak_halted,
        )
