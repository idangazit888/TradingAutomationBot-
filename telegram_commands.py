"""
telegram_commands.py — remote control of theSecondBot via Telegram.

Long-polls Telegram getUpdates and dispatches slash commands so you can control
the running bot from your phone. SECURITY: commands are obeyed ONLY when they
come from the whitelisted TELEGRAM_CHAT_ID (the same chat the bot already sends
to). Anything from any other chat is logged and ignored.

This module only READS bot state and calls a few well-defined bot methods
(status_text, force_close_position, and two boolean flags). It never touches the
strategy math or places orders directly.

Commands:
  /status   — bankroll, P&L, open position, σ, BTC, run/pause state
  /stop     — pause NEW entries (an open position is still managed/exited)
  /start    — resume entries
  /position — show the open position (or 'none')
  /closenow — close the open position right now (manual exit)
  /halt     — emergency: pause AND close any open position
  /stats    — session performance
  /help     — list commands
"""

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger("theSecondBot.tgcmd")


class TelegramController:
    def __init__(self, bot, notifier):
        self.bot = bot
        self.notifier = notifier
        self.token = str(getattr(notifier, "bot_token", "") or "")
        self.chat_id = str(getattr(notifier, "chat_id", "") or "")
        self.enabled = bool(notifier and getattr(notifier, "enabled", False))
        self._offset = 0
        self._base = f"https://api.telegram.org/bot{self.token}"

    # ── outbound ──────────────────────────────────────────────────────────────
    async def _reply(self, text: str):
        if self.notifier:
            await self.notifier.send_message(text)

    # ── main loop ─────────────────────────────────────────────────────────────
    async def run(self):
        if not self.enabled:
            log.info("Telegram remote control disabled (no token/chat_id in .env)")
            return
        async with aiohttp.ClientSession() as session:
            await self._drain_backlog(session)
            await self._reply(self._help_text("🎮 <b>Remote control online.</b>\n\n"))
            while self.bot.running:
                try:
                    await self._poll_once(session)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning(f"tg poll error: {type(e).__name__}: {e}")
                    await asyncio.sleep(3)

    async def _drain_backlog(self, session):
        """Skip any commands that were queued before startup, so a stale /stop
        sent yesterday doesn't fire the moment the bot comes online."""
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
            msg = u.get("message") or u.get("edited_message")
            if not msg:
                continue
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id != self.chat_id:
                log.warning(f"⛔ ignoring command from unauthorized chat {chat_id}")
                continue
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                await self._dispatch(text)

    # ── command router ────────────────────────────────────────────────────────
    async def _dispatch(self, text: str):
        cmd = text.split()[0].lstrip("/").lower()
        cmd = cmd.split("@")[0]  # strip @botname suffix Telegram adds in groups
        log.info(f"📲 Telegram command: /{cmd}")
        try:
            if cmd in ("status", "s"):
                await self._reply(self.bot.status_text())
            elif cmd in ("stop", "pause"):
                self.bot._trading_enabled = False
                await self._reply("⏸️ <b>Trading paused.</b> No new entries. "
                                  "An open position (if any) is still managed. "
                                  "Send /start to resume.")
            elif cmd in ("start", "resume", "go"):
                self.bot._trading_enabled = True
                was_halted = self.bot.risk.state.halt_until > 0
                self.bot.risk.reset_daily(time.time())
                self.bot._daily_halt_alerted = False
                msg = "▶️ <b>Trading resumed.</b> New entries enabled."
                if was_halted:
                    msg += "\n🔓 Daily loss halt cleared."
                await self._reply(msg)
            elif cmd in ("position", "pos", "p"):
                await self._reply(self._position_text())
            elif cmd in ("closenow", "close", "flat"):
                await self._reply("⏳ Closing open position…")
                result = await self.bot.force_close_position()
                await self._reply(f"🚪 {result}")
            elif cmd == "halt":
                self.bot._trading_enabled = False
                await self._reply("🛑 <b>HALT</b> — pausing and closing…")
                result = await self.bot.force_close_position()
                await self._reply(f"🛑 Trading paused. {result}")
            elif cmd in ("stats", "stat"):
                await self._reply(self._stats_text())
            elif cmd in ("help", "h", "commands", "start_help"):
                await self._reply(self._help_text())
            else:
                await self._reply(f"❓ Unknown command /{cmd}. Send /help for the list.")
        except Exception as e:
            log.warning(f"command /{cmd} failed: {type(e).__name__}: {e}")
            await self._reply(f"⚠️ /{cmd} failed: {type(e).__name__}: {e}")

    # ── text builders ─────────────────────────────────────────────────────────
    def _position_text(self) -> str:
        pos = self.bot.open_position
        if not pos:
            return "📦 No open position."
        held = int(time.time() - pos.entry_ts)
        left = int(pos.window_end_ts - time.time())
        return (f"📦 <b>Open position</b>\n"
                f"Direction: <b>{pos.direction}</b>\n"
                f"Entry: {pos.entry_price:.3f}  Size: {pos.size_shares:.1f}sh "
                f"(${pos.size_usd:.2f})\n"
                f"Held: {held}s   Window closes in: {left}s")

    def _stats_text(self) -> str:
        s = self.bot.risk.get_stats()
        session_pnl = s["bankroll"] - self.bot.session_start_balance
        return (f"📈 <b>Session stats</b>\n"
                f"Bankroll: ${s['bankroll']:.2f}\n"
                f"Total P&L: ${s['total_pnl']:+.2f}   Session: ${session_pnl:+.2f}\n"
                f"Daily P&L: ${s['daily_pnl']:+.2f}\n"
                f"Trades: {s['trades']}   W/L: {s['wins']}/{s['losses']}   "
                f"WR: {s['win_rate']:.0f}%\n"
                f"Consecutive losses: {s['consecutive_losses']}\n"
                f"Peak: ${s['peak']:.2f}   Drawdown: {s['drawdown_pct']:.1f}%")

    def _help_text(self, prefix: str = "") -> str:
        return (prefix +
                "🎮 <b>Commands</b>\n"
                "/status — bankroll, P&L, position, σ, run/pause state\n"
                "/stop — pause new entries (position still managed)\n"
                "/start — resume new entries\n"
                "/position — show the open position\n"
                "/closenow — close the open position now\n"
                "/halt — pause AND close position (emergency)\n"
                "/stats — session performance\n"
                "/help — this list")
