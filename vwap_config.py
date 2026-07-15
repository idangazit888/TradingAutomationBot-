"""
theSecondBot — VWAP strategy configuration.

Every tunable for the session-anchored VWAP cross-up multi-asset paper trader.
Defaults are the approved spec values
(docs/superpowers/specs/2026-06-27-vwap-cross-multiasset-paper-design.md).

`dry_run=True` is a hard default and MUST stay True until the operator flips it.
"""

from dataclasses import dataclass, field


def _default_timeframes() -> dict:
    # label -> (kraken_interval_minutes, window_seconds)
    return {"15m": (15, 900), "5m": (5, 300)}


def _default_assets() -> dict:
    # asset -> Kraken OHLC pair. Kraken's result key varies per pair
    # (XXBTZUSD / SOLUSD / XETHZUSD / XXRPZUSD); the engine reads "the key
    # that isn't `last`", so only the request pair matters here.
    return {
        "BTC": "XBTUSD",
        "SOL": "SOLUSD",
        "ETH": "ETHUSD",
        "XRP": "XRPUSD",
    }


@dataclass
class VwapConfig:
    # ── mode ────────────────────────────────────────────────────────────────
    dry_run: bool = True               # PAPER. Never flipped without operator action.

    # ── account ─────────────────────────────────────────────────────────────
    paper_starting_balance: float = 300.00   # shared wallet across all 4 assets
    position_size: float = 10.00             # fixed stake per primary/standalone leg

    # ── entry guards ─────────────────────────────────────────────────────────
    max_up_price: float = 0.60         # skip entry if live UP ask > this (log-only)

    # ── fees ─────────────────────────────────────────────────────────────────
    fee_rate: float = 0.015            # per-leg notional fee (see spec §7.3 caveat)

    # ── risk (per-stream) ────────────────────────────────────────────────────
    daily_loss_limit: float = 50.0     # per-stream; LOG-ONLY in paper (would_halt)
    resolve_grace_sec: int = 120       # wait this long past window end for the candle;
                                       # then VOID + refund (never lock capital forever)
    max_trades_per_hour: int = 4       # per-stream cap on entries

    # ── signal ───────────────────────────────────────────────────────────────
    min_gap_pct: float = 0.002         # relative gap (vwap_prev - open)/vwap_prev

    # ── market / venue constants ─────────────────────────────────────────────
    timeframes: dict = field(default_factory=_default_timeframes)
    min_shares: int = 5                # Polymarket orderMinSize
    tick: float = 0.01

    # ── data source ──────────────────────────────────────────────────────────
    assets: dict = field(default_factory=_default_assets)
    kraken_ohlc_poll_sec: int = 15
    resolution_source: str = "kraken_close_proxy"

    # ── intra-block fill-price collector (LOG-ONLY observer) ─────────────────
    collect_intrablock: bool = True    # record real BTC 5m UP asks at each 1m cross
    intrablock_csv_path: str = "intrablock_trades.csv"
    intrablock_poll_sec: int = 45      # own (slower) Kraken 1m cadence — 1m candles
                                       # close every 60s, so 15s wasted requests + rate limits

    # ── output paths ─────────────────────────────────────────────────────────
    csv_path: str = "vwap_trades.csv"
    account_path: str = "paper_account.json"
    candle_dir: str = "."
    candle_file_template: str = "candles_{asset}_{timeframe}_{date}.json"

    def slug_prefix(self, asset: str) -> str:
        """Polymarket slug prefix for an asset (lowercase)."""
        return asset.lower()
