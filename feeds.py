"""
Data feeds: BTC price (Kraken REST 2s poll) + Polymarket CLOB (WebSocket) + Gamma market discovery.

Wired to:
- BTC price: https://api.kraken.com/0/public/Ticker?pair=XBTUSD  (REST, polled every 2s)
- Polymarket CLOB: wss://ws-subscriptions-clob.polymarket.com/ws/market  (book stream)
- Gamma REST: https://gamma-api.polymarket.com/markets  (active 5-min BTC markets)
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import aiohttp
import websockets

logger = logging.getLogger("theSecondBot.feeds")

_BACKOFF_BASE = 2.0
_BACKOFF_MAX = 60.0
_STALE_TICK_SECONDS = 10.0


def _next_backoff(attempt: int) -> float:
    return min(_BACKOFF_BASE ** attempt, _BACKOFF_MAX)


def updown_slug(prefix: str, label: str, slot: int) -> str:
    """Polymarket Up/Down market slug, e.g. updown_slug('btc','15m',1782518400)
    -> 'btc-updown-15m-1782518400'."""
    return f"{prefix}-updown-{label}-{int(slot)}"


def slot_starts(now: float, window_size: int, count: int) -> list[int]:
    """The current window-start boundary plus the next (count-1) future ones."""
    current = int(now // window_size) * window_size
    return [current + i * window_size for i in range(count)]


@dataclass
class BinanceTick:
    timestamp: float
    price: float


@dataclass
class PolymarketBookUpdate:
    timestamp: float
    market_id: str
    token_id: str
    direction: str  # "UP" or "DOWN"
    best_bid: float
    best_ask: float
    mid_price: float
    ask_ladder: list = None  # [(price, size), ...] ascending, for depth-walk fills
    bid_ladder: list = None  # [(price, size), ...] descending from best bid
    src: str = "ws"          # "ws" = CLOB websocket, "poll" = endgame REST snapshot


class KrakenRestFeed:
    """BTC/USD price via Kraken public REST Ticker, polled every 2 seconds.

    Primary and only BTC price source. Polls every 2s with exponential-backoff
    retry on failure. price_ready fires once on the first successful tick.
    """

    _KRAKEN_URL = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
    _POLL_INTERVAL = 2.0

    def __init__(self, on_tick: Callable[[BinanceTick], None],
                 on_connect: Optional[Callable] = None,
                 on_disconnect: Optional[Callable] = None):
        self.on_tick = on_tick
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.connected = False
        self.last_tick_ts: float = 0.0
        self.price_ready = asyncio.Event()  # set once on first successful tick

    def is_stale(self) -> bool:
        if self.last_tick_ts == 0:
            return False  # warming up, not stale
        return (time.time() - self.last_tick_ts) > _STALE_TICK_SECONDS

    def is_price_stale(self, threshold: float = 60.0) -> bool:
        if self.last_tick_ts == 0:
            return False
        return (time.time() - self.last_tick_ts) > threshold

    async def run(self):
        attempt = 0
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(
                        self._KRAKEN_URL,
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"Kraken HTTP {resp.status}")
                        data = await resp.json()
                    if data.get("error"):
                        raise RuntimeError(f"Kraken API error: {data['error']}")
                    price = float(data["result"]["XXBTZUSD"]["c"][0])

                    was_disconnected = not self.connected
                    self.connected = True
                    attempt = 0
                    if was_disconnected:
                        logger.info("Connected to Kraken REST ticker (XBTUSD, 2s poll)")
                        if self.on_connect:
                            asyncio.create_task(self.on_connect("kraken_rest"))

                    tick = BinanceTick(timestamp=time.time(), price=price)
                    self.last_tick_ts = time.time()
                    self.price_ready.set()
                    self.on_tick(tick)
                    await asyncio.sleep(self._POLL_INTERVAL)
                except Exception as e:
                    was_connected = self.connected
                    self.connected = False
                    if was_connected and self.on_disconnect:
                        asyncio.create_task(self.on_disconnect("kraken_rest"))
                    delay = _next_backoff(attempt)
                    logger.warning(f"Kraken poll failed (attempt {attempt + 1}): {e} — retry in {delay:.0f}s")
                    attempt += 1
                    await asyncio.sleep(delay)


class PolymarketBookFeed:
    """Polymarket CLOB book updates + Gamma market discovery."""

    def __init__(
        self,
        on_book_update: Callable[[PolymarketBookUpdate], None],
        registry,
        binance_feed,
        market_discovery_interval: int = 30,
        on_connect: Optional[Callable] = None,
        on_disconnect: Optional[Callable] = None,
        asset_prefixes: Optional[dict] = None,
        timeframe_configs: Optional[list] = None,
        stale_after_sec: float = 90.0,
        watchdog_poll_sec: float = 10.0,
    ):
        self.on_book_update = on_book_update
        self.registry = registry
        self.binance_feed = binance_feed
        self.market_discovery_interval = market_discovery_interval
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        # asset -> slug prefix. Default preserves single-asset BTC behavior.
        self.asset_prefixes = asset_prefixes or {"BTC": "btc"}
        # [(label, window_seconds), ...]. Default preserves single 5m behavior.
        self.timeframe_configs = timeframe_configs or [("5m", 300)]
        # per-asset price source: asset -> callable()->float. Falls back to the
        # single _get_btc_price for back-compat.
        self.price_source_by_asset: dict = {}
        self.url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.gamma_url = "https://gamma-api.polymarket.com/markets"
        self.subscribed_tokens: set[str] = set()
        self.token_to_market: dict[str, tuple[str, str]] = {}
        self._ws = None
        self.connected = False
        self._pending_markets: list[dict] = []
        self.stale_after_sec = stale_after_sec
        self.watchdog_poll_sec = watchdog_poll_sec
        # Liveness = data flow. The 2026-07-04 outage was a zombie socket that
        # answered protocol pings for 31h while pushing zero book messages.
        self.last_message_ts = time.time()

    async def discover_active_markets(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch active Up/Down markets for every configured asset by slot-based
        slug query. Each returned market is tagged with its `asset`."""
        markets = []
        now = time.time()
        from datetime import datetime as _dt, timezone as _tz
        logger.info(f"🔎 Discovery cycle | UTC={_dt.now(_tz.utc).strftime('%H:%M:%S')} | "
                    f"assets={list(self.asset_prefixes)} × tfs="
                    f"{[t[0] for t in self.timeframe_configs]}")
        gamma_hits = 0
        gamma_misses = 0
        ended_skipped = 0
        token_failures = 0
        # iterate timeframe × asset × slot
        triples = []
        for label, size in self.timeframe_configs:
            for slot_ts in slot_starts(now, size, 5):
                for asset, prefix in self.asset_prefixes.items():
                    triples.append((asset, prefix, label, size, slot_ts))
        for asset, prefix, label, size, slot_ts in triples:
            slug = updown_slug(prefix, label, int(slot_ts))
            try:
                async with session.get(self.gamma_url, params={"slug": slug},
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        gamma_misses += 1
                        continue
                    data = await r.json()
                    items = data if isinstance(data, list) else data.get("data", [])
                    if not items:
                        gamma_misses += 1
                        continue
                    gamma_hits += 1
                    m = items[0]
                    cond = m.get("conditionId", "")
                    end_iso = m.get("endDate", "")
                    if not cond or not end_iso:
                        token_failures += 1
                        continue
                    try:
                        end_ts = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        token_failures += 1
                        continue
                    if end_ts <= now:
                        ended_skipped += 1
                        continue
                # Look up tokens via CLOB
                clob_url = f"https://clob.polymarket.com/markets/{cond}"
                async with session.get(clob_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        token_failures += 1
                        continue
                    cdata = await r.json()
                    tokens = cdata.get("tokens", [])
                    up_token = down_token = None
                    for t in tokens:
                        if not isinstance(t, dict):
                            continue
                        outcome = str(t.get("outcome", "")).upper()
                        tid = str(t.get("token_id", ""))
                        if outcome in ("UP", "YES"):
                            up_token = tid
                        elif outcome in ("DOWN", "NO"):
                            down_token = tid
                    if (not up_token or not down_token) and len(tokens) == 2:
                        up_token = str(tokens[0].get("token_id", ""))
                        down_token = str(tokens[1].get("token_id", ""))
                    if not up_token or not down_token:
                        token_failures += 1
                        continue
                markets.append({
                    "market_id": cond,
                    "asset": asset,
                    "timeframe": label,
                    "window_size": size,
                    "window_start_ts": end_ts - size,
                    "window_end_ts": end_ts,
                    "up_token_id": up_token,
                    "down_token_id": down_token,
                    "slug": slug,
                })
            except Exception as e:
                logger.debug(f"discover {slug}: {e}")
        registered = len(self.registry.markets)
        closing_5m = sum(1 for m in self.registry.markets.values() if 0 < m["window_end_ts"] - now <= 300)
        closing_10m = sum(1 for m in self.registry.markets.values() if 300 < m["window_end_ts"] - now <= 600)
        logger.info(
            f"📊 Discovery: gamma_hits={gamma_hits} gamma_misses={gamma_misses} "
            f"ended_skipped={ended_skipped} token_failures={token_failures} → returned {len(markets)} markets. "
            f"Registry: {registered} total ({closing_5m} closing ≤5m, {closing_10m} closing 5–10m). "
            f"NOTE: discovery probes {len(triples)} (asset×tf×slot) combos per cycle; "
            f"registry accumulates past windows for 1h before cleanup."
        )
        return markets

    async def _register_new_window(self, market: dict):
        """Register a new window with the registry, capturing price at open."""
        if market["market_id"] in self.registry.markets:
            return False
        price_at_open = self._price_for_asset(market.get("asset"))
        if price_at_open is None:
            if market not in self._pending_markets:
                self._pending_markets.append(market)
                logger.debug(f"Queued {market['slug']} — waiting for price")
            return False
        return self._do_register(market, price_at_open)

    def _price_for_asset(self, asset) -> Optional[float]:
        """Per-asset spot price source; falls back to the single BTC source."""
        if asset and asset in self.price_source_by_asset:
            try:
                return self.price_source_by_asset[asset]()
            except Exception:
                return None
        return self.binance_feed_get_price()

    def _do_register(self, market: dict, btc_at_open: float) -> bool:
        """Write a market into the registry and wire up token lookups."""
        if market["market_id"] in self.registry.markets:
            return False
        self.registry.register_window(
            market_id=market["market_id"],
            window_start_ts=market["window_start_ts"],
            window_end_ts=market["window_end_ts"],
            up_token_id=market["up_token_id"],
            down_token_id=market["down_token_id"],
            btc_price_at_open=btc_at_open,
            asset=market.get("asset"),
            timeframe=market.get("timeframe"),
        )
        self.token_to_market[market["up_token_id"]] = (market["market_id"], "UP")
        self.token_to_market[market["down_token_id"]] = (market["market_id"], "DOWN")
        logger.info(f"📅 Registered window {market['slug']} open=${btc_at_open:.4f}")
        return True

    async def _retry_pending_markets(self):
        """Drain _pending_markets once the REST feed has a price."""
        while True:
            if self._pending_markets:
                new_tokens = []
                for market in self._pending_markets[:]:
                    btc_price = self._price_for_asset(market.get("asset"))
                    if btc_price is not None:
                        registered = self._do_register(market, btc_price)
                        if registered:
                            new_tokens.extend([market["up_token_id"], market["down_token_id"]])
                        self._pending_markets.remove(market)
                if new_tokens and self._ws is not None:
                    await self._subscribe(self._ws, new_tokens)
            await asyncio.sleep(2)

    def binance_feed_get_price(self) -> Optional[float]:
        """Get the latest BTC price from the bot's volatility estimator."""
        if hasattr(self, "_get_btc_price") and callable(self._get_btc_price):
            return self._get_btc_price()
        return None

    async def _subscribe(self, ws, token_ids: list[str]):
        if not token_ids:
            return
        msg = {"type": "Market", "assets_ids": token_ids}
        await ws.send(json.dumps(msg))
        self.subscribed_tokens.update(token_ids)
        logger.info(f"Subscribed to {len(token_ids)} Polymarket tokens")

    def _active_token_set(self) -> set:
        """UP/DOWN tokens of the markets currently in the registry."""
        toks = set()
        for m in self.registry.markets.values():
            toks.add(m["up_token_id"])
            toks.add(m["down_token_id"])
        return toks

    def _prune_subscriptions(self):
        """Drop tokens for markets no longer in the registry so `subscribed_tokens`
        (and the resubscribe on every reconnect) stays bounded to active markets.
        Without this it grew to 3000+ and triggered a WS reconnect storm."""
        self.subscribed_tokens &= self._active_token_set()

    def _missing_active_tokens(self) -> set:
        """Active-market tokens not (yet) in `subscribed_tokens`. A market is
        marked registered in the registry BEFORE its WS subscribe is sent
        (_do_register then _subscribe are two separate steps); a reconnect
        landing between them drops that subscribe silently and — since the
        registry check blocks re-registration — it is never retried. This
        detects the gap so it can be healed, instead of a market sitting with
        current_up_ask=None for its entire window (silent "window ended" drop)."""
        return self._active_token_set() - self.subscribed_tokens

    async def _reconcile_subscriptions(self, ws):
        """Subscribe any active market tokens missing from `subscribed_tokens`."""
        missing = self._missing_active_tokens()
        if missing:
            logger.warning(f"Reconciling {len(missing)} unsubscribed active tokens "
                           f"(lost to a prior reconnect race)")
            await self._subscribe(ws, list(missing))

    async def _discovery_loop(self, session: aiohttp.ClientSession):
        while True:
            try:
                markets = await self.discover_active_markets(session)
                new_tokens = []
                for m in markets:
                    if await self._register_new_window(m):
                        new_tokens.extend([m["up_token_id"], m["down_token_id"]])
                if new_tokens and self._ws is not None:
                    await self._subscribe(self._ws, new_tokens)
                self.registry.cleanup_old(time.time())
                self._prune_subscriptions()   # keep the subscription bounded to active markets
                if self._ws is not None:
                    await self._reconcile_subscriptions(self._ws)   # heal any lost subscribe
            except Exception as e:
                logger.error(f"Discovery loop error: {e}")
            await asyncio.sleep(self.market_discovery_interval)

    def _parse_book_message(self, data) -> Optional[PolymarketBookUpdate]:
        """Parse a CLOB book message into a PolymarketBookUpdate."""
        msgs = data if isinstance(data, list) else [data]
        for m in msgs:
            if not isinstance(m, dict):
                continue
            asset_id = m.get("asset_id") or m.get("market")
            if not asset_id or asset_id not in self.token_to_market:
                continue
            market_id, direction = self.token_to_market[asset_id]

            bids = m.get("bids") or m.get("buys") or []
            asks = m.get("asks") or m.get("sells") or []
            try:
                best_bid = max((float(b["price"]) for b in bids), default=0.0)
                best_ask = min((float(a["price"]) for a in asks), default=1.0)
                # ascending (price, size) ladder for depth-walk fills
                ask_ladder = sorted(
                    ((float(a["price"]), float(a["size"])) for a in asks),
                    key=lambda lvl: lvl[0],
                )
                # descending from best bid, for sell-side depth
                bid_ladder = sorted(
                    ((float(b["price"]), float(b["size"])) for b in bids),
                    key=lambda lvl: lvl[0], reverse=True,
                )
            except (KeyError, ValueError, TypeError):
                continue
            if best_bid <= 0 and best_ask >= 1:
                continue
            mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask < 1 else (best_bid or best_ask)
            ts = float(m.get("timestamp", 0)) / 1000.0 if m.get("timestamp") else 0
            if ts == 0:
                ts = time.time()
            return PolymarketBookUpdate(
                timestamp=ts, market_id=market_id, token_id=asset_id,
                direction=direction, best_bid=best_bid, best_ask=best_ask, mid_price=mid,
                ask_ladder=ask_ladder, bid_ladder=bid_ladder,
            )
        return None

    # ── Endgame REST book poller ─────────────────────────────────
    # WS book messages typically go quiet ~40s before window end (static or
    # abandoned book), leaving the endgame invisible in ud_ticks. This polls
    # the CLOB REST book for every market inside its final stretch so the
    # last minute is captured regardless of WS behavior. Snapshots are
    # emitted through the same on_book_update path, tagged src="poll".
    _CLOB_BOOK_URL = "https://clob.polymarket.com/book"
    endgame_window_sec: float = 80.0
    endgame_poll_sec: float = 2.0

    async def _poll_book_once(self, session, token_id: str):
        try:
            async with session.get(self._CLOB_BOOK_URL,
                                   params={"token_id": token_id},
                                   timeout=aiohttp.ClientTimeout(total=6)) as r:
                if r.status != 200:
                    return
                data = await r.json()
        except Exception:
            return
        if not isinstance(data, dict):
            return
        data = dict(data)
        data.setdefault("asset_id", token_id)
        upd = self._parse_book_message(data)
        if upd:
            upd.src = "poll"
            try:
                self.on_book_update(upd)
            except Exception as e:
                logger.debug(f"endgame update error: {e}")

    async def _endgame_poll_loop(self, session):
        while True:
            try:
                now = time.time()
                for m in list(self.registry.markets.values()):
                    remaining = m["window_end_ts"] - now
                    if 0 < remaining <= self.endgame_window_sec:
                        for tid in (m.get("up_token_id"), m.get("down_token_id")):
                            if tid:
                                await self._poll_book_once(session, tid)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"endgame poll loop error: {e}")
            await asyncio.sleep(self.endgame_poll_sec)

    async def _watchdog_loop(self, ws):
        """Force-close a zombie connection. Protocol ping/pong is not proof of
        life — only inbound messages are. Closing the ws ends the `async for`
        in run(), which drops into the proven reconnect/resubscribe path."""
        while True:
            await asyncio.sleep(self.watchdog_poll_sec)
            try:
                silent = time.time() - self.last_message_ts
                if silent > self.stale_after_sec:
                    logger.warning(f"Polymarket feed stalled {silent:.0f}s "
                                   f"(no ws messages) — forcing reconnect")
                    await ws.close()
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"feed watchdog error: {e}")

    async def run(self):
        attempt = 0
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    logger.info(f"Connecting to Polymarket CLOB: {self.url}")
                    async with websockets.connect(self.url, ping_interval=20, ping_timeout=10) as ws:
                        self._ws = ws
                        self.connected = True
                        attempt = 0
                        logger.info("Connected to Polymarket CLOB")
                        self.last_message_ts = time.time()   # fresh grace window
                        # Resubscribe only ACTIVE markets — never the full historical
                        # set (that bloated to 3000+ tokens and caused a reconnect storm).
                        self._prune_subscriptions()
                        if self.subscribed_tokens:
                            await self._subscribe(ws, list(self.subscribed_tokens))
                        # a reconnect (or the one before it) may have dropped a
                        # market's subscribe entirely — heal it on every fresh connect
                        await self._reconcile_subscriptions(ws)
                        if self.on_connect:
                            asyncio.create_task(self.on_connect("polymarket"))
                        discovery_task = asyncio.create_task(self._discovery_loop(session))
                        retry_task = asyncio.create_task(self._retry_pending_markets())
                        watchdog_task = asyncio.create_task(self._watchdog_loop(ws))
                        endgame_task = asyncio.create_task(self._endgame_poll_loop(session))
                        try:
                            async for msg in ws:
                                self.last_message_ts = time.time()
                                try:
                                    data = json.loads(msg)
                                    update = self._parse_book_message(data)
                                    if update:
                                        self.on_book_update(update)
                                except Exception as e:
                                    logger.debug(f"Book parse error: {e}")
                        finally:
                            discovery_task.cancel()
                            retry_task.cancel()
                            watchdog_task.cancel()
                            endgame_task.cancel()
                except Exception as e:
                    logger.error(f"Polymarket feed error: {e}")
                self.connected = False
                self._ws = None
                if self.on_disconnect:
                    asyncio.create_task(self.on_disconnect("polymarket"))
                delay = _next_backoff(attempt)
                attempt += 1
                logger.info(f"Polymarket reconnecting in {delay:.0f}s (attempt {attempt})")
                await asyncio.sleep(delay)


