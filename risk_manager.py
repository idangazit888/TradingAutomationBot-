"""Hard risk limits — non-negotiable safety net."""

import json
import os
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RiskState:
    starting_bankroll: float
    current_bankroll: float
    daily_starting_bankroll: float
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    consecutive_losses: int = 0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    cooldown_until: float = 0.0
    halt_until: float = 0.0
    halt_reason: str = ""
    last_reset_day: str = ""


class RiskManager:
    def __init__(self, starting_bankroll: float, max_daily_loss_pct: float = 0.05,
                 max_consecutive_losses: int = 5, max_drawdown_pct: float = 0.15,
                 min_bankroll: float = 10.0, cooldown_seconds: int = 300,
                 max_daily_loss_usd: float = 0.0):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_daily_loss_usd = max_daily_loss_usd
        self.max_consecutive_losses = max_consecutive_losses
        self.max_drawdown_pct = max_drawdown_pct
        self.min_bankroll = min_bankroll
        self.cooldown_seconds = cooldown_seconds
        self.state = RiskState(starting_bankroll, starting_bankroll, starting_bankroll)
        self.peak_bankroll = starting_bankroll
        self.load()

    def can_trade(self, current_ts: float) -> tuple[bool, str]:
        if current_ts < self.state.halt_until:
            return (False, f"HALTED: {self.state.halt_reason}")
        if current_ts < self.state.cooldown_until:
            remaining = int(self.state.cooldown_until - current_ts)
            return (False, f"cooldown: {remaining}s remaining")
        if self.state.current_bankroll < self.min_bankroll:
            return (False, f"bankroll below minimum: ${self.state.current_bankroll:.2f}")
        if self.max_daily_loss_usd > 0 and -self.state.daily_pnl >= self.max_daily_loss_usd:
            if not self.state.halt_until:
                self.state.halt_until = current_ts + 86400
                self.state.halt_reason = f"daily loss limit ${self.max_daily_loss_usd:.0f}"
                self.save()
            return (False, f"HALTED: {self.state.halt_reason}")
        daily_loss_pct = -self.state.daily_pnl / self.state.daily_starting_bankroll
        if daily_loss_pct >= self.max_daily_loss_pct:
            if not self.state.halt_until:
                self.state.halt_until = current_ts + 86400
                self.state.halt_reason = f"daily loss {daily_loss_pct:.1%}"
                self.save()
            return (False, f"HALTED: {self.state.halt_reason}")
        drawdown = (self.peak_bankroll - self.state.current_bankroll) / self.peak_bankroll
        if drawdown >= self.max_drawdown_pct:
            if not self.state.halt_until:
                self.state.halt_until = float('inf')
                self.state.halt_reason = f"drawdown {drawdown:.1%}"
                self.save()
            return (False, f"HALTED: {self.state.halt_reason}")
        return (True, "OK")

    def record_trade_close(self, pnl: float, current_ts: float) -> None:
        self.state.current_bankroll += pnl
        self.state.daily_pnl += pnl
        self.state.total_pnl += pnl
        self.state.total_trades += 1
        if pnl > 0:
            self.state.total_wins += 1
            self.state.consecutive_losses = 0
            if self.state.current_bankroll > self.peak_bankroll:
                self.peak_bankroll = self.state.current_bankroll
        else:
            self.state.total_losses += 1
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.max_consecutive_losses:
                self.state.cooldown_until = current_ts + self.cooldown_seconds
                self.state.consecutive_losses = 0
        self.save()

    def save(self, path: str = "") -> None:
        if not path:
            path = os.path.join(os.path.dirname(__file__), "risk_state.json")
        try:
            with open(path, "w") as f:
                json.dump({
                    "daily_pnl": self.state.daily_pnl,
                    "consecutive_losses": self.state.consecutive_losses,
                    "halt_until": self.state.halt_until,
                    "halt_reason": self.state.halt_reason,
                    "last_reset_day": datetime.utcnow().strftime("%Y-%m-%d"),
                }, f)
        except OSError:
            pass  # non-fatal — VPS path may not exist in dev/test

    def load(self, path: str = "") -> None:
        if not path:
            path = os.path.join(os.path.dirname(__file__), "risk_state.json")
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            today = datetime.utcnow().strftime("%Y-%m-%d")
            if data.get("last_reset_day") == today:
                self.state.daily_pnl = data["daily_pnl"]
                self.state.consecutive_losses = data["consecutive_losses"]
                self.state.halt_until = data["halt_until"]
                self.state.halt_reason = data["halt_reason"]
        except (OSError, KeyError, json.JSONDecodeError):
            pass  # corrupt or missing file — start fresh

    def reset_daily(self, current_ts: float) -> None:
        self.state.daily_starting_bankroll = self.state.current_bankroll
        self.state.daily_pnl = 0.0
        self.state.halt_until = 0.0
        self.state.halt_reason = ""

    def get_stats(self) -> dict:
        s = self.state
        win_rate = (s.total_wins / s.total_trades * 100) if s.total_trades > 0 else 0
        return {
            "bankroll": s.current_bankroll, "total_pnl": s.total_pnl,
            "daily_pnl": s.daily_pnl, "trades": s.total_trades,
            "wins": s.total_wins, "losses": s.total_losses, "win_rate": win_rate,
            "consecutive_losses": s.consecutive_losses, "peak": self.peak_bankroll,
            "drawdown_pct": (self.peak_bankroll - s.current_bankroll) / self.peak_bankroll * 100 if self.peak_bankroll > 0 else 0.0,
        }
