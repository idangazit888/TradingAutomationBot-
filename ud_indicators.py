"""theSecondBot — pure intra-window indicators (VWAP, volume, chop, gap)."""
from __future__ import annotations


def typical_price(c) -> float:
    return (c.high + c.low + c.close) / 3.0


def session_vwap(candles) -> float | None:
    sum_pv = sum(typical_price(c) * c.volume for c in candles)
    sum_v = sum(c.volume for c in candles)
    if sum_v <= 0.0:
        return None
    return sum_pv / sum_v


def vol_ma30(candles, upto_index: int) -> float | None:
    start = upto_index - 30
    if start < 0:
        window = candles[0:upto_index]
    else:
        window = candles[start:upto_index]
    if not window:
        return None
    return sum(c.volume for c in window) / len(window)


def vol_ratio(candles, index: int) -> float | None:
    ma = vol_ma30(candles, index)
    if ma is None or ma <= 0.0:
        return None
    return candles[index].volume / ma


def _side(close: float, vwap: float) -> int:
    return 1 if close >= vwap else -1


def chop(candles, vwaps, index: int, lookback: int = 15) -> int:
    start = max(0, index - lookback + 1)
    crosses = 0
    prev = None
    for i in range(start, index + 1):
        if vwaps[i] is None:
            prev = None
            continue
        s = _side(candles[i].close, vwaps[i])
        if prev is not None and s != prev:
            crosses += 1
        prev = s
    return crosses


def gap_bps(window_open: float, vwap_prev: float) -> float:
    if window_open <= 0.0:
        return 0.0
    return abs(window_open - vwap_prev) / window_open * 10000.0
