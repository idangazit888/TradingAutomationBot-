"""Watchdog: a zombie ws (TCP alive, no data) must be force-closed so the
existing reconnect path fires. Liveness is defined by DATA, not connection."""
import asyncio
import time

from feeds import PolymarketBookFeed, CurrentMarketsRegistry


class FakeWs:
    def __init__(self):
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


def _feed(stale_after_sec, watchdog_poll_sec):
    return PolymarketBookFeed(
        on_book_update=lambda u: None,
        registry=CurrentMarketsRegistry(),
        binance_feed=None,
        stale_after_sec=stale_after_sec,
        watchdog_poll_sec=watchdog_poll_sec,
    )


def test_watchdog_closes_stalled_ws_once():
    async def scenario():
        feed = _feed(stale_after_sec=0.05, watchdog_poll_sec=0.01)
        feed.last_message_ts = time.time()   # fresh connect
        ws = FakeWs()
        task = asyncio.create_task(feed._watchdog_loop(ws))
        await asyncio.sleep(0.2)             # silence > stale_after_sec
        assert ws.close_calls == 1           # closed exactly once, loop exited
        assert task.done()
    asyncio.run(scenario())


def test_watchdog_keeps_live_ws_open():
    async def scenario():
        feed = _feed(stale_after_sec=0.08, watchdog_poll_sec=0.01)
        ws = FakeWs()
        task = asyncio.create_task(feed._watchdog_loop(ws))
        for _ in range(20):                  # keep data flowing for 0.2s
            feed.last_message_ts = time.time()
            await asyncio.sleep(0.01)
        assert ws.close_calls == 0
        task.cancel()
    asyncio.run(scenario())


def test_watchdog_defaults():
    feed = PolymarketBookFeed(on_book_update=lambda u: None,
                              registry=CurrentMarketsRegistry(),
                              binance_feed=None)
    assert feed.stale_after_sec == 90.0
    assert feed.watchdog_poll_sec == 10.0
    assert feed.last_message_ts > 0
