"""T2 — Kraken OHLC parsing: read the non-`last` result key, build Candles."""
from vwap_engine import Candle, parse_ohlc, select_since


def _result(pair_key, rows, last):
    # Kraken OHLC row: [time, open, high, low, close, vwap, volume, count]
    return {"error": [], "result": {pair_key: rows, "last": last}}


def test_parse_reads_non_last_key_btc():
    rows = [[1782518400, "60000.0", "60100.0", "59900.0", "60050.0", "60010.0", "1.5", 12]]
    data = _result("XXBTZUSD", rows, 1782518340)
    candles = parse_ohlc(data)
    assert len(candles) == 1
    c = candles[0]
    assert isinstance(c, Candle)
    assert c.ts == 1782518400
    assert c.open == 60000.0 and c.high == 60100.0
    assert c.low == 59900.0 and c.close == 60050.0
    assert c.volume == 1.5
    # ohlc4 = (o+h+l+c)/4
    assert c.ohlc4 == (60000.0 + 60100.0 + 59900.0 + 60050.0) / 4


def test_parse_works_for_all_pair_key_shapes():
    rows = [[1, "1", "2", "0", "1", "1", "10", 3]]
    for key in ("XXBTZUSD", "SOLUSD", "XETHZUSD", "XXRPZUSD"):
        candles = parse_ohlc(_result(key, rows, 0))
        assert len(candles) == 1 and candles[0].volume == 10.0


def test_parse_raises_on_kraken_error():
    import pytest
    with pytest.raises(ValueError):
        parse_ohlc({"error": ["EQuery:Unknown asset pair"], "result": {}})


def test_select_since_filters_strictly_newer():
    rows = [
        [100, "1", "1", "1", "1", "1", "1", 1],
        [160, "1", "1", "1", "1", "1", "1", 1],
        [220, "1", "1", "1", "1", "1", "1", 1],
    ]
    candles = parse_ohlc(_result("SOLUSD", rows, 220))
    newer = select_since(candles, 160)
    assert [c.ts for c in newer] == [220]
