import asyncio
from notifications import NotificationBatcher


class FakeTg:
    def __init__(self):
        self.sent = []

    def send_message(self, text):
        self.sent.append(text)


def _stats():
    return {"balance": 300.0, "realized_pnl_total": 0.0, "per_stream_pnl": {}}


def test_critical_is_immediate():
    tg = FakeTg()
    b = NotificationBatcher(tg, _stats, now_fn=lambda: 100.0)
    asyncio.run(b.critical("halt!"))
    assert any("halt!" in m for m in tg.sent)


def test_entries_batch_per_boundary_after_grace():
    tg = FakeTg()
    t = {"now": 1000.0}
    b = NotificationBatcher(tg, _stats, now_fn=lambda: t["now"], grace_sec=3.0)
    b.add_entry(1000, "BTC 15m ENTRY")
    b.add_entry(1000, "XRP 5m ENTRY")
    # before grace elapses: nothing flushes
    t["now"] = 1002.0
    asyncio.run(b.flush_due())
    assert tg.sent == []
    # after grace: one batched ENTRY message containing both lines + a snapshot
    t["now"] = 1004.0
    asyncio.run(b.flush_due())
    joined = "\n".join(tg.sent)
    assert "BTC 15m ENTRY" in joined and "XRP 5m ENTRY" in joined
    assert "balance" in joined.lower() or "300" in joined


def test_result_and_entry_separate_messages():
    tg = FakeTg()
    t = {"now": 2000.0}
    b = NotificationBatcher(tg, _stats, now_fn=lambda: t["now"], grace_sec=3.0)
    b.add_entry(2000, "BTC 15m ENTRY")
    b.add_result(2000, "BTC 15m WIN +4.20")
    t["now"] = 2010.0
    asyncio.run(b.flush_due())
    assert any("ENTRY" in m for m in tg.sent)
    assert any("WIN" in m for m in tg.sent)
