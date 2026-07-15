import sys, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from datetime import datetime

lines = Path("theSecondBot.log").read_text(encoding="utf-8", errors="replace").split("\n")

skip_patterns = {}
for line in lines:
    if "skip" in line.lower():
        parts = line.split(" INFO ")
        if len(parts) > 1:
            msg = parts[1].strip()
            msg_norm = re.sub(r"0x[0-9a-fA-F]+", "ADDR", msg)
            msg_norm = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}[^\s]*", "ID", msg_norm)
            msg_norm = re.sub(r"\d+s before", "Ns before", msg_norm)
            msg_norm = msg_norm[:130]
            skip_patterns[msg_norm] = skip_patterns.get(msg_norm, 0) + 1

print("=== SKIP PATTERNS ===")
for msg, cnt in sorted(skip_patterns.items(), key=lambda x: -x[1])[:15]:
    print(f"{cnt:6d}x  {msg}")

# Find the long quiet periods (no trade entry for > 30 min)
print("\n=== LONG QUIET PERIODS (no trade entry > 30 min) ===")
entry_lines = [(i, line) for i, line in enumerate(lines) if "opening position" in line.lower() or "entered" in line.lower() or "fill" in line.lower()]
print(f"Trade entry-related lines found: {len(entry_lines)}")

# Just look at heartbeat timestamps to find gaps
ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
heartbeat_times = []
for line in lines:
    if "heartbeat" in line.lower():
        m = ts_pattern.match(line)
        if m:
            heartbeat_times.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))

print(f"\nHeartbeat events: {len(heartbeat_times)}")
if heartbeat_times:
    for i in range(1, len(heartbeat_times)):
        gap = (heartbeat_times[i] - heartbeat_times[i-1]).total_seconds()
        if gap > 300:
            print(f"  GAP {gap/60:.0f}min: {heartbeat_times[i-1]} --> {heartbeat_times[i]}")
