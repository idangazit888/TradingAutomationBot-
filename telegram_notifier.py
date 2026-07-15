"""
telegram_notifier.py – Send trading updates to Telegram

Sends:
- New window opening notifications
- Trade entry/exit notifications
- Session P&L summaries
- Periodic balance updates (every 5 min)
"""

import logging
import os
from typing import Optional
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trading updates to Telegram via bot API."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """
        Initialize Telegram notifier.

        Args:
            bot_token: Telegram bot token (from BotFather). Defaults to TELEGRAM_BOT_TOKEN env var
            chat_id: Chat ID to send messages to. Defaults to TELEGRAM_CHAT_ID env var
        """
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.bot_token and self.chat_id)

        if not self.enabled:
            log.warning("⚠️  Telegram notifications disabled (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env)")
        else:
            log.info("✅ Telegram notifier enabled")

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    async def send_message(self, text: str):
        """Send a message to Telegram."""
        if not self.enabled:
            return

        if not text.strip():
            return

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                }
                async with session.post(self.api_url, json=payload, timeout=5) as resp:
                    if resp.status != 200:
                        log.warning(f"Telegram error: HTTP {resp.status}")
                    else:
                        log.info("Telegram message sent")
        except Exception as e:
            log.error(f"Telegram send failed: {type(e).__name__}: {e}")
            # Retry once after 5 seconds
            import asyncio
            await asyncio.sleep(5)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(self.api_url, json=payload, timeout=5) as resp:
                        if resp.status != 200:
                            log.error(f"Telegram retry also failed — HTTP {resp.status} — message lost")
                        else:
                            log.info("Telegram message sent (on retry)")
            except Exception:
                log.error("Telegram retry also failed — message lost")

    async def notify_new_window(self, symbol: str, direction: str, rsi: float, btc_price: float):
        """Notify when a new BTC 5-min UP/DOWN window opens."""
        if not self.enabled:
            return

        msg = f"""
🎯 <b>NEW WINDOW OPENED</b>

📊 <b>{symbol}</b>
🔹 Direction: <b>{direction}</b>
📈 RSI: <b>{rsi:.1f}</b>
💰 BTC Price: <b>${btc_price:.2f}</b>
⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())

    async def notify_entry(self, symbol: str, direction: str, entry_price: float, shares: float, usdc: float):
        """Notify when we enter a trade."""
        if not self.enabled:
            return

        msg = f"""
🎯 <b>ENTRY OPENED</b>

📊 <b>{symbol}</b>
🔹 Side: <b>{direction}</b>
💵 Entry Price: <b>${entry_price*100:.1f}¢</b>
📦 Shares: <b>{shares:.2f}</b>
💰 Invested: <b>${usdc:.2f}</b>
⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())

    async def notify_exit(self, symbol: str, direction: str, entry_price: float, exit_price: float,
                         pnl: float, reason: str, duration_sec: int):
        """Notify when we exit a trade."""
        if not self.enabled:
            return

        emoji = "✅" if pnl >= 0 else "❌"
        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        msg = f"""
{emoji} <b>EXIT: {reason.upper()}</b>

📊 <b>{symbol}</b>
🔹 Side: <b>{direction}</b>
📍 Entry: <b>${entry_price*100:.1f}¢</b>
📍 Exit: <b>${exit_price*100:.1f}¢</b>
💵 P&L: <b>${pnl:+.4f}</b> ({pnl_pct:+.2f}%)
⏱️ Duration: <b>{duration_sec}s</b>
⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())

    async def notify_session_summary(self, balance_start: float, balance_end: float,
                                    n_trades: int, n_wins: int, n_losses: int,
                                    total_fees: float, duration_str: str):
        """Notify session summary when bot stops."""
        if not self.enabled:
            return

        session_pnl = balance_end - balance_start
        win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0

        emoji = "📈" if session_pnl >= 0 else "📉"

        msg = f"""
{emoji} <b>SESSION SUMMARY</b>

💰 <b>Starting Balance:</b> ${balance_start:.4f}
💰 <b>Ending Balance:</b> ${balance_end:.4f}
📊 <b>Session P&L:</b> ${session_pnl:+.4f}

📈 <b>Trades:</b> {n_trades}
✅ <b>Wins:</b> {n_wins}
❌ <b>Losses:</b> {n_losses}
📊 <b>Win Rate:</b> {win_rate:.1f}%

💸 <b>Total Fees Paid:</b> ${total_fees:.4f}
⏱️ <b>Duration:</b> {duration_str}

⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())

    async def notify_balance_update(self, balance: float, session_pnl: float, n_trades: int,
                                   n_wins: int, n_losses: int, rsi: float, signal: str):
        """Notify balance update (every 5 minutes)."""
        if not self.enabled:
            return

        emoji = "📈" if session_pnl >= 0 else "📉"
        win_rate = (n_wins / (n_wins + n_losses) * 100) if (n_wins + n_losses) > 0 else 0

        msg = f"""
{emoji} <b>BALANCE UPDATE</b>

💰 <b>Current Balance:</b> ${balance:.4f}
📊 <b>Session P&L:</b> ${session_pnl:+.4f}

📈 <b>Trades:</b> {n_trades} ({n_wins}W / {n_losses}L)
📊 <b>Win Rate:</b> {win_rate:.1f}%

🎯 <b>Signal:</b> {signal}
📊 <b>RSI:</b> {rsi:.1f}

⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())

    async def notify_window_closed(self, window_slug: str, direction: str, balance: float,
                                   session_pnl: float, n_trades: int, n_wins: int, n_losses: int):
        """Notify balance update after a 5-min BTC window closes/resolves."""
        if not self.enabled:
            return

        emoji = "📈" if session_pnl >= 0 else "📉"
        win_rate = (n_wins / (n_wins + n_losses) * 100) if (n_wins + n_losses) > 0 else 0

        msg = f"""
{emoji} <b>WINDOW CLOSED: {direction}</b>

🎯 <b>Market:</b> {window_slug}

💰 <b>Current Balance:</b> ${balance:.4f}
📊 <b>Session P&L:</b> ${session_pnl:+.4f}

📈 <b>Trades:</b> {n_trades} ({n_wins}W / {n_losses}L)
📊 <b>Win Rate:</b> {win_rate:.1f}%

⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())

    async def notify_warning(self, title: str, message: str):
        """Notify warning (e.g., insufficient balance, daily loss limit)."""
        if not self.enabled:
            return

        msg = f"""
⚠️ <b>{title.upper()}</b>

{message}

⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())

    async def notify_startup(self, dry_run: bool = True):
        """Notify bot startup."""
        if not self.enabled:
            return

        if dry_run:
            mode_line = "🟡 <b>PAPER MODE — NO REAL ORDERS</b>"
        else:
            mode_line = "🔴 <b>LIVE MODE — REAL ORDERS WILL BE PLACED</b>"

        msg = f"""
🚀 <b>BOT STARTED</b>

{mode_line}
📊 Monitoring BTC 5-minute markets on Polymarket

⏰ Time: {_timestamp()}
"""
        await self.send_message(msg.strip())


def _timestamp() -> str:
    """Get current timestamp in UTC."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
