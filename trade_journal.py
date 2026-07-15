"""
Append-only JSON-lines trade journal.

Every closed trade writes one line to trades.json with every piece of
signal, execution, and outcome data available at the time. Run
analyze_trades.py to read and analyze.
"""

import json
from pathlib import Path

JOURNAL_PATH = Path("trades.json")


def record(entry: dict) -> None:
    """Append one completed trade record as a JSON line to trades.json."""
    with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
