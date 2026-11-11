"""Refresh ``backtest/data`` from the public Binance Vision archive.

Bybit's kline API is the bot's live source. It answers 403 from some
networks, so the committed cache is Binance spot BTCUSDT, ETHUSDT and
SOLUSDT, which tracks the same coins closely enough for the blend check
in ``tests/backtest/test_blend_replay.py``. No API key is used.

The files are the same shape the replay expects: gzipped CSV with
``ts,open,high,low,close,volume`` and ``ts`` in milliseconds.
"""

from __future__ import annotations

import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import pandas as pd

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
# Binance interval -> file suffix used by replay.py
INTERVALS = {"1d": "D", "4h": "240"}
OUT = Path(__file__).resolve().parent / "data"
_HEADERS = {"User-Agent": "bit-backtest/1.0"}


def _months():
    year, month = 2021, 7
    while (year, month) <= (2026, 10):
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def _urls(symbol: str, interval: str) -> list[str]:
    jobs = []
    for year, month in _months():
        name = f"{symbol}-{interval}-{year}-{month:02d}.zip"
        jobs.append(
            f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/{interval}/{name}"
        )
    for day in range(1, 11):
        name = f"{symbol}-{interval}-2026-11-{day:02d}.zip"
        jobs.append(
            f"https://data.binance.vision/data/spot/daily/klines/{symbol}/{interval}/{name}"
        )
    return jobs


def _get(url: str) -> bytes | None:
    for attempt in range(4):
        try:
            response = httpx.get(url, headers=_HEADERS, timeout=40.0, follow_redirects=True)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            time.sleep(0.4 * (attempt + 1))
    return None


def _read_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        raw = archive.read(archive.namelist()[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    if str(frame.iloc[0, 0]).lower().startswith("open"):
        frame = frame.iloc[1:]
    frame = frame.iloc[:, :6]
    frame.columns = ["ts", "open", "high", "low", "close", "volume"]
    return frame


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [
        (symbol, interval, url)
        for symbol in SYMBOLS
        for interval in INTERVALS
        for url in _urls(symbol, interval)
    ]
    parts: dict[tuple[str, str], list[pd.DataFrame]] = {
        (symbol, interval): [] for symbol in SYMBOLS for interval in INTERVALS
    }
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_get, url): (symbol, interval) for symbol, interval, url in jobs}
        for future in as_completed(futures):
            content = future.result()
            if content:
                symbol, interval = futures[future]
                parts[(symbol, interval)].append(_read_zip(content))
    for (symbol, interval), frames in parts.items():
        frame = pd.concat(frames, ignore_index=True)
        ts = pd.to_numeric(frame["ts"], errors="coerce")
        # Vision files switched from milliseconds to microseconds.
        micros = ts > 10**14
        ts = ts.where(~micros, ts // 1000)
        frame["ts"] = ts.astype("int64")
        frame = frame.dropna(subset=["ts"]).drop_duplicates("ts").sort_values("ts")
        path = OUT / f"{symbol}_{INTERVALS[interval]}.csv.gz"
        frame.to_csv(path, index=False, compression="gzip")
        print(f"{path.name} {len(frame)} rows")


if __name__ == "__main__":
    main()
