# BinanceNot4VPS — the Binance price-feed strategy (frozen backup)

> **Why this file exists.** The bot's BTC price source was switched from
> **Binance WebSocket** to **Kraken REST polling** because Binance's WebSocket
> (and REST) is **geo-blocked (HTTP 451)** on some hosts — notably DigitalOcean
> **Frankfurt** VPS. "Not4VPS" = the Binance feed is fine locally / in the US,
> but **not** on the Frankfurt VPS. This document freezes the *exact* Binance
> implementation and the calculation details so we can restore it in one sitting
> if we ever move to a host where Binance is reachable.
>
> Nothing in here is live code. It is a reference snapshot. The live feed is
> Kraken REST (see [feeds.py](feeds.py) → `BinancePriceFeed`).

---

## 1. TL;DR — what is different between Binance and Kraken

| | **Binance (this doc)** | **Kraken REST (live now)** |
|---|---|---|
| Transport | WebSocket (push) | REST (poll) |
| URL | `wss://stream.binance.com:9443/ws/btcusdt@trade` | `https://api.kraken.com/0/public/Ticker?pair=XBTUSD` |
| Symbol | `BTCUSDT` | `XBTUSD` (canonical key `XXBTZUSD`) |
| Tick rate | ~5–20 ticks/sec (every trade) | ~0.5 ticks/sec (1 poll / 2s) |
| Warmup to first σ | ~10–15 s | ~5 min (one full window) |
| Geo-blocked in Frankfurt? | **YES (451)** | No |
| Auth / key needed | No | No |

**The sigma MATH is identical for both.** The estimator is tick-rate-agnostic
(see §4). Only the *data source adapter* changes. No strategy, GBM, edge, Kelly,
or risk code depends on which exchange the price came from — they only use the
*movement* of price, not its absolute level or its source.

---

## 2. Why we left Binance

- Binance returns **HTTP 451 "Unavailable For Legal Reasons"** to data-center
  IP ranges in several EU regions. On a **Frankfurt** VPS the WebSocket handshake
  fails outright, so `BinancePriceFeed` never delivers a tick → `sigma = None`
  and `btc_price = None` **permanently** → the bot never trades.
- This is a **network/geo** problem, not a code bug. The same code works locally
  on the PC (and on US hosts).
- Kraken has no such restriction in the EU and needs no API key, so it is the
  drop-in fallback. We chose **REST polling** (not Kraken WebSocket) for
  simplicity — fewer moving parts, no subscription/reconnect handshake.

---

## 3. The exact original Binance feed (frozen)

This is the `BinancePriceFeed.run()` that streamed live trades. To restore,
replace the body of the current Kraken `BinancePriceFeed` in
[feeds.py](feeds.py) with this. The **class name, the `on_tick`/`on_connect`/
`on_disconnect` callbacks, and the `BinanceTick` dataclass stay exactly the
same** — only `run()` and the class docstring/constants change.

```python
import json
import time
import asyncio
import logging
import websockets   # already a dependency

logger = logging.getLogger("theSecondBot.feeds")

_STALE_TICK_SECONDS = 10.0
_BACKOFF_BASE = 2.0
_BACKOFF_MAX = 60.0

def _next_backoff(attempt: int) -> float:
    return min(_BACKOFF_BASE ** attempt, _BACKOFF_MAX)


class BinancePriceFeed:
    """BTC/USD price via Binance WebSocket trade stream (real-time, every trade).

    Message shape (btcusdt@trade):
        {"e":"trade","E":169..,"s":"BTCUSDT","t":12345,
         "p":"95000.12","q":"0.013","T":1699999999999,"m":false, ...}
      p = trade price (string)   T = trade time in MILLISECONDS
    """

    _WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"

    def __init__(self, on_tick, on_connect=None, on_disconnect=None):
        self.on_tick = on_tick
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.connected = False
        self.last_tick_ts = 0.0

    def is_stale(self) -> bool:
        if self.last_tick_ts == 0:
            return False  # no tick yet — warming up, not stale
        return (time.time() - self.last_tick_ts) > _STALE_TICK_SECONDS

    async def run(self):
        attempt = 0
        while True:
            try:
                async with websockets.connect(
                    self._WS_URL, ping_interval=20, ping_timeout=10
                ) as ws:
                    was_disconnected = not self.connected
                    self.connected = True
                    attempt = 0
                    if was_disconnected:
                        logger.info("Connected to Binance WS (btcusdt@trade)")
                        if self.on_connect:
                            asyncio.create_task(self.on_connect("binance"))
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            price = float(data["p"])           # trade price
                            ts = float(data.get("T", 0)) / 1000.0 or time.time()
                        except (KeyError, ValueError, TypeError):
                            continue
                        if price <= 0:
                            continue
                        tick = BinanceTick(timestamp=ts, price=price)
                        self.last_tick_ts = time.time()
                        self.on_tick(tick)
            except Exception as e:
                was_connected = self.connected
                self.connected = False
                if was_connected and self.on_disconnect:
                    asyncio.create_task(self.on_disconnect("binance"))
                delay = _next_backoff(attempt)
                logger.warning(f"Binance WS failed (attempt {attempt + 1}): {e} — retry in {delay:.0f}s")
                attempt += 1
                await asyncio.sleep(delay)
```

