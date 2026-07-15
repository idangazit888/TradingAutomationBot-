from vwap_engine import Candle
from vwap_signal import check_signal, CandleSignal, WindowEntry


def test_signal_fires_open_below_high_crosses_gap_enough():
    # vwap_prev=100, open=99.7 -> gap 0.3% >= 0.2%, high 100.1 >= 100
    c = Candle(ts=900, open=99.7, high=100.1, low=99.5, close=99.9, volume=5)
    assert check_signal(c, vwap_prev=100.0, min_gap_pct=0.002) is True


def test_no_signal_when_open_above_vwap():
    c = Candle(ts=900, open=100.5, high=101.0, low=100.2, close=100.8, volume=5)
    assert check_signal(c, vwap_prev=100.0, min_gap_pct=0.002) is False


def test_no_signal_when_high_never_reaches_vwap():
    c = Candle(ts=900, open=99.0, high=99.8, low=98.5, close=99.5, volume=5)
    assert check_signal(c, vwap_prev=100.0, min_gap_pct=0.002) is False


def test_no_signal_when_gap_too_small():
    # open 99.9 -> gap 0.1% < 0.2%
    c = Candle(ts=900, open=99.9, high=100.2, low=99.8, close=100.0, volume=5)
    assert check_signal(c, vwap_prev=100.0, min_gap_pct=0.002) is False


def test_no_signal_when_vwap_prev_none():
    c = Candle(ts=900, open=99.7, high=100.1, low=99.5, close=99.9, volume=5)
    assert check_signal(c, vwap_prev=None, min_gap_pct=0.002) is False


def test_dataclasses_construct():
    s = CandleSignal(stream_key="BTC 15m", signal_ts=900, vwap_prev=100.0,
                     candle_open=99.7, gap_pct=0.003)
    e = WindowEntry(stream_key="BTC 15m", window_start_ts=1800,
                    window_end_ts=2700, window_slug="btc-updown-15m-1800")
    assert s.stream_key == "BTC 15m" and e.window_end_ts == 2700
