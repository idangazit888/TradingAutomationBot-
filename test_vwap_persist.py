"""T4 — SessionVwap persistence: atomic write, resume sums, staleness discard."""
import json
from vwap_engine import Candle, SessionVwap, save_session, load_session

SESSION = 1782518400


def c(ts, price, vol):
    return Candle(ts=ts, open=price, high=price, low=price, close=price, volume=vol)


def test_save_then_load_resumes_exact_sums(tmp_path):
    s = SessionVwap()
    s.add_candle(c(SESSION, 100.0, 3.0))
    s.add_candle(c(SESSION + 60, 200.0, 1.0))
    p = tmp_path / "candles_BTC_2026-06-27.json"
    save_session(str(p), s, "2026-06-27")

    restored = load_session(str(p), "2026-06-27")
    assert restored is not None
    assert restored.sum_pv == s.sum_pv
    assert restored.sum_v == s.sum_v
    assert restored.last_ts == s.last_ts
    assert restored.value() == s.value()


def test_stale_file_from_prior_day_is_discarded(tmp_path):
    s = SessionVwap()
    s.add_candle(c(SESSION, 100.0, 3.0))
    p = tmp_path / "candles_BTC_2026-06-26.json"
    save_session(str(p), s, "2026-06-26")
    # today is a different UTC date -> must discard
    assert load_session(str(p), "2026-06-27") is None


def test_missing_file_returns_none(tmp_path):
    assert load_session(str(tmp_path / "nope.json"), "2026-06-27") is None


def test_write_is_atomic_no_temp_left_behind(tmp_path):
    s = SessionVwap()
    s.add_candle(c(SESSION, 100.0, 1.0))
    p = tmp_path / "candles_BTC_2026-06-27.json"
    save_session(str(p), s, "2026-06-27")
    # only the final file exists, no leftover .tmp
    names = [f.name for f in tmp_path.iterdir()]
    assert names == ["candles_BTC_2026-06-27.json"]
    # and it is valid JSON with the expected keys
    data = json.loads(p.read_text())
    assert set(data) >= {"utc_date", "sum_pv", "sum_v", "count", "last_ts"}