### Binance REST klines (optional sigma seed under Binance)
If running the Binance feed AND you want the startup sigma seed (Fix 2) from
Binance instead of Kraken:

```
GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=60
→ [[openTime, open, high, low, close, volume, closeTime, ...], ...]   # close = index 4
```
Same computation as the Kraken seed (§5): 1-min log-return std × √5 × last_close.
(Also 451-blocked in Frankfurt — that is the whole reason Fix 2 uses Kraken.)

---

## 4. Tick-rate & sigma calculation — the part that must NOT silently break

Live code: [volatility.py](volatility.py) → `VolatilityEstimator.get_sigma_5min_usd()`.

```
rate  = n_returns / t_span                         # ACTUAL ticks per second
sigma_5min_usd = price * sigma_per_tick * sqrt(300 * rate)
```

**Why the `rate` term exists (history):**
- A naive `price * sigma_per_tick * sqrt(300)` only holds at **exactly 1 tick/sec**.
- Binance pushes **5–20 ticks/sec**, so per-tick variance is tiny and a flat
  `sqrt(300)` *under-scaled* sigma badly (~$5–11 vs a true ~$40).
- Kraken REST polls every 2 s (**~0.5 ticks/sec**) — the opposite error.
- The `sqrt(300 * rate)` form normalizes per-tick variance to a fixed **300 s
  (5-min) horizon using the real elapsed time spanned by the buffer**, so the
  output is correct for **any** tick rate. This is what makes Binance↔Kraken a
  pure feed swap with no math change.

**Warmup gate:** `if t_span <= 0 or n_returns < 150: return None`.
- 150 returns ≈ **10–15 s** on Binance (dense ticks).
- 150 returns ≈ **5 min** on Kraken REST (one full window).
- If you restore Binance, the 150 threshold is still valid — it just warms up
  much faster. **Do not lower it blindly**; 150 is a variance-stability floor.

`DailyAverageSigmaTracker` averages these 5-min-σ-USD readings (1 sample/min,
1440-sample cap = ~24 h). Its default was a blind `$50` guess; Fix 2 now seeds it
from real OHLC (§5).

---

## 5. Current Kraken implementation (what replaced Binance)

**Live feed** — [feeds.py](feeds.py) `BinancePriceFeed` (name kept for
compatibility; source is Kraken):
```
GET https://api.kraken.com/0/public/Ticker?pair=XBTUSD   every 2.0s
price = response["result"]["XXBTZUSD"]["c"][0]           # last-trade price
```

**Startup sigma seed (Fix 2)** — [bot.py](bot.py) `_seed_daily_sigma_from_kraken()`:
```
GET https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1
→ result["XXBTZUSD"] = [[time, open, high, low, close, vwap, volume, count], ...]
seed_5min_usd = last_close * std(1min_log_returns) * sqrt(5)
```
Seeds `DailyAverageSigmaTracker` so the regime filter is calibrated from trade #1
instead of using `$50`.

---

## 6. How to revert Kraken → Binance (checklist)

Only touch files inside `theSecondBot/`. The feed interface is unchanged, so the
blast radius is small.

1. **[feeds.py](feeds.py)** — in `BinancePriceFeed`:
   - Replace the class docstring + `_KRAKEN_URL`/`_POLL_INTERVAL` constants and
     the Kraken `run()` body with the **Binance `run()` from §3**.
   - Keep `__init__`, `is_stale`, and the `BinanceTick` dataclass as they are.
   - Update the module top docstring URL back to the Binance WS URL.
2. **[bot.py](bot.py)** — `_seed_daily_sigma_from_kraken()`:
   - Either keep it (Kraken OHLC seed works even with a Binance live feed — it is
     just a one-shot REST call at startup), **or** swap to Binance klines (§3) if
     you specifically want the seed off Binance too. On Frankfurt, keep Kraken.
3. **No changes needed** in: [volatility.py](volatility.py), [strategy.py](strategy.py),
   [risk_manager.py](risk_manager.py), [execution.py](execution.py),
   [run.py](run.py). The math and decision logic are source-agnostic.
4. **Sanity check after revert:**
   - First `📡 BTC=$… σ5m=$…` status line should show σ within ~10–15 s
     (Binance warms fast), not 5 min.
   - `💓 heartbeat binance=OK` should be steady (no 451 in logs).
   - `FILLQ` lines should appear with non-`N/A` sigma.

---

## 7. One-line summary

> The exchange is just a price *source*. Swapping Binance↔Kraken is a feed-adapter
> change only (URL + parse + transport). The σ math is tick-rate-agnostic and the
> strategy only cares about price *movement*, so **no calculation or value changes
> are required** to switch — Binance is faster but 451-blocked in Frankfurt;
> Kraken is slower to warm up but works everywhere.
