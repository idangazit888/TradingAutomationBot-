"""theSecondBot — VWAP-cross confirmation gate: fakeout filter.

VWAP only updates once per closed 1-minute candle, so during the
confirmation wait (well under a minute) the reference VWAP value is fixed —
only the live spot price moves. Confirming just means checking the spot
price is still on the trigger side of that fixed reference.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PendingConfirm:
    asset: str
    window_slug: str
    direction: str          # "UP" or "DOWN"
    vwap_ref: float          # vwap_prev captured at trigger time (fixed for the wait)
    trigger_ts: float
    m: int
    tier: str
    window_start_ts: float
    window_end_ts: float
    window_open: float
    gap_bps: float = 0.0
    vol_ratio: float = 0.0
    chop: int = 0


def still_holds(direction: str, spot_price: float, vwap_ref: float) -> bool:
    return spot_price > vwap_ref if direction == "UP" else spot_price < vwap_ref


def elapsed_ge(now: float, trigger_ts: float, seconds: int) -> bool:
    return (now - trigger_ts) >= seconds
