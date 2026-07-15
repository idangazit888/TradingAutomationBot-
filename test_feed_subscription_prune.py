"""Regression test for the subscription bloat that caused the WS reconnect storm
(3064 tokens → 830 reconnects → books unstable → 0 fills).

`subscribed_tokens` must stay bounded to the ACTIVE registry, not accumulate every
market ever seen, so the resubscribe on reconnect stays small.
"""
from feeds import PolymarketBookFeed, CurrentMarketsRegistry


def _feed(reg):
    return PolymarketBookFeed(lambda u: None, reg, None)


def test_active_token_set_from_registry():
    reg = CurrentMarketsRegistry()
    reg.register_window("m1", 0, 300, "u1", "d1", 1.0)
    reg.register_window("m2", 0, 300, "u2", "d2", 1.0)
    assert _feed(reg)._active_token_set() == {"u1", "d1", "u2", "d2"}


def test_prune_subscriptions_keeps_only_active_registry_tokens():
    reg = CurrentMarketsRegistry()
    reg.register_window("m1", 0, 300, "up1", "down1", 1.0, asset="BTC", timeframe="5m")
    feed = _feed(reg)
    feed.subscribed_tokens = {"up1", "down1", "stale_a", "stale_b", "stale_c"}
    feed._prune_subscriptions()
    assert feed.subscribed_tokens == {"up1", "down1"}


def test_missing_active_tokens_detects_registered_but_never_subscribed_market():
    # A market can be registered (so discovery never retries it) but its subscribe
    # call can be lost to a reconnect race — this must be detectable and healable.
    reg = CurrentMarketsRegistry()
    reg.register_window("m1", 0, 900, "up1", "down1", 1.0)
    feed = _feed(reg)
    feed.subscribed_tokens = set()          # subscribe for m1 never went out
    assert feed._missing_active_tokens() == {"up1", "down1"}


def test_missing_active_tokens_empty_when_fully_subscribed():
    reg = CurrentMarketsRegistry()
    reg.register_window("m1", 0, 900, "up1", "down1", 1.0)
    feed = _feed(reg)
    feed.subscribed_tokens = {"up1", "down1"}
    assert feed._missing_active_tokens() == set()


def test_prune_after_cleanup_drops_expired_market_tokens():
    reg = CurrentMarketsRegistry()
    reg.register_window("old", 0, 300, "up_old", "down_old", 1.0)
    reg.register_window("cur", 100_000, 100_300, "up_cur", "down_cur", 1.0)
    feed = _feed(reg)
    feed.subscribed_tokens = {"up_old", "down_old", "up_cur", "down_cur"}
    reg.cleanup_old(current_ts=100_000 + 3601)   # 'old' (end 300) is >1h stale → removed
    feed._prune_subscriptions()
    assert feed.subscribed_tokens == {"up_cur", "down_cur"}
