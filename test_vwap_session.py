"""T3 — SessionVwap accumulation (volume-weighted ohlc4)."""
from vwap_engine import Candle, SessionVwap

SESSION = 1782518400  # 2026-06-27 00:00 UTC (a multiple of 900)


def c(ts, price, vol, spread=10.0):
    """Convenience: a candle whose ohlc4 == price."""
    return Candle(ts=ts, open=price, high=price + spread, low=price - spread,
                  close=price, volume=vol)


def test_add_and_value_is_volume_weighted():
    s = SessionVwap()
    s.add_candle(c(SESSION, 100.0, 2.0))      # pv = 200
    s.add_candle(c(SESSION + 60, 110.0, 8.0)) # pv = 880
    # vwap = (200 + 880) / (2 + 8) = 1080/10 = 108
    assert s.value() == 108.0
    assert s.last_ts == SESSION + 60


def test_reset_zeroes():
    s = SessionVwap()
    s.add_candle(c(SESSION, 100.0, 5.0))
    s.reset()
    assert s.value() is None
    assert s.sum_v == 0.0 and s.sum_pv == 0.0
