"""T11 — multi-asset slug building + registry ask-ladder/asset retention."""
from feeds import updown_slug, slot_starts, CurrentMarketsRegistry


def test_updown_slug_15m():
    assert updown_slug("btc", "15m", 1782518400) == "btc-updown-15m-1782518400"
    assert updown_slug("xrp", "15m", 1782519300) == "xrp-updown-15m-1782519300"


def test_slot_starts_aligns_to_window_size():
    # now just after a 900 boundary -> first slot is that boundary
    slots = slot_starts(now=1782518460, window_size=900, count=3)
    assert slots == [1782518400, 1782519300, 1782520200]


def test_registry_stores_asset_and_ask_ladder():
    reg = CurrentMarketsRegistry()
    reg.register_window("cond1", 900, 1800, "uptok", "downtok", 60000.0, asset="BTC")
    assert reg.markets["cond1"]["asset"] == "BTC"
    ladder = [(0.60, 100.0), (0.61, 50.0)]
    reg.update_prices("cond1", "UP", 0.59, 0.60, 123.0, ask_ladder=ladder)
    m = reg.markets["cond1"]
    assert m["current_up_ask"] == 0.60
    assert m["up_ask_ladder"] == ladder


def test_register_window_defaults_asset_none_for_back_compat():
    reg = CurrentMarketsRegistry()
    reg.register_window("c", 900, 1800, "u", "d", 100.0)
    assert reg.markets["c"]["asset"] is None
    # old update_prices signature (no ladder) still works
    reg.update_prices("c", "DOWN", 0.4, 0.45, 9.0)
    assert reg.markets["c"]["current_down_ask"] == 0.45


def test_timeframe_configs_drive_discovery():
    from feeds import PolymarketBookFeed
    feed = PolymarketBookFeed(
        on_book_update=lambda u: None, registry=CurrentMarketsRegistry(),
        binance_feed=None, asset_prefixes={"BTC": "btc", "XRP": "xrp"},
        timeframe_configs=[("15m", 900), ("5m", 300)],
    )
    assert ("15m", 900) in feed.timeframe_configs
    assert ("5m", 300) in feed.timeframe_configs
    slots15 = slot_starts(1000.0, 900, 5)
    assert updown_slug("btc", "15m", slots15[0]).startswith("btc-updown-15m-")


def test_register_window_stores_timeframe():
    reg = CurrentMarketsRegistry()
    reg.register_window("c", 900, 1800, "u", "d", 100.0, asset="BTC", timeframe="15m")
    assert reg.markets["c"]["timeframe"] == "15m"


def test_parse_book_message_extracts_ask_ladder():
    from feeds import PolymarketBookFeed
    feed = PolymarketBookFeed(on_book_update=lambda u: None,
                              registry=CurrentMarketsRegistry(), binance_feed=None)
    feed.token_to_market["tok123"] = ("cond1", "UP")
    msg = {
        "asset_id": "tok123",
        "bids": [{"price": "0.58", "size": "100"}],
        "asks": [{"price": "0.61", "size": "40"}, {"price": "0.60", "size": "25"}],
        "timestamp": "1782518400000",
    }
    upd = feed._parse_book_message(msg)
    assert upd is not None
    assert upd.best_ask == 0.60
    # ladder ascending by price
    assert upd.ask_ladder == [(0.60, 25.0), (0.61, 40.0)]
