"""theSecondBot — append-only ledger of every signal (taken or skipped)."""
from __future__ import annotations

import csv
import os

FIELDS = [
    "ts",
    "asset",
    "direction",
    "m",
    "gap_bps",
    "vol_ratio",
    "chop",
    "tier",
    "ask",
    "bid",
    "true_prob",
    "max_price",
    "action",
    "size_usd",
    "shares",
    "fee",
    "outcome",
    "pnl",
    "reason",
]


class Ledger:
    """Append-only CSV ledger of signal evaluations."""

    def __init__(self, path: str):
        """Initialize ledger at given path (file created on first log call)."""
        self.path = path

    def log(self, **kwargs) -> None:
        """
        Append one row to the ledger.

        Unknown keys are ignored; missing keys are written as empty strings.
        Header is written only on first call (if file does not exist).
        """
        exists = os.path.exists(self.path)
        row = {k: kwargs.get(k, "") for k in FIELDS}
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if not exists:
                w.writeheader()
            w.writerow(row)
