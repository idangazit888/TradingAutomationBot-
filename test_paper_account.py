"""T5 — PaperAccount: deduct/credit, insufficient-balance skip, contention, stats."""
from paper_account import PaperAccount


def test_starts_at_configured_balance():
    a = PaperAccount(starting_balance=300.0)
    assert a.balance == 300.0
    assert a.realized_pnl_total == 0.0


def test_try_deduct_success_and_insufficient():
    a = PaperAccount(starting_balance=20.0)
    assert a.try_deduct(15.0) is True
    assert a.balance == 5.0
    # not enough for another 15 -> refused, balance unchanged, never negative
    assert a.try_deduct(15.0) is False
    assert a.balance == 5.0


def test_contention_two_legs_cannot_exceed_balance():
    a = PaperAccount(starting_balance=12.0)
    assert a.try_deduct(10.0) is True   # asset A leg
    assert a.try_deduct(10.0) is False  # asset B leg blocked by shared balance
    assert a.balance == 2.0


def test_settle_window_credits_and_records_pnl():
    a = PaperAccount(starting_balance=100.0)
    a.try_deduct(6.0)                     # cost for a window (e.g. 10sh @0.60)
    pnl = a.settle_window("BTC 15m", total_cost=6.0, payout=10.0)  # Up: 10 shares pay $1
    assert pnl == 4.0
    assert a.balance == 100.0 - 6.0 + 10.0   # 104
    assert a.realized_pnl_total == 4.0
    assert a.per_stream_pnl["BTC 15m"] == 4.0
    assert a.win_count == 1 and a.loss_count == 0


def test_settle_window_loss():
    a = PaperAccount(starting_balance=100.0)
    a.try_deduct(6.0)
    pnl = a.settle_window("SOL 5m", total_cost=6.0, payout=0.0)  # Down: $0
    assert pnl == -6.0
    assert a.balance == 94.0
    assert a.loss_count == 1 and a.win_count == 0
    assert a.per_stream_pnl["SOL 5m"] == -6.0


def test_settle_window_streams_isolated():
    a = PaperAccount(starting_balance=300.0)
    a.try_deduct(10.0); a.settle_window("BTC 15m", 10.0, 14.0)
    a.try_deduct(10.0); a.settle_window("BTC 5m", 10.0, 6.0)
    assert a.per_stream_pnl["BTC 15m"] == 4.0
    assert a.per_stream_pnl["BTC 5m"] == -4.0


def test_peak_and_max_drawdown_tracked():
    a = PaperAccount(starting_balance=100.0)
    a.try_deduct(6.0)
    a.settle_window("BTC 15m", 6.0, 20.0)   # balance 114, peak 114
    a.try_deduct(6.0)
    a.settle_window("BTC 15m", 6.0, 0.0)    # balance 108
    assert a.peak_balance == 114.0
    # drawdown from peak: 114 - 108 = 6
    assert a.max_drawdown == 6.0


def test_stats_is_per_stream_never_blended():
    a = PaperAccount(starting_balance=100.0)
    s = a.stats()
    assert "per_stream_pnl" in s
    assert "balance" in s and "realized_pnl_total" in s
    assert "pnl_pct" in s and "peak_balance" in s and "max_drawdown" in s
    # no single blended win-rate key
    assert "win_rate" not in s


# ── resolve_open_position direction handling (2026-07-15 DOWN-inversion fix) ──
# Bug: payout was `total_shares if outcome_up else 0` regardless of the
# position's direction — every DOWN trade settled backwards (GAP/STACK arms
# resolved exclusively through this path; 34/34 DOWN trades Jul 12–15 inverted).

def _open_pos(direction):
    return {"stream_key": "BTC", "asset": "BTC", "window_slug": "w1",
            "direction": direction, "window_start_ts": 0.0,
            "window_end_ts": 300.0, "window_open": 100.0,
            "total_shares": 10.0, "total_cost": 7.0}


def test_resolve_open_position_down_win_pays():
    a = PaperAccount(starting_balance=100.0)
    a.try_deduct(7.0)
    a.add_open_position(_open_pos("DOWN"))
    pnl = a.resolve_open_position("w1", outcome_up=False)   # DOWN won
    assert pnl == 3.0                                       # 10sh*$1 − $7 cost
    assert a.balance == 103.0


def test_resolve_open_position_down_loss_pays_nothing():
    a = PaperAccount(starting_balance=100.0)
    a.try_deduct(7.0)
    a.add_open_position(_open_pos("DOWN"))
    pnl = a.resolve_open_position("w1", outcome_up=True)    # DOWN lost
    assert pnl == -7.0
    assert a.balance == 93.0


def test_resolve_open_position_up_unchanged():
    a = PaperAccount(starting_balance=100.0)
    a.try_deduct(7.0)
    a.add_open_position(_open_pos("UP"))
    assert a.resolve_open_position("w1", outcome_up=True) == 3.0
    a.try_deduct(7.0)
    a.add_open_position(_open_pos("UP"))
    assert a.resolve_open_position("w1", outcome_up=False) == -7.0


def test_resolve_open_position_legacy_no_direction_treated_as_up():
    a = PaperAccount(starting_balance=100.0)
    a.try_deduct(7.0)
    pos = _open_pos("UP"); del pos["direction"]             # legacy persisted state
    a.add_open_position(pos)
    assert a.resolve_open_position("w1", outcome_up=True) == 3.0
