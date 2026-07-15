"""T1 — VwapConfig defaults must match the approved spec exactly."""
from vwap_config import VwapConfig


def test_defaults_match_spec():
    c = VwapConfig()
    assert c.dry_run is True
    assert c.paper_starting_balance == 300.00
    assert c.position_size == 10.00
    assert c.max_up_price == 0.60
    assert c.fee_rate == 0.015
    assert c.daily_loss_limit == 50.0
    assert c.max_trades_per_hour == 4
    assert c.min_shares == 5
    assert c.kraken_ohlc_poll_sec == 15


def test_timeframes_and_gap():
    c = VwapConfig()
    assert c.timeframes == {"15m": (15, 900), "5m": (5, 300)}
    assert c.min_gap_pct == 0.002
    assert c.resolution_source == "kraken_close_proxy"
    assert "{timeframe}" in c.candle_file_template
    assert c.dry_run is True


def test_dip_knobs_removed():
    c = VwapConfig()
    for gone in ("max_entry_minute", "dip_arm_level", "dip_trigger_level",
                 "dip_extra_slippage", "window_size_sec"):
        assert not hasattr(c, gone), f"{gone} should be removed"


def test_asset_pair_map():
    c = VwapConfig()
    assert c.assets == {
        "BTC": "XBTUSD",
        "SOL": "SOLUSD",
        "ETH": "ETHUSD",
        "XRP": "XRPUSD",
    }
    # slug prefixes are lowercase asset names
    assert c.slug_prefix("BTC") == "btc"
    assert c.slug_prefix("XRP") == "xrp"


def test_paths_present():
    c = VwapConfig()
    assert c.csv_path.endswith("vwap_trades.csv")
    assert c.account_path.endswith("paper_account.json")
    # candle file template includes asset + timeframe + date placeholders
    assert "{asset}" in c.candle_file_template
    assert "{timeframe}" in c.candle_file_template
    assert "{date}" in c.candle_file_template
