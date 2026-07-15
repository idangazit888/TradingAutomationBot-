"""
vwap_telegram.py — read-only Telegram menu for the VWAP paper bot.

Long-polls getUpdates and answers status questions from your phone. SECURITY:
commands are obeyed ONLY from the whitelisted TELEGRAM_CHAT_ID; anything else
is logged and ignored. READ-ONLY by design — the paper bot has no stop/halt/
close controls here.

Commands:
  /status    — alive + uptime, balance, P&L, open count, feed health
  /positions — open position details
  /stats     — trades, W/L, per-stream P&L, peak, max drawdown
  /help      — this list
"""

import asyncio
import logging

import aiohttp

log = logging.getLogger("theSecondBot.vwap_tg")


def _age(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s}s" if s < 120 else f"{s // 60}m"


class VwapTelegramController:
    def __init__(self, bot, notifier):
        self.bot = bot
        self.notifier = notifier
        self.token = str(getattr(notifier, "bot_token", "") or "")
        self.chat_id = str(getattr(notifier, "chat_id", "") or "")
        self.enabled = bool(notifier and getattr(notifier, "enabled", False))
        self._offset = 0
        self._base = f"https://api.telegram.org/bot{self.token}"

    async def _reply(self, text: str):
        if self.notifier:
            await self.notifier.send_message(text)

    # ── main loop ─────────────────────────────────────────────────────────────
    async def run(self):
        if not self.enabled:
            log.info("Telegram menu disabled (no token/chat_id in .env)")
            return
        async with aiohttp.ClientSession() as session:
            await self._drain_backlog(session)
            try:
                await self._reply(self._help_text("🎮 <b>Menu online.</b>\n\n"))
            except Exception as e:
                log.warning(f"startup reply failed: {type(e).__name__}: {e}")
            while True:
                try:
                    await self._poll_once(session)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning(f"tg poll error: {type(e).__name__}: {e}")
                    await asyncio.sleep(3)

    async def _drain_backlog(self, session):
        """Skip commands queued before startup."""
        try:
            async with session.get(f"{self._base}/getUpdates",
                                   params={"timeout": 0, "offset": -1},
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
            for u in data.get("result", []):
                self._offset = u["update_id"] + 1
        except Exception as e:
            log.debug(f"drain backlog failed: {e}")

    async def _poll_once(self, session):
        params = {"timeout": 30, "offset": self._offset}
        async with session.get(f"{self._base}/getUpdates", params=params,
                               timeout=aiohttp.ClientTimeout(total=40)) as r:
            data = await r.json()
        if not data.get("ok"):
            await asyncio.sleep(3)
            return
        for u in data.get("result", []):
            self._offset = u["update_id"] + 1
            await self._handle_update(u)

    async def _handle_update(self, update: dict) -> bool:
        """Dispatch one update. Returns True iff a command was handled."""
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return False
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != self.chat_id:
            log.warning(f"⛔ ignoring command from unauthorized chat {chat_id}")
            return False
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return False
        await self._dispatch(text)
        return True

    # ── command router ────────────────────────────────────────────────────────
    async def _dispatch(self, text: str):
        cmd = text.split()[0].lstrip("/").lower().split("@")[0]
        log.info(f"📲 Telegram command: /{cmd}")
        try:
            if cmd in ("status", "s"):
                await self._reply(self._status_text())
            elif cmd in ("positions", "position", "pos", "p"):
                await self._reply(self._positions_text())
            elif cmd in ("stats", "stat"):
                await self._reply(self._stats_text())
            elif cmd in ("help", "h", "commands", "start"):
                await self._reply(self._help_text())
            else:
                await self._reply(f"❓ Unknown command /{cmd}. Send /help for the list.")
        except Exception as e:
            log.warning(f"command /{cmd} failed: {type(e).__name__}: {e}")
            await self._reply(f"⚠️ /{cmd} failed: {type(e).__name__}: {e}")

    # ── text builders ─────────────────────────────────────────────────────────
    def _status_text(self) -> str:
        now = self.bot._now()
        st = self.bot.account.stats()
        uptime = _age(now - self.bot._bot_start_ts)
        feed = self.bot.poly_feed
        feed_line = "Polymarket feed: n/a"
        if feed is not None:
            # last_message_ts is the real liveness signal (a zombie socket can
            # stay "connected" while pushing zero book messages), so always
            # report the freshness age and flag the socket state separately.
            feed_line = f"Polymarket feed: last msg {_age(now - feed.last_message_ts)} ago"
            if not feed.connected:
                feed_line += " (socket DISCONNECTED)"
        # oldest Kraken candle across engines — one number answers "are candles flowing?"
        candle_ages = [now - e.session.last_ts
                       for e in self.bot.engines.values() if e.session.last_ts > 0]
        kraken_line = (f"Kraken candles: oldest stream {_age(max(candle_ages))} ago"
                       if candle_ages else "Kraken candles: warming up")
        mode = "PAPER (dry_run)" if self.bot.cfg.dry_run else "LIVE"
        lines = [f"✅ <b>Bot alive</b> [{mode}] — uptime {uptime}",
                 f"🕐 Waiter: ${st['balance']:.2f}   "
                 f"P&L: ${st['realized_pnl_total']:+.2f} ({st['pnl_pct']:+.1f}%)   "
                 f"open: {st['open_positions']}"]
        ghost_account = getattr(self.bot, "ghost_account", None)
        if ghost_account is not None:
            gst = ghost_account.stats()
            lines.append(f"👻 Ghost:  ${gst['balance']:.2f}   "
                        f"P&L: ${gst['realized_pnl_total']:+.2f} ({gst['pnl_pct']:+.1f}%)   "
                        f"open: {gst['open_positions']}")
        for attr, tag in (("gap_account", "📐 Gap:   "), ("stack_account", "🧱 Stack: ")):
            acct = getattr(self.bot, attr, None)
            if acct is not None:
                ast = acct.stats()
                lines.append(f"{tag}${ast['balance']:.2f}   "
                            f"P&L: ${ast['realized_pnl_total']:+.2f} ({ast['pnl_pct']:+.1f}%)   "
                            f"open: {ast['open_positions']}")
        lines.append(feed_line)
        lines.append(kraken_line)
        return "\n".join(lines)

    @staticmethod
    def _positions_block(label: str, positions: dict, now: float) -> str:
        if not positions:
            return f"{label}: no open positions."
        lines = [f"{label}:"]
        for pos in positions.values():
            left = int(pos["window_end_ts"] - now)
            lines.append(f"  {pos['stream_key']} {pos['window_slug']}: "
                         f"{pos['shares']:.1f}sh @ {pos['fill']:.3f} "
                         f"(${pos['stake']:.2f}) — closes in {left}s")
        return "\n".join(lines)

    def _positions_text(self) -> str:
        now = self.bot._now()
        ghost_positions = getattr(self.bot, "_ghost_positions", None)
        if ghost_positions is None:
            # old single-arm bot — unchanged behavior
            if not self.bot._positions:
                return "📦 No open positions."
            return self._positions_block("📦 Open positions", self.bot._positions, now)
        waiter_block = self._positions_block("🕐 Waiter", self.bot._positions, now)
        ghost_block = self._positions_block("👻 Ghost", ghost_positions, now)
        blocks = [waiter_block, ghost_block]
        for attr, tag in (("_gap_positions", "📐 Gap"), ("_stack_positions", "🧱 Stack")):
            pos = getattr(self.bot, attr, None)
            if pos is not None:
                blocks.append(self._positions_block(tag, pos, now))
        return "📦 <b>Open positions</b>\n" + "\n".join(blocks)

    @staticmethod
    def _stats_block(label: str, st: dict) -> str:
        per_stream = "\n".join(f"    {k}: ${v:+.2f}"
                               for k, v in sorted(st["per_stream_pnl"].items()))
        return (f"<b>{label}</b>\n"
               f"  Balance: ${st['balance']:.2f}   "
               f"P&L: ${st['realized_pnl_total']:+.2f} ({st['pnl_pct']:+.1f}%)\n"
               f"  Trades: {st['trade_count']}   "
               f"W/L: {st['win_count']}/{st['loss_count']}\n"
               f"  Peak: ${st['peak_balance']:.2f}   "
               f"Max DD: ${st['max_drawdown']:.2f}\n"
               f"  Per stream:\n{per_stream or '    (none yet)'}")

    def _stats_text(self) -> str:
        st = self.bot.account.stats()
        ghost_account = getattr(self.bot, "ghost_account", None)
        if ghost_account is None:
            # old single-arm bot — unchanged behavior
            per_stream = "\n".join(f"  {k}: ${v:+.2f}"
                                   for k, v in sorted(st["per_stream_pnl"].items()))
            return (f"📈 <b>Paper stats</b>\n"
                    f"Balance: ${st['balance']:.2f}   "
                    f"P&L: ${st['realized_pnl_total']:+.2f} ({st['pnl_pct']:+.1f}%)\n"
                    f"Trades: {st['trade_count']}   "
                    f"W/L: {st['win_count']}/{st['loss_count']}\n"
                    f"Peak: ${st['peak_balance']:.2f}   "
                    f"Max DD: ${st['max_drawdown']:.2f}\n"
                    f"Per stream:\n{per_stream or '  (none yet)'}")
        gst = ghost_account.stats()
        blocks = [self._stats_block("🕐 Waiter (45s confirm)", st),
                  self._stats_block("👻 Ghost (0s immediate)", gst)]
        gap_account = getattr(self.bot, "gap_account", None)
        if gap_account is not None:
            blocks.append(self._stats_block("📐 Gap rule (1s)", gap_account.stats()))
        stack_account = getattr(self.bot, "stack_account", None)
        if stack_account is not None:
            blocks.append(self._stats_block("🧱 Full stack (gap+vel+CVD)",
                                            stack_account.stats()))
        return "📈 <b>Paper stats — all arms</b>\n\n" + "\n\n".join(blocks)

    def _help_text(self, prefix: str = "") -> str:
        return (prefix +
                "🎮 <b>Commands</b> (read-only)\n"
                "/status — alive, uptime, balance, P&L, feed health (both arms)\n"
                "/positions — open position details (both arms)\n"
                "/stats — trades, W/L, per-stream P&L (both arms)\n"
                "/help — this list")
