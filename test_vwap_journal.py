"""T10 — VwapJournal: one row per (stream_key,window), clean entry_* schema."""
import csv
from vwap_journal import VwapJournal, COLUMNS


def test_columns_have_timeframe_and_entry_naming():
    for col in ("stream_key", "asset", "timeframe", "resolution_source",
                "close_margin_price", "close_margin_pct",
                "entry_up_ask_at_signal", "entry_assumed_fill",
                "gap_pct", "vwap_prev", "window_pnl",
                "balance_before", "cost", "payout", "realized_pnl", "balance_after",
                "vwap_reconstructed", "would_halt", "dry_run"):
        assert col in COLUMNS, f"{col} missing"
    # no leg2 / dip columns survive the rename
    assert not any(c.startswith("leg2_") for c in COLUMNS)
    assert not any("dip" in c for c in COLUMNS)


def test_writes_header_then_rows(tmp_path):
    p = str(tmp_path / "vwap_trades.csv")
    j = VwapJournal(p)
    j.append_window({"stream_key": "BTC 15m", "asset": "BTC", "timeframe": "15m",
                     "window_slug": "btc-1", "resolved_outcome": "Up",
                     "realized_pnl": 4.0, "balance_after": 304.0})
    j.append_window({"stream_key": "SOL 5m", "asset": "SOL", "timeframe": "5m",
                     "window_slug": "sol-1", "resolved_outcome": "Down",
                     "realized_pnl": -6.0, "balance_after": 298.0})

    with open(p, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["stream_key"] == "BTC 15m" and rows[0]["timeframe"] == "15m"
    assert rows[1]["asset"] == "SOL" and rows[1]["realized_pnl"] == "-6.0"
    # missing fields are written as empty, not crashing
    assert rows[0]["entry_assumed_fill"] == ""


def test_header_written_only_once(tmp_path):
    p = str(tmp_path / "vwap_trades.csv")
    j = VwapJournal(p)
    j.append_window({"stream_key": "BTC 15m"})
    j2 = VwapJournal(p)            # reopen existing file
    j2.append_window({"stream_key": "ETH 5m"})
    with open(p, newline="") as f:
        lines = f.read().strip().splitlines()
    assert lines[0].startswith("window_start_utc")
    assert sum(1 for ln in lines if ln.startswith("window_start_utc")) == 1
    assert len(lines) == 3  # header + 2 rows