class KrakenTradeFeed:
    """Kraken WebSocket v2 `trade` channel — per-trade price/qty/side.

    This is the venue-native aggressor-flow feed (real CVD), unlike the 2s
    REST ticker which only gives a price point. on_trade(asset, ts, price,
    qty, side) fires for every trade; side is "buy"/"sell" = taker side.
    """

    _URL = "wss://ws.kraken.com/v2"

    def __init__(self, symbol_to_asset: dict, on_trade: Callable,
                 stale_after_sec: float = 90.0):
        # e.g. {"BTC/USD": "BTC", "ETH/USD": "ETH"}
        self.symbol_to_asset = symbol_to_asset
        self.on_trade = on_trade
        self.connected = False
        self.last_message_ts = time.time()
        # Kraken v2 pushes heartbeat frames ~1/s on a live subscription, so
        # >90s of total silence = zombie socket (the 2026-07-04 failure mode:
        # protocol pings answered, zero data for 31h). Force-close to trigger
        # the reconnect path rather than trusting ping/pong.
        self.stale_after_sec = stale_after_sec

    @staticmethod
    def _parse_ts(raw) -> float:
        if isinstance(raw, (int, float)):
            return float(raw)
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            return time.time()

    def _handle(self, data: dict) -> None:
        if data.get("channel") != "trade":
            return
        for tr in data.get("data", []):
            asset = self.symbol_to_asset.get(tr.get("symbol"))
            if asset is None:
                continue
            try:
                price = float(tr["price"])
                qty = float(tr["qty"])
                side = str(tr.get("side", "")).lower()
            except (KeyError, ValueError, TypeError):
                continue
            if side not in ("buy", "sell"):
                continue
            self.on_trade(asset, self._parse_ts(tr.get("timestamp")), price, qty, side)

    async def _watchdog(self, ws):
        while True:
            await asyncio.sleep(10)
            silent = time.time() - self.last_message_ts
            if silent > self.stale_after_sec:
                logger.warning(f"Kraken trade WS stalled {silent:.0f}s "
                               f"(no frames, not even heartbeat) — forcing reconnect")
                try:
                    await ws.close()
                except Exception:
                    pass
                return

    async def run(self):
        attempt = 0
        sub = {"method": "subscribe",
               "params": {"channel": "trade",
                          "symbol": list(self.symbol_to_asset)}}
        while True:
            watchdog_task = None
            try:
                async with websockets.connect(self._URL, ping_interval=20,
                                              ping_timeout=10) as ws:
                    await ws.send(json.dumps(sub))
                    self.connected = True
                    attempt = 0
                    self.last_message_ts = time.time()
                    logger.info(f"Connected to Kraken trade WS "
                                f"({list(self.symbol_to_asset)})")
                    watchdog_task = asyncio.create_task(self._watchdog(ws))
                    async for msg in ws:
                        self.last_message_ts = time.time()
                        try:
                            self._handle(json.loads(msg))
                        except Exception as e:
                            logger.debug(f"kraken trade parse error: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Kraken trade WS error: {e}")
            finally:
                if watchdog_task is not None:
                    watchdog_task.cancel()
            self.connected = False
            delay = _next_backoff(attempt)
            attempt += 1
            logger.info(f"Kraken trade WS reconnecting in {delay:.0f}s")
            await asyncio.sleep(delay)


