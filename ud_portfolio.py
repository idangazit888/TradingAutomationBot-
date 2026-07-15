"""theSecondBot — portfolio risk: concurrency, circuit breaker, USD sizing."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PortfolioState:
    consecutive_losses: int = 0
    day_start_balance: float = 0.0
    halted_reason: str = ""
    utc_day: str = ""


class Portfolio:
    def __init__(self, cfg, day_start_balance: float, utc_day: str):
        self.cfg = cfg
        self.state = PortfolioState(day_start_balance=day_start_balance, utc_day=utc_day)

    def size_usd(self, size_mode: str, bankroll: float) -> float:
        pct = self.cfg.full_size_pct if size_mode == "full" else self.cfg.half_size_pct
        return bankroll * pct

    def can_open(self, asset: str, open_positions: list) -> tuple[bool, str]:
        if self.state.halted_reason:
            return (False, self.state.halted_reason)
        if len(open_positions) >= self.cfg.max_concurrent:
            return (False, "max concurrent windows")
        per_asset = sum(1 for p in open_positions if p.get("asset") == asset)
        if per_asset >= self.cfg.max_per_asset:
            return (False, "max per asset")
        return (True, "ok")

    def record_result(self, pnl: float, balance_now: float) -> None:
        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        if self.state.consecutive_losses >= self.cfg.circuit_consecutive_losses:
            self.state.halted_reason = (
                f"circuit: {self.state.consecutive_losses} consecutive losses")
        floor = self.state.day_start_balance * (1.0 - self.cfg.circuit_daily_loss_pct)
        if balance_now <= floor:
            self.state.halted_reason = (
                f"circuit: daily drawdown to ${balance_now:.2f} "
                f"(floor ${floor:.2f})")

    def maybe_reset_day(self, utc_day: str, balance_now: float) -> None:
        if utc_day != self.state.utc_day:
            self.state = PortfolioState(
                consecutive_losses=0, day_start_balance=balance_now,
                halted_reason="", utc_day=utc_day)
