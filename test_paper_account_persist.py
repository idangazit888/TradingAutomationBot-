"""T6 — PaperAccount persistence + restart recovery of open positions."""
from paper_account import PaperAccount


def _pos(slug="btc-updown-15m-900", end=2000):
    return {
        "asset": "BTC", "stream_key": "BTC 15m", "window_slug": slug,
        "window_start_ts": end - 900, "window_end_ts": end,
        "window_open": 60000.0, "total_shares": 16.0, "total_cost": 9.6,
        "legs": [{"type": "vwap_cross", "shares": 16.0, "fill": 0.60}],
    }


def test_save_load_resumes_exact_state(tmp_path):
    p = str(tmp_path / "paper_account.json")
    a = PaperAccount(starting_balance=300.0, persist_path=p)
    a.try_deduct(9.6)
    a.settle_window("BTC 15m", 9.6, 16.0)   # +6.4
    a.save()

    b = PaperAccount.load(p, starting_balance=300.0)
    assert b.balance == a.balance
    assert b.realized_pnl_total == a.realized_pnl_total
    assert b.per_stream_pnl == a.per_stream_pnl
    assert b.win_count == 1
    assert b.persist_path == p   # keeps persisting after reload


def test_persists_after_every_change(tmp_path):
    p = str(tmp_path / "paper_account.json")
    a = PaperAccount(starting_balance=300.0, persist_path=p)
    a.try_deduct(10.0)   # auto-saves
    reloaded = PaperAccount.load(p, starting_balance=300.0)
    assert reloaded.balance == 290.0


def test_open_position_survives_restart_and_credits(tmp_path):
    p = str(tmp_path / "paper_account.json")
    a = PaperAccount(starting_balance=300.0, persist_path=p)
    a.try_deduct(9.6)
    a.add_open_position(_pos())
    a.save()

    # restart: position is reloaded, still pending
    b = PaperAccount.load(p, starting_balance=300.0)
    assert len(b.open_positions) == 1
    assert b.balance == 290.4

    # resolve Up -> 16 shares pay $1 each -> +16, removed from open list
    pnl = b.resolve_open_position("btc-updown-15m-900", outcome_up=True)
    assert pnl == 16.0 - 9.6
    assert b.balance == 290.4 + 16.0
    assert b.open_positions == []


def test_due_positions_returns_windows_past_end(tmp_path):
    a = PaperAccount(starting_balance=300.0)
    a.add_open_position(_pos(slug="a", end=1000))
    a.add_open_position(_pos(slug="b", end=5000))
    due = a.due_positions(now=2000)
    assert [d["window_slug"] for d in due] == ["a"]


def test_load_missing_file_starts_fresh(tmp_path):
    p = str(tmp_path / "nope.json")
    a = PaperAccount.load(p, starting_balance=300.0)
    assert a.balance == 300.0
    assert a.persist_path == p