class CurrentMarketsRegistry:
    def __init__(self):
        self.markets: dict[str, dict] = {}

    def register_window(self, market_id, window_start_ts, window_end_ts,
                        up_token_id, down_token_id, btc_price_at_open,
                        asset=None, timeframe=None):
        self.markets[market_id] = {
            "market_id": market_id,
            "asset": asset,
            "timeframe": timeframe,
            "window_start_ts": window_start_ts, "window_end_ts": window_end_ts,
            "up_token_id": up_token_id, "down_token_id": down_token_id,
            "p_open": btc_price_at_open,
            "current_up_bid": None, "current_up_ask": None,
            "current_down_bid": None, "current_down_ask": None,
            "up_ask_ladder": [], "down_ask_ladder": [],
            "last_update_ts": 0,
        }

    def update_prices(self, market_id, direction, bid, ask, ts, ask_ladder=None):
        if market_id not in self.markets:
            return
        m = self.markets[market_id]
        if direction == "UP":
            m["current_up_bid"] = bid
            m["current_up_ask"] = ask
            if ask_ladder is not None:
                m["up_ask_ladder"] = ask_ladder
        else:
            m["current_down_bid"] = bid
            m["current_down_ask"] = ask
            if ask_ladder is not None:
                m["down_ask_ladder"] = ask_ladder
        m["last_update_ts"] = ts

    def get_active_market(self, current_ts):
        best = None
        for m in self.markets.values():
            if m["window_start_ts"] <= current_ts < m["window_end_ts"]:
                if m["current_up_bid"] is not None and m["current_down_bid"] is not None:
                    return m
                if best is None:
                    best = m
        return best

    def cleanup_old(self, current_ts):
        to_remove = [mid for mid, m in self.markets.items() if m["window_end_ts"] < current_ts - 3600]
        for mid in to_remove:
            del self.markets[mid]
