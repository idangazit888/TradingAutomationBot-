"""
theSecondBot — Telegram NotificationBatcher.

Exactly 4 message types:
  1. ENTRY            — batched per boundary (all streams entering this window)
  2. RESULT          — batched per boundary (all streams resolving this window)
  3. ACCOUNT SNAPSHOT — one per flushed boundary, from stats_fn()
  4. CRITICAL        — immediate, never batched

Entries/results buffer by boundary_ts and flush after grace_sec so all streams
sharing a boundary land in one message instead of N separate pings.
"""
from __future__ import annotations

import asyncio
import time


class NotificationBatcher:
    def __init__(self, telegram, stats_fn, now_fn=time.time, grace_sec: float = 3.0):
        self.tg = telegram
        self.stats_fn = stats_fn
        self._now = now_fn
        self.grace_sec = grace_sec
        self._entries: dict[int, list[str]] = {}
        self._results: dict[int, list[str]] = {}

    def add_entry(self, boundary_ts: int, text: str):
        self._entries.setdefault(int(boundary_ts), []).append(text)

    def add_result(self, boundary_ts: int, text: str):
        self._results.setdefault(int(boundary_ts), []).append(text)

    async def _send(self, text: str):
        if self.tg is None:
            return
        res = self.tg.send_message(text)
        if asyncio.iscoroutine(res):
            await res

    async def critical(self, text: str):
        """Type 4 — immediate, never batched."""
        await self._send(f"🚨 CRITICAL\n{text}")

    async def flush_due(self):
        now = self._now()
        due = sorted(b for b in set(self._entries) | set(self._results)
                     if b + self.grace_sec <= now)
        for b in due:
            entries = self._entries.pop(b, [])
            results = self._results.pop(b, [])
            if entries:
                await self._send("🟢 ENTRY\n" + "\n".join(entries))
            if results:
                await self._send("🏁 RESULT\n" + "\n".join(results))
            if entries or results:
                await self._send(self._format_snapshot(self.stats_fn()))

    def _format_snapshot(self, st: dict) -> str:
        lines = ["📊 ACCOUNT SNAPSHOT",
                 f"balance: {st.get('balance')}",
                 f"realized_pnl: {st.get('realized_pnl_total')}"]
        for k, v in (st.get("per_stream_pnl") or {}).items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)
