"""theSecondBot — pure position lifecycle predicates + resolution P&L."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    direction: str
    window_open: float
    shares: float
    total_cost: float
    added: bool = False
    exited: bool = False
    exit_proceeds: float = 0.0
    flip_direction: str | None = None
    flip_shares: float = 0.0
    entry_size: str = "half"


def right_side(direction: str, price: float, window_open: float) -> bool:
    if direction == "UP":
        return price >= window_open
    return price <= window_open


def _recrossed_against(direction: str, candle_close: float, vwap_prev: float) -> bool:
    # VWAP recross against the position: UP position sees close back below VWAP.
    if direction == "UP":
        return candle_close < vwap_prev
    return candle_close > vwap_prev


def should_add(pos: Position, cfg, candle_close: float, vwap_prev: float,
               own_ask: float) -> bool:
    if pos.added or pos.exited:
        return False
    if not right_side(pos.direction, candle_close, pos.window_open):
        return False
    if not _recrossed_against(pos.direction, candle_close, vwap_prev):
        return False
    return own_ask <= cfg.addon_cap


def is_kill(pos: Position, candle_close: float) -> bool:
    return not right_side(pos.direction, candle_close, pos.window_open)


def exit_decision(pos: Position, own_bid: float, cfg) -> str:
    return "sell" if own_bid >= cfg.exit_fair else "hold"


def flip_decision(pos: Position, opp_ask: float, cfg) -> bool:
    return opp_ask <= cfg.flip_cap


def resolve_pnl(pos: Position, outcome_up: bool) -> tuple[float, float]:
    winning = "UP" if outcome_up else "DOWN"
    payout = pos.exit_proceeds
    if not pos.exited and pos.direction == winning:
        payout += pos.shares * 1.0
    if pos.flip_direction == winning:
        payout += pos.flip_shares * 1.0
    return pos.total_cost, payout
