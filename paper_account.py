"""
theSecondBot — persistent shared paper wallet.

One PaperAccount, shared by all four assets, simulates a real wallet: cost is
deducted at entry, payout credited at resolution, balance can never go negative,
and cross-asset contention for the shared balance is real. Persists atomically
(see T6) so a restart resumes the exact balance and still-open positions.

The check-and-deduct in `try_deduct` is synchronous with no `await` between the
read and the write, so in the bot's single-threaded asyncio loop it is atomic —
total deducted exposure can never exceed the balance.
"""

from __future__ import annotations

from vwap_engine import atomic_write_json
import json


class PaperAccount:
    def __init__(self, starting_balance: float = 300.0, persist_path: str | None = None):
        self.starting_balance = float(starting_balance)
        self.balance = float(starting_balance)
        self.realized_pnl_total = 0.0
        self.peak_balance = float(starting_balance)
        self.max_drawdown = 0.0
        self.trade_count = 0          # legs deducted
        self.win_count = 0            # winning windows
        self.loss_count = 0           # losing windows
        self.per_stream_pnl: dict[str, float] = {}
        self.open_positions: list[dict] = []
        self.persist_path = persist_path

    def _touch(self):
        """Update peak / drawdown after any balance change."""
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = self.peak_balance - self.balance
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def try_deduct(self, cost: float) -> bool:
        """Atomically deduct `cost` if affordable. Returns False (no change) if
        the shared balance can't cover it — the leg is then skipped upstream as
        reason=insufficient_balance. Balance never goes negative."""
        if cost > self.balance + 1e-12:
            return False
        self.balance -= cost
        self.trade_count += 1
        self._touch()
        self._maybe_save()
        return True

    def credit(self, payout: float):
        self.balance += payout
        self._touch()
        self._maybe_save()

    def settle_window(self, stream_key: str, total_cost: float, payout: float) -> float:
        """Credit a resolved window's payout and record its realized P&L.
        `realized_pnl = payout - total_cost`. Returns that P&L."""
        self.balance += payout            # credit without an extra save…
        self._touch()
        pnl = payout - total_cost
        self.realized_pnl_total += pnl
        self.per_stream_pnl[stream_key] = self.per_stream_pnl.get(stream_key, 0.0) + pnl
        if pnl > 0:
            self.win_count += 1
        else:
            self.loss_count += 1
        self._maybe_save()                # …single save with all updates applied
        return pnl

    # ── open positions / restart recovery ────────────────────────────────────

    def add_open_position(self, pos: dict):
        """Track a still-open window (cost already deducted, payout pending)."""
        self.open_positions.append(pos)
        self._maybe_save()

    def resolve_open_position(self, window_slug: str, outcome_up: bool) -> float | None:
        """Resolve a tracked open position: credit payout, record P&L, remove it.
        Returns the window P&L, or None if the slug isn't open.

        Positions without a "direction" field (legacy persisted state) are
        treated as UP — the pre-fix semantics, so old state can't get worse."""
        for i, pos in enumerate(self.open_positions):
            if pos["window_slug"] == window_slug:
                direction = str(pos.get("direction") or "UP").upper()
                won = outcome_up if direction == "UP" else not outcome_up
                payout = pos["total_shares"] * 1.0 if won else 0.0
                stream_key = pos.get("stream_key") or pos.get("asset")
                pnl = self.settle_window(stream_key, pos["total_cost"], payout)
                self.open_positions.pop(i)
                self._maybe_save()
                return pnl
        return None

    def void_open_position(self, window_slug: str) -> float | None:
        """Refund a position's stake WITHOUT recording a win/loss — used when the
        outcome is undeterminable (window candle never arrived). Returns the
        refunded amount, or None if the slug isn't open."""
        for i, pos in enumerate(self.open_positions):
            if pos["window_slug"] == window_slug:
                self.credit(pos["total_cost"])
                self.open_positions.pop(i)
                self._maybe_save()
                return pos["total_cost"]
        return None

    def due_positions(self, now: float) -> list[dict]:
        """Open positions whose window_end_ts has passed — ready to resolve."""
        return [p for p in self.open_positions if p["window_end_ts"] <= now]

    # ── persistence ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "starting_balance": self.starting_balance,
            "balance": self.balance,
            "realized_pnl_total": self.realized_pnl_total,
            "peak_balance": self.peak_balance,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "per_stream_pnl": self.per_stream_pnl,
            "open_positions": self.open_positions,
        }

    def save(self):
        if self.persist_path:
            atomic_write_json(self.persist_path, self.to_dict())

    def _maybe_save(self):
        if self.persist_path:
            self.save()

    @classmethod
    def load(cls, path: str, starting_balance: float = 300.0) -> "PaperAccount":
        """Load a persisted wallet (resumes exact balance + open positions). If
        the file is missing, start fresh. Unlike the candle store, the wallet is
        NOT date-scoped — it always resumes, never resets mid-run."""
        a = cls(starting_balance=starting_balance, persist_path=path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (FileNotFoundError, ValueError):
            return a
        a.starting_balance = float(d.get("starting_balance", starting_balance))
        a.balance = float(d.get("balance", starting_balance))
        a.realized_pnl_total = float(d.get("realized_pnl_total", 0.0))
        a.peak_balance = float(d.get("peak_balance", a.balance))
        a.max_drawdown = float(d.get("max_drawdown", 0.0))
        a.trade_count = int(d.get("trade_count", 0))
        a.win_count = int(d.get("win_count", 0))
        a.loss_count = int(d.get("loss_count", 0))
        a.per_stream_pnl = dict(d.get("per_stream_pnl", d.get("per_asset_pnl", {})))
        a.open_positions = list(d.get("open_positions", []))
        return a

    def stats(self) -> dict:
        """Per-stream account snapshot. Deliberately NO single blended win rate."""
        pnl_pct = (self.realized_pnl_total / self.starting_balance * 100.0
                   if self.starting_balance else 0.0)
        return {
            "balance": round(self.balance, 4),
            "realized_pnl_total": round(self.realized_pnl_total, 4),
            "pnl_pct": round(pnl_pct, 3),
            "peak_balance": round(self.peak_balance, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "per_stream_pnl": {k: round(v, 4) for k, v in self.per_stream_pnl.items()},
            "open_positions": len(self.open_positions),
        }
