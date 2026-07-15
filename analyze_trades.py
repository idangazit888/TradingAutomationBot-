"""
Trade journal analysis.

Usage:
    python analyze_trades.py           # all trades
    python analyze_trades.py --last 20 # most recent N trades
"""

import argparse
import json
import sys
from pathlib import Path

JOURNAL_PATH = Path("trades.json")


# ── helpers ──────────────────────────────────────────────────────────────────

def load_trades(last_n=None):
    if not JOURNAL_PATH.exists():
        print("No trades.json found. Run the bot for a while first.")
        sys.exit(0)
    trades = []
    with JOURNAL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if last_n:
        trades = trades[-last_n:]
    return trades


def stats(trades):
    """Return (n, wins, losses, wr_pct, total_pnl) for a list of trades."""
    n = len(trades)
    if n == 0:
        return 0, 0, 0, 0.0, 0.0
    wins = sum(1 for t in trades if t.get("won", False))
    losses = n - wins
    wr = wins / n * 100
    total_pnl = sum(t.get("pnl_dollars", 0.0) for t in trades)
    return n, wins, losses, wr, total_pnl


def band_table(trades, key_fn, bands, band_labels):
    """
    Bucket trades by key_fn(trade) into bands and print a table.
    bands: list of upper bounds (last band = everything above prev upper)
    band_labels: matching list of label strings
    """
    buckets = [[] for _ in bands]
    for t in trades:
        val = key_fn(t)
        if val is None:
            continue
        placed = False
        for i, upper in enumerate(bands[:-1]):
            if val < upper:
                buckets[i].append(t)
                placed = True
                break
        if not placed:
            buckets[-1].append(t)

    rows = []
    for label, bucket in zip(band_labels, buckets):
        n, wins, _, wr, pnl = stats(bucket)
        rows.append((label, n, wr, pnl))
    return rows


def print_band_table(rows, warn_wr=55.0):
    for label, n, wr, pnl in rows:
        if n == 0:
            continue
        flag = "  << losing band" if wr < warn_wr else ""
        sign = "+" if pnl >= 0 else ""
        print(f"  {label:<20}  {n:>3} trades  {wr:>5.1f}% WR  {sign}${pnl:.2f}{flag}")


# ── section printers ─────────────────────────────────────────────────────────

def section_overview(trades):
    n, wins, losses, wr, pnl = stats(trades)
    avg = pnl / n if n else 0
    sign = "+" if pnl >= 0 else ""
    print(f"Total trades: {n}   Wins: {wins}   Losses: {losses}   WR: {wr:.1f}%")
    print(f"Total P&L: {sign}${pnl:.2f}   Avg/trade: {sign}${avg:.3f}")


def section_by_edge(trades):
    print("\n--- BY EDGE BAND ---")
    rows = band_table(
        trades,
        key_fn=lambda t: t.get("edge"),
        bands=[0.25, 0.30, 0.40, float("inf")],
        band_labels=["0.22-0.25", "0.25-0.30", "0.30-0.40", ">0.40"],
    )
    print_band_table(rows)


def section_by_fair(trades):
    print("\n--- BY FAIR VALUE ---")
    rows = band_table(
        trades,
        key_fn=lambda t: t.get("fair_value"),
        bands=[0.70, 0.80, 0.90, float("inf")],
        band_labels=["0.65-0.70", "0.70-0.80", "0.80-0.90", ">0.90"],
    )
    print_band_table(rows)


def section_by_sigma_regime(trades):
    print("\n--- BY SIGMA REGIME ---")
    rows = band_table(
        trades,
        key_fn=lambda t: t.get("sigma_ratio"),
        bands=[0.8, 1.5, 2.5, float("inf")],
        band_labels=["below 0.8x avg", "0.8x-1.5x avg", "1.5x-2.5x avg", ">2.5x avg"],
    )
    print_band_table(rows)


def section_by_hour(trades):
    print("\n--- BY TIME OF DAY (UTC) ---")
    hour_buckets = {rng: [] for rng in ["00-06", "06-12", "12-18", "18-24"]}
    for t in trades:
        h = t.get("entry_hour_utc")
        if h is None:
            continue
        if h < 6:
            hour_buckets["00-06"].append(t)
        elif h < 12:
            hour_buckets["06-12"].append(t)
        elif h < 18:
            hour_buckets["12-18"].append(t)
        else:
            hour_buckets["18-24"].append(t)
    for label, bucket in hour_buckets.items():
        n, wins, _, wr, pnl = stats(bucket)
        if n == 0:
            continue
        sign = "+" if pnl >= 0 else ""
        print(f"  {label}:  {n:>3} trades  {wr:>5.1f}% WR  {sign}${pnl:.2f}")


def section_by_entry_second(trades):
    print("\n--- BY ENTRY SECOND IN WINDOW ---")
    rows = band_table(
        trades,
        key_fn=lambda t: t.get("seconds_elapsed_at_entry"),
        bands=[120, 180, 240, float("inf")],
        band_labels=["s90-s120", "s120-s180", "s180-s240", "s240+"],
    )
    print_band_table(rows)


def section_by_spread(trades):
    print("\n--- BY SPREAD AT ENTRY ---")
    rows = band_table(
        trades,
        key_fn=lambda t: t.get("spread_at_entry"),
        bands=[0.04, 0.08, float("inf")],
        band_labels=["spread <0.04", "spread 0.04-0.08", "spread >0.08"],
    )
    print_band_table(rows, warn_wr=60.0)


