from ud_confirm import PendingConfirm, still_holds, elapsed_ge


def test_still_holds_up():
    assert still_holds("UP", 101.0, 100.0) is True
    assert still_holds("UP", 99.0, 100.0) is False


def test_still_holds_down():
    assert still_holds("DOWN", 99.0, 100.0) is True
    assert still_holds("DOWN", 101.0, 100.0) is False


def test_elapsed_ge():
    assert elapsed_ge(now=145.0, trigger_ts=100.0, seconds=45) is True
    assert elapsed_ge(now=144.9, trigger_ts=100.0, seconds=45) is False


def test_pending_confirm_fields():
    p = PendingConfirm(asset="BTC", window_slug="btc-updown-5m-100", direction="UP",
                       vwap_ref=100.0, trigger_ts=105.0, m=2, tier="STRONG",
                       window_start_ts=100.0, window_end_ts=400.0, window_open=99.0)
    assert p.asset == "BTC" and p.direction == "UP" and p.gap_bps == 0.0
