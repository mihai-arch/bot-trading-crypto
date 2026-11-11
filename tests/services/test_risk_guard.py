"""Risk limits: -25% from the starting bankroll, 3% daily loss, loss streak, STOP file."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bit.config import BITConfig
from bit.services.risk_guard import GuardView, RiskGuard

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
_START = Decimal("1000")
_GOAL = Decimal("2000")


def _guard() -> RiskGuard:
    return RiskGuard(
        BITConfig(
            bybit_api_key="",
            bybit_api_secret="",
            paper_trading=True,
            max_drawdown_pct=Decimal("0.25"),
            daily_loss_limit_pct=Decimal("0.03"),
            max_consecutive_losses=6,
            goal_equity_usdt=_GOAL,
        )
    )


def _view(**overrides) -> GuardView:
    base = dict(
        starting_equity=_START,
        equity=_START,
        day_start_equity=_START,
        utc_day=_NOW.date(),
        now=_NOW,
        consecutive_losses=0,
        drawdown_halted=False,
        loss_streak_halted=False,
        stop_file_present=False,
        goal_equity=_GOAL,
    )
    base.update(overrides)
    return GuardView(**base)


class TestDrawdownHalt:
    def test_floor_is_25_percent_below_start(self):
        verdict = _guard().evaluate(_view(equity=Decimal("750")))
        assert verdict.drawdown_halted
        assert verdict.flatten
        assert verdict.block_entries
        assert any("drawdown halt" in reason for reason in verdict.reasons)

    def test_just_above_the_floor_does_not_halt(self):
        verdict = _guard().evaluate(_view(equity=Decimal("750.01")))
        assert not verdict.drawdown_halted
        assert not verdict.flatten

    def test_halt_stays_on_after_equity_recovers(self):
        verdict = _guard().evaluate(_view(equity=Decimal("990"), drawdown_halted=True))
        assert verdict.drawdown_halted
        assert verdict.flatten
        assert verdict.block_entries
        assert any("manual reset" in reason for reason in verdict.reasons)

    def test_goal_progress_uses_current_equity(self):
        verdict = _guard().evaluate(_view(equity=Decimal("1500")))
        assert verdict.goal_progress == Decimal("1500") / _GOAL
        assert verdict.goal_remaining == Decimal("500")


class TestDailyLossLimit:
    def test_three_percent_loss_blocks_entries_only(self):
        # 1000 * 0.97 = 970. Exactly -3% blocks. It is not a permanent halt.
        verdict = _guard().evaluate(_view(equity=Decimal("970")))
        assert verdict.block_entries
        assert not verdict.drawdown_halted
        assert not verdict.flatten
        assert any("daily loss" in reason for reason in verdict.reasons)

    def test_loss_inside_the_daily_band_is_allowed(self):
        verdict = _guard().evaluate(_view(equity=Decimal("970.01")))
        assert not verdict.block_entries

    def test_new_utc_day_resets_the_daily_limit(self):
        next_day = _NOW + timedelta(days=1)
        verdict = _guard().evaluate(
            _view(equity=Decimal("900"), day_start_equity=_START, now=next_day, utc_day=_NOW.date())
        )
        assert verdict.utc_day == date(2026, 11, 12)
        assert verdict.day_start_equity == Decimal("900")
        assert not any("daily loss" in reason for reason in verdict.reasons)
        assert not verdict.block_entries


class TestConsecutiveLosses:
    def test_limit_blocks_and_latches(self):
        verdict = _guard().evaluate(_view(consecutive_losses=6))
        assert verdict.loss_streak_halted
        assert verdict.block_entries
        assert not verdict.flatten

    def test_five_losses_do_not_latch(self):
        verdict = _guard().evaluate(_view(consecutive_losses=5))
        assert not verdict.loss_streak_halted
        assert not verdict.block_entries

    def test_loss_increments_and_win_resets_before_the_latch(self):
        guard = _guard()
        updated = guard.register_round_trip(
            consecutive_losses=5,
            loss_streak_halted=False,
            realized_pnl=Decimal("-1"),
        )
        assert updated.consecutive_losses == 6
        assert updated.loss_streak_halted
        cleared = guard.register_round_trip(
            consecutive_losses=2,
            loss_streak_halted=False,
            realized_pnl=Decimal("3"),
        )
        assert cleared.consecutive_losses == 0
        assert not cleared.loss_streak_halted

    def test_win_does_not_clear_a_latched_streak(self):
        updated = _guard().register_round_trip(
            consecutive_losses=6,
            loss_streak_halted=True,
            realized_pnl=Decimal("10"),
        )
        assert updated.loss_streak_halted
        assert updated.consecutive_losses == 6


class TestStopFile:
    def test_stop_file_kills_the_process_and_blocks_entries(self):
        verdict = _guard().evaluate(_view(stop_file_present=True))
        assert verdict.kill_process
        assert verdict.block_entries
        assert not verdict.flatten
        assert any("STOP file" in reason for reason in verdict.reasons)


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError):
        _guard().evaluate(_view(now=datetime(2026, 11, 11, 12, 0)))
