"""
slippage_logger.py — per-trade execution quality log

Writes two JSON lines to slippage_log.jsonl for every attempted entry:
  type=signal  — captured when the edge signal fires (pre-order)
  type=fill    — captured when the order returns (post-fill)

Slippage = actual_fill_price - best_ask_at_signal
  (0 = perfect fill at ask; positive = paid above ask; negative = got a better price)

Never raises — all errors are logged and swallowed so this never affects trading.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SlippageLogger:
    def __init__(self, path: str = "slippage_log.jsonl"):
        self._path = Path(path)
        self._pending: dict = {}  # market_id → signal snapshot (for slippage calc at fill)

    def log_signal(self, *, market_id: str, signal_ts: float, direction: str,
                   best_bid, best_ask, ask_depth,
                   btc_price, btc_move_15s, fair_value, edge):
        entry = {
            "type":                 "signal",
            "market_id":            market_id,
            "signal_ts":            round(signal_ts, 3),
            "direction":            direction,
            "best_bid_at_signal":   _r4(best_bid),
            "best_ask_at_signal":   _r4(best_ask),
            "ask_depth":            ask_depth,
            "btc_price":            _r2(btc_price),
            "btc_move_15s":         _r2(btc_move_15s),
            "fair_value":           _r4(fair_value),
            "edge":                 _r4(edge),
        }
        self._pending[market_id] = entry
        self._write(entry)

    def log_fill(self, *, market_id: str, fill_ts: float, fill_status: str,
                 actual_fill_price, shares_filled):
        signal = self._pending.pop(market_id, {})
        best_ask  = signal.get("best_ask_at_signal")
        signal_ts = signal.get("signal_ts", fill_ts)
        slippage  = (_r4(actual_fill_price - best_ask)
                     if best_ask is not None and actual_fill_price is not None else None)
        entry = {
            "type":               "fill",
            "market_id":          market_id,
            "signal_ts":          signal_ts,
            "fill_ts":            round(fill_ts, 3),
            "fill_status":        fill_status,
            "actual_fill_price":  _r4(actual_fill_price),
            "shares_filled":      _r2(shares_filled),
            "latency_ms":         round((fill_ts - signal_ts) * 1000) if signal_ts else None,
            "slippage":           slippage,
        }
        self._write(entry)

    def _write(self, entry: dict):
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"slippage_logger write failed: {e}")


def _r4(v):
    return round(v, 4) if v is not None else None

def _r2(v):
    return round(v, 2) if v is not None else None
