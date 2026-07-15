import sys, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from datetime import datetime

lines = Path("theSecondBot.log").read_text(encoding="utf-8", errors="replace").split("\n")

def show_around(target_str, context=30, keyword=None):
    """Show lines around a timestamp string"""
    for i, line in enumerate(lines):
        if target_str in line:
            start = max(0, i - 5)
            end = min(len(lines), i + context)
            for l in lines[start:end]:
                if keyword is None or keyword.lower() in l.lower():
                    print(l[:160])
            print("---")
            break

# Focus on today's 55-min gap (12:37 -> 13:32)
print("=== TODAY 12:37-13:33 GAP (55 min) ===")
for line in lines:
    ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    if ts_match:
        ts_str = ts_match.group(1)
        if "2026-06-01 12:3" in ts_str or "2026-06-01 12:4" in ts_str or "2026-06-01 12:5" in ts_str or "2026-06-01 13:0" in ts_str or "2026-06-01 13:1" in ts_str or "2026-06-01 13:2" in ts_str or "2026-06-01 13:3" in ts_str:
            # Filter to interesting lines only
            ll = line.lower()
            if any(k in ll for k in ["skip", "block", "edge", "sigma", "risk", "cool", "regime", "daily", "loss", "warn", "error", "heartbeat", "trade", "entry", "window", "paused", "halt", "stop", "no market"]):
                print(line[:160])

print()
print("=== TODAY 10:16-10:28 GAP (12 min) ===")
for line in lines:
    ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    if ts_match:
        ts_str = ts_match.group(1)
        if "2026-06-01 10:1" in ts_str or "2026-06-01 10:2" in ts_str:
            ll = line.lower()
            if any(k in ll for k in ["skip", "block", "edge", "sigma", "risk", "cool", "regime", "daily", "warn", "error", "heartbeat"]):
                print(line[:160])
