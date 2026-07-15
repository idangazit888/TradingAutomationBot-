"""
theSecondBot — VWAP paper journal.

One unified CSV, one row per (stream_key, window) where stream_key is
"{asset} {timeframe}" (e.g. "BTC 15m"). `timeframe` is its own column so 5m
and 15m P&L can be split independently in analysis. Single entry leg per window
(candle-close signal → next window); the shared-wallet equity curve makes the
$300 balance traceable over time.

Equity-curve note: `cost`/`payout`/`realized_pnl` are this window's own deltas;
`balance_before`/`balance_after` are wallet snapshots reflecting ALL streams'
interleaved activity (read the curve from balance_after ordered by time).
"""

from __future__ import annotations

import csv
import os

COLUMNS = [
    "window_start_utc", "window_end_utc", "stream_key", "asset", "timeframe",
    "window_slug", "window_open", "vwap_prev", "gap_pct", "open_below",
    "vwap_reconstructed", "qualified",
    "entry_up_ask_at_signal", "entry_assumed_fill", "entry_shares", "entry_stake",
    "entry_partial", "total_stake", "avg_entry_price",
    "resolution_source", "close_price", "close_margin_price", "close_margin_pct",
    "resolved_outcome", "window_pnl", "fees",
    "balance_before", "cost", "payout", "realized_pnl", "balance_after",
    "dry_run", "would_halt",
]


class VwapJournal:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(COLUMNS)

    def append_window(self, row: dict):
        """Append one window's row. Unknown keys are ignored; missing columns
        are written empty."""
        out = {c: row.get(c, "") for c in COLUMNS}
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writerow(out)
