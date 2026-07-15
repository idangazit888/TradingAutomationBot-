import asyncio
from vwap_config import VwapConfig
from vwap_engine import VwapEngine, Candle, CandleClose


def _candle(ts, o, h, l, c, v=10.0):
    return Candle(ts=ts, open=o, high=h, low=l, close=c, volume=v)


class FakeProvider:
    """Returns canned candles strictly newer than since."""
    def __init__(self, candles):
        self.candles = candles

    def __call__(self, interval, since):
        return [c for c in self.candles if since is None or c.ts > since]


def test_stream_key_and_interval():
    cfg = VwapConfig()
    eng = VwapEngine("BTC", "15m", "XBTUSD", cfg, FakeProvider([]),
                     now_fn=lambda: 1000.0)
    assert eng.stream_key == "BTC 15m"
    assert eng.interval_min == 15
    assert eng.window_sec == 900


def test_warm_start_reconstructs_then_poll_clears_flag(tmp_path):
    cfg = VwapConfig(candle_dir=str(tmp_path))
    seed = [_candle(0, 100, 100, 100, 100, v=10.0),
            _candle(900, 100, 100, 100, 100, v=10.0)]
    now = {"t": 1801.0}   # 900 candle is closed (900+900=1800 <= 1801)
    prov = FakeProvider(seed)
    eng = VwapEngine("BTC", "15m", "XBTUSD", cfg, prov, now_fn=lambda: now["t"])
    asyncio.run(eng.warm_start())
    assert eng.vwap_reconstructed is True
    assert eng.vwap() == 100.0

    # a live candle opens below vwap (90) at 1800, closes by 2700
    prov.candles = seed + [_candle(1800, 90, 101, 89, 95, v=10.0)]
    now["t"] = 2701.0
    closes = asyncio.run(eng.poll_once())
    assert len(closes) == 1
    cc = closes[0]
    assert isinstance(cc, CandleClose)
    assert cc.vwap_prev == 100.0
    assert cc.was_reconstructed is True
    assert eng.vwap_reconstructed is False
