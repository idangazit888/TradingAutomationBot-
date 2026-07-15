from vwap_engine import Candle
import ud_indicators as ind

def _c(ts, o, h, l, cl, v):
    return Candle(ts=ts, open=o, high=h, low=l, close=cl, volume=v)

def test_typical_price_and_session_vwap():
    cs = [_c(0, 10, 12, 8, 10, 100), _c(60, 10, 14, 10, 12, 100)]
    assert ind.typical_price(cs[0]) == (12 + 8 + 10) / 3
    # tp0=10, tp1=12; vwap=(10*100+12*100)/200 = 11
    assert ind.session_vwap(cs) == 11.0

def test_session_vwap_zero_volume_is_none():
    assert ind.session_vwap([_c(0, 10, 10, 10, 10, 0)]) is None

def test_vol_ma30_lagged():
    cs = [_c(i*60, 10, 10, 10, 10, 100 + i) for i in range(32)]
    # ma30 at index 31 = mean(volumes[1..30]) = mean(101..130)
    expected = sum(100 + i for i in range(1, 31)) / 30
    assert abs(ind.vol_ma30(cs, 31) - expected) < 1e-9

def test_vol_ratio():
    cs = [_c(i*60, 10, 10, 10, 10, 100) for i in range(31)]
    cs.append(_c(31*60, 10, 10, 10, 10, 150))
    # ma30 over indices 1..30 (all 100) = 100; ratio = 150/100 = 1.5
    assert abs(ind.vol_ratio(cs, 31) - 1.5) < 1e-9

def test_chop_counts_vwap_crosses():
    # closes alternate around a flat vwap=10 across the lookback → 3 crosses in 4 candles
    closes = [11, 9, 11, 9]
    cs = [_c(i*60, 10, 12, 8, closes[i], 100) for i in range(4)]
    vwaps = [10.0, 10.0, 10.0, 10.0]
    assert ind.chop(cs, vwaps, index=3, lookback=4) == 3

def test_gap_bps():
    assert abs(ind.gap_bps(100.0, 99.5) - 50.0) < 1e-9   # 0.5% = 50 bps
