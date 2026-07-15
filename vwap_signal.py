"""
# ⚠️ RETIRED 2026-07-06 — replaced by the intra-window Up/Down bot (ud_*.py).
# Kept only so historical tests still pass. Not imported by run.py.

theSecondBot — VWAP candle-close signal (pure).

Signal on a CONFIRMED candle, VWAP reference = value at the candle's OPEN
(vwap_prev, computed before this candle's contribution):
    opened_below  = open  <  vwap_prev
    crossed_above = high  >= vwap_prev
    gap_enough    = (vwap_prev - open) / vwap_prev >= min_gap_pct
Entry is the NEXT window. Win = window close >= window open (Kraken-close proxy).
"""
from __future__ import annotations

from dataclasses import dataclass


def check_signal(candle, vwap_prev: float | None, min_gap_pct: float) -> bool:
    if vwap_prev is None or vwap_prev <= 0:
        return False
    opened_below = candle.open < vwap_prev
    crossed_above = candle.high >= vwap_prev
    gap_enough = (vwap_prev - candle.open) / vwap_prev >= min_gap_pct
    return opened_below and crossed_above and gap_enough


@dataclass
class CandleSignal:
    stream_key: str
    signal_ts: int          # the signal candle's open ts
    vwap_prev: float
    candle_open: float
    gap_pct: float


@dataclass
class WindowEntry:
    stream_key: str
    window_start_ts: int
    window_end_ts: int
    window_slug: str