def section_by_slippage(trades):
    print("\n--- BY SLIPPAGE ---")
    rows = band_table(
        trades,
        key_fn=lambda t: t.get("slippage"),
        bands=[0.01, 0.03, float("inf")],
        band_labels=["slippage <0.01", "slippage 0.01-0.03", "slippage >0.03"],
    )
    print_band_table(rows, warn_wr=60.0)


def section_latency(trades):
    print("\n--- LATENCY ANALYSIS ---")
    s2o = [t["ms_signal_to_order"] for t in trades if t.get("ms_signal_to_order") is not None]
    o2f = [t["ms_order_to_fill"] for t in trades if t.get("ms_order_to_fill") is not None]
    if s2o:
        print(f"  Avg signal-to-order: {sum(s2o)/len(s2o):.0f}ms")
    if o2f:
        print(f"  Avg order-to-fill:   {sum(o2f)/len(o2f):.0f}ms")
    all_lat = [t.get("ms_signal_to_order", 0) + t.get("ms_order_to_fill", 0) for t in trades]
    slow = [l for l in all_lat if l > 1000]
    if all_lat:
        print(f"  Worst latency: {max(all_lat):.0f}ms ({len(slow)} trades >1000ms)")
    slow_trades = [t for t in trades
                   if (t.get("ms_signal_to_order", 0) + t.get("ms_order_to_fill", 0)) > 500]
    if slow_trades and trades:
        _, sw, _, swr, _ = stats(slow_trades)
        pct = len(slow_trades) / len(trades) * 100
        print(f"  Trades >500ms latency: {len(slow_trades)} ({pct:.1f}%) — WR {swr:.1f}%")


def section_recommendations(trades):
    print("\n--- WHAT TO FIX ---")
    issues = []
    suggestions = []

    # Spread
    wide_spread = [t for t in trades if (t.get("spread_at_entry") or 0) > 0.08]
    if wide_spread:
        _, _, _, wr, _ = stats(wide_spread)
        if wr < 60:
            issues.append(f"Trades with spread >0.08: {wr:.1f}% WR — add spread filter at 0.08")
        else:
            suggestions.append(f"Wide spread trades ({wr:.1f}% WR) still profitable — no filter needed yet")

    # Fair value low band
    low_fair = [t for t in trades if 0.65 <= (t.get("fair_value") or 0) < 0.70]
    if low_fair:
        _, _, _, wr, _ = stats(low_fair)
        if wr < 60:
            issues.append(f"Trades with fair 0.65-0.70: {wr:.1f}% WR — raise min_fair_value to 0.70?")
        else:
            suggestions.append(f"Fair 0.65-0.70 band ({wr:.1f}% WR) — keep min_fair_value at 0.65")

    # Low sigma regime
    low_sigma = [t for t in trades if (t.get("sigma_ratio") or 1.0) < 0.8]
    if low_sigma:
        _, _, _, wr, _ = stats(low_sigma)
        if wr < 60:
            issues.append(f"Trades with sigma below 0.8x avg: {wr:.1f}% WR — raise min_sigma_multiplier?")
        else:
            suggestions.append(f"Low-sigma trades ({wr:.1f}% WR) still profitable")

    # High slippage
    high_slip = [t for t in trades if (t.get("slippage") or 0) > 0.03]
    if high_slip:
        _, _, _, wr, _ = stats(high_slip)
        if wr < 60:
            issues.append(f"Slippage >0.03 on {len(high_slip)} trades ({wr:.1f}% WR) — max_entry_slippage working?")

    # Sweet spots
    sweet_sigma = [t for t in trades if 0.8 <= (t.get("sigma_ratio") or 0) <= 1.5]
    if sweet_sigma:
        _, _, _, wr, pnl = stats(sweet_sigma)
        suggestions.append(f"Trades 0.8x-1.5x sigma: {wr:.1f}% WR — this is your best regime")

    best_timing = [t for t in trades if 120 <= (t.get("seconds_elapsed_at_entry") or 0) < 180]
    if best_timing:
        _, _, _, wr, _ = stats(best_timing)
        suggestions.append(f"s120-s180 entries: {wr:.1f}% WR — consider tightening entry window")

    for msg in issues:
        print(f"  [!] {msg}")
    for msg in suggestions:
        print(f"  [+] {msg}")
    if not issues and not suggestions:
        print("  Not enough data yet — run more trades.")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyze trade journal")
    parser.add_argument("--last", type=int, default=None, metavar="N",
                        help="Analyze only the last N trades")
    args = parser.parse_args()

    trades = load_trades(last_n=args.last)
    if not trades:
        print("No trades found.")
        return

    scope = f"last {args.last}" if args.last else "all"
    print(f"\n=== TRADE JOURNAL ANALYSIS ({scope} trades) ===")
    section_overview(trades)
    section_by_edge(trades)
    section_by_fair(trades)
    section_by_sigma_regime(trades)
    section_by_hour(trades)
    section_by_entry_second(trades)
    section_by_spread(trades)
    section_by_slippage(trades)
    section_latency(trades)
    section_recommendations(trades)
    print()


if __name__ == "__main__":
    main()
