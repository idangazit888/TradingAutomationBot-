"""theSecondBot — pure pricing gate: effective size + max price + edge rule.

Two entry-price regimes, selected per asset via AssetConfig.taker_threshold:
  - taker threshold (Option B, BTC): a time-decaying flat cap per minute —
    buy at market as soon as the ask is cheap enough for how far into the
    window we are. Ignores the prob/edge_margin/tier caps entirely.
  - classic edge gate (ETH and any uncalibrated asset): the original
    min(minute_cap, tier_cap, true_prob - edge_margin - fee_buffer).

Also carries the real Polymarket taker fee: every trade this bot makes is a
taker (market buy/sell, crossing the spread) — makers pay nothing, takers pay
`fee_rate * p * (1-p)` per share (Crypto category rate 0.07, per Polymarket's
published fee docs). Peaks at p=0.50 (~1.75c/share) and shrinks toward the
extremes. Charged on entries, add-ons, kill-line exits, and flips; NOT charged
on resolution/redemption (that's a settlement, not a trade).
"""
from __future__ import annotations

from dataclasses import dataclass

_TIER_FULL = {"ELITE", "STRONG"}


def taker_fee_per_share(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1.0 - price)


def taker_fee(shares: float, price: float, fee_rate: float) -> float:
    return shares * taker_fee_per_share(price, fee_rate)


def _rank(mode: str) -> int:
    return {"skip": 0, "half": 1, "full": 2}[mode]


def size_mode(cfg, direction: str, m: int, tier: str, utc_hour: int) -> str | None:
    if tier == "SKIP":
        return None
    hour = cfg.hour_mode(utc_hour)
    if hour == "skip":
        return None
    tier_size = "full" if tier in _TIER_FULL else "half"
    minute = cfg.minute_size(direction, m)
    worst = min(_rank(tier_size), _rank(minute), _rank(hour))
    if worst == 0:  # any dimension resolving to "skip" (e.g. minute size) -> None
        return None
    return {1: "half", 2: "full"}[worst]


def max_price(cfg, direction: str, m: int, tier: str, fee_buffer: float) -> float:
    if cfg.entry_max is not None:
        return cfg.entry_max - fee_buffer
    if cfg.taker_threshold:
        return cfg.taker_threshold[m] - fee_buffer
    minute_cap = cfg.price_cap(direction, m)
    tier_cap = cfg.elite_cap if tier == "ELITE" else minute_cap
    edge_price = cfg.p(direction, m) - cfg.edge_margin - fee_buffer
    return min(minute_cap, tier_cap, edge_price)


@dataclass
class PriceDecision:
    allowed: bool
    size: str | None
    max_price: float
    true_prob: float
    reason: str


def evaluate(cfg, direction: str, m: int, tier: str, ask: float,
             utc_hour: int, fee_buffer: float) -> PriceDecision:
    size = size_mode(cfg, direction, m, tier, utc_hour)
    mp = max_price(cfg, direction, m, tier, fee_buffer)
    tp = cfg.p(direction, m)
    if size is None:
        return PriceDecision(False, None, mp, tp, "tier/hour skip")
    if cfg.entry_min is not None and ask < cfg.entry_min:
        return PriceDecision(False, size, mp, tp, f"ask {ask:.3f} < min {cfg.entry_min:.3f}")
    if ask > mp:
        return PriceDecision(False, size, mp, tp, f"ask {ask:.3f} > max {mp:.3f}")
    return PriceDecision(True, size, mp, tp, "ok")
