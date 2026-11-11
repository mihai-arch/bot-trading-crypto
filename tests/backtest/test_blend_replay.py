"""The ported blend should land near the out-of-sample figures in report section 9."""

from pathlib import Path

import pytest

from backtest.replay import REPORT_OOS, oos_metrics

_DATA = Path(__file__).resolve().parents[2] / "backtest" / "data"


@pytest.mark.skipif(
    not (_DATA / "BTCUSDT_D.csv.gz").exists(),
    reason="cached Bybit klines are not in backtest/data",
)
def test_blend_oos_is_near_the_report():
    result = oos_metrics(_DATA)
    # Section 9 post-hoc blend, out of sample: CAGR +20%, total +36%,
    # MaxDD -46%, Sharpe 0.68. The cache is Binance spot (Bybit's API is
    # blocked on some networks); the same rules land within a few points.
    assert result["CAGR"] == pytest.approx(REPORT_OOS["CAGR"], abs=0.05)
    assert result["TotalRet"] == pytest.approx(REPORT_OOS["TotalRet"], abs=0.08)
    assert result["MaxDD"] == pytest.approx(REPORT_OOS["MaxDD"], abs=0.08)
    assert result["Sharpe"] == pytest.approx(REPORT_OOS["Sharpe"], abs=0.15)
