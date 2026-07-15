"""
Maker-only order execution on Polymarket CLOB.

All SDK REST calls run in asyncio.run_in_executor() so they never block the
event loop.  Critical-path latency: ~100-300ms per REST call vs 300-900ms
when blocking the loop.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Literal

logger = logging.getLogger("theSecondBot.execution")

Direction = Literal["UP", "DOWN"]

# ──────────────────────────────────────────────────────────────────────────────
# #7  Rate limiter (async token bucket)
# ──────────────────────────────────────────────────────────────────────────────

class _AsyncTokenBucket:
    def __init__(self, rate: float, burst: int):
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock: Optional[asyncio.Lock] = None  # lazy — created inside running loop

    async def consume(self, n: int = 1):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= n:
                self._tokens -= n
                return
            wait = (n - self._tokens) / self._rate
            self._tokens = 0.0
        if wait > 0:
            await asyncio.sleep(wait)


_rate_limiter = _AsyncTokenBucket(rate=5.0, burst=5)


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    filled_size: float
    filled_price: float
    fees_paid: float
    error_message: Optional[str] = None
    partial: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# #2  Error classification
# ──────────────────────────────────────────────────────────────────────────────

class _RejectionKind:
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    MARKET_CLOSED        = "MARKET_CLOSED"
    PRICE_OUT_OF_RANGE   = "PRICE_OUT_OF_RANGE"
    ORDER_TOO_SMALL      = "ORDER_TOO_SMALL"
    RATE_LIMITED         = "RATE_LIMITED"
    AUTH_ERROR           = "AUTH_ERROR"
    UNKNOWN              = "UNKNOWN"


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("insufficient", "balance", "funds", "collateral")):
        return _RejectionKind.INSUFFICIENT_BALANCE
    if any(k in msg for k in ("closed", "paused", "not active", "not trading", "resolved")):
        return _RejectionKind.MARKET_CLOSED
    if any(k in msg for k in ("price", "out of range", "invalid price", "min price", "max price")):
        return _RejectionKind.PRICE_OUT_OF_RANGE
    if any(k in msg for k in ("too small", "minimum size", "min_size", "below minimum")):
        return _RejectionKind.ORDER_TOO_SMALL
    if any(k in msg for k in ("429", "rate limit", "too many requests")):
        return _RejectionKind.RATE_LIMITED
    if any(k in msg for k in ("401", "403", "unauthorized", "forbidden", "api key")):
        return _RejectionKind.AUTH_ERROR
    return _RejectionKind.UNKNOWN


# ──────────────────────────────────────────────────────────────────────────────
# Executor
# ──────────────────────────────────────────────────────────────────────────────

class PolymarketExecutor:
    def __init__(self, clob_client, max_entry_attempts: int = 3,
                 max_slippage: float = 0.08,
                 min_partial_fill_ratio: float = 0.50,
                 on_auth_error=None,
                 max_entry_slippage: float = 0.05):
        self.client = clob_client
        self.max_entry_attempts = max_entry_attempts
        self.max_slippage = max_slippage
        # Tighter adverse-selection guard: reject if the achievable fill price
        # (best_ask for a buy) is more than this above the intended entry price.
        self.max_entry_slippage = max_entry_slippage
        self.min_partial_fill_ratio = min_partial_fill_ratio
        self.on_auth_error = on_auth_error

        from py_clob_client_v2.clob_types import OrderArgs, OrderType, OrderPayload
        self.OrderArgs = OrderArgs
        self.OrderType = OrderType
        self.OrderPayload = OrderPayload

    # ── helpers ──────────────────────────────────────────────────────────────

    def _run(self, fn):
        """Schedule a blocking SDK call in the thread-pool executor."""
        return asyncio.get_event_loop().run_in_executor(None, fn)

    async def get_orderbook(self, token_id: str) -> dict:
        book = await self._run(lambda: self.client.get_order_book(token_id))
        if isinstance(book, dict):
            raw_bids = book.get("bids") or []
            raw_asks = book.get("asks") or []
        else:
            raw_bids = book.bids or []
            raw_asks = book.asks or []

        def _to_entry(x):
            if isinstance(x, dict):
                return {"price": str(x.get("price", 0)), "size": str(x.get("size", 0))}
            return {"price": str(x.price), "size": str(x.size)}

        bids = sorted([_to_entry(b) for b in raw_bids], key=lambda x: float(x["price"]), reverse=True)
        asks = sorted([_to_entry(a) for a in raw_asks], key=lambda x: float(x["price"]))
        return {"bids": bids, "asks": asks}

    async def get_best_bid_ask(self, token_id: str) -> tuple[float, float]:
        try:
            book = await self.get_orderbook(token_id)
            bid = float(book["bids"][0]["price"]) if book["bids"] else 0.0
            ask = float(book["asks"][0]["price"]) if book["asks"] else 1.0
            return (bid, ask)
        except Exception as e:
            logger.warning(f"orderbook fetch failed: {e}")
            return (0.0, 1.0)

    def get_best_bid_ask_sync(self, token_id: str) -> tuple[float, float]:
        """Sync version for use outside async context (position restore, etc.)."""
        try:
            book = self.client.get_order_book(token_id)
            if isinstance(book, dict):
                raw_bids = book.get("bids") or []
                raw_asks = book.get("asks") or []
            else:
                raw_bids = book.bids or []
                raw_asks = book.asks or []
            bid = float(raw_bids[0]["price"] if isinstance(raw_bids[0], dict) else raw_bids[0].price) if raw_bids else 0.0
            ask = float(raw_asks[0]["price"] if isinstance(raw_asks[0], dict) else raw_asks[0].price) if raw_asks else 1.0
            return (bid, ask)
        except Exception as e:
            logger.warning(f"sync orderbook fetch failed: {e}")
            return (0.0, 1.0)

    def get_mid_price(self, token_id: str) -> float:
        bid, ask = self.get_best_bid_ask_sync(token_id)
        return (bid + ask) / 2.0

    def _round_tick(self, price: float) -> float:
        return round(price * 100) / 100.0

    def _slippage_ok(self, target_price: float, live_ask: float) -> bool:
        return live_ask <= target_price + self.max_slippage

    # ── place maker buy ──────────────────────────────────────────────────────

    async def place_maker_buy(self, token_id: str, max_price: float, size_usd: float,
                              timeout_sec: int = 30, bankroll: float = 0.0) -> OrderResult:
        for attempt in range(self.max_entry_attempts):
            await _rate_limiter.consume()

            best_bid, best_ask = await self.get_best_bid_ask(token_id)
            logger.info(f"  Orderbook: bid={best_bid:.3f} ask={best_ask:.3f} (target={max_price:.3f})")

            if best_ask >= 1.0 and best_bid <= 0.0:
                logger.warning("Orderbook empty — no liquidity, skipping")
                return OrderResult(False, None, 0, 0, 0, "orderbook empty, no liquidity")

            if best_ask < 1.0 and not self._slippage_ok(max_price, best_ask):
                logger.warning(
                    f"Slippage abort: best_ask={best_ask:.3f} > target={max_price:.3f}+{self.max_slippage:.2f}"
                )
                return OrderResult(False, None, 0, 0, 0,
                                   f"slippage too high: ask={best_ask:.3f} target={max_price:.3f}")

            # Cross the spread when ask is within allowed slippage budget.
            # Old code capped at max_price (bid) so orders never filled as taker.
            # Now: if ask ≤ bid + max_entry_slippage, pay the ask and fill immediately.
            limit_price = min(best_ask, max_price + self.max_entry_slippage)
            limit_price = self._round_tick(limit_price)

            if limit_price <= 0 or limit_price >= 1:
                return OrderResult(False, None, 0, 0, 0, f"invalid limit price: {limit_price}")

            # Slippage guard (adverse-selection fix): the real fillable price is the
            # best ask. If the book has moved >max_entry_slippage above the intended
            # entry price, the edge the signal assumed is gone — skip the trade.
            intended = max_price
            fill_price = best_ask
            if fill_price < 1.0 and fill_price > intended + self.max_entry_slippage:
                logger.warning(
                    f"slippage guard: fill {fill_price:.3f} > intended {intended:.3f}"
                    f"+{self.max_entry_slippage:.2f} — skipping (adverse selection)"
                )
                return OrderResult(False, None, 0, 0, 0,
                                   f"slippage guard: fill {fill_price:.3f} > intended {intended:.3f}+{self.max_entry_slippage:.2f}")

            num_shares = round(size_usd / limit_price, 2)
            if size_usd < 1.0:
                size_usd = 1.0
                num_shares = round(size_usd / limit_price, 2)
            if num_shares < 5.0:
                num_shares = 5.0
                size_usd = num_shares * limit_price
                logger.info(f"Bumped to minimum 5 shares → size_usd=${size_usd:.2f}")
                if bankroll > 0 and size_usd > bankroll * 0.08:
                    logger.warning(
                        f"Minimum order ${size_usd:.2f} exceeds 8% risk limit "
                        f"(${bankroll * 0.08:.2f} of ${bankroll:.2f} bankroll) — skipping trade"
                    )
                    return OrderResult(False, None, 0, 0, 0,
                                       f"min order ${size_usd:.2f} > 8% risk limit")
            logger.info(f"Maker BUY attempt {attempt+1}: ${limit_price:.3f} x {num_shares:.2f} (${size_usd:.2f})")

            try:
                order = self.OrderArgs(
                    token_id=token_id, price=limit_price,
                    size=num_shares, side="BUY",
                    expiration=int(time.time()) + 150,
                )
                resp = await self._run(
                    lambda o=order: self.client.create_and_post_order(o, order_type=self.OrderType.GTD)
                )
                if not resp:
                    raise RuntimeError("create_and_post_order returned empty response")
                if resp.get("errorMsg") or resp.get("error"):
                    raise RuntimeError(f"order rejected: {resp.get('errorMsg') or resp.get('error')}")
                order_id = resp.get("orderID") or resp.get("id") or resp.get("orderId")
                if not order_id:
                    raise RuntimeError(f"no order id in CLOB response: {resp!r}")
                logger.info(f"✅ Order placed on CLOB: {str(order_id)[:12]}…")
            except Exception as e:
                kind = _classify_error(e)
                logger.error(f"Order rejected ({kind}): {e}")

                if kind == _RejectionKind.INSUFFICIENT_BALANCE:
                    return OrderResult(False, None, 0, 0, 0, f"insufficient balance: {e}")
                if kind == _RejectionKind.MARKET_CLOSED:
                    return OrderResult(False, None, 0, 0, 0, f"market closed: {e}")
                if kind == _RejectionKind.ORDER_TOO_SMALL:
                    return OrderResult(False, None, 0, 0, 0, f"order too small: {e}")
                if kind == _RejectionKind.RATE_LIMITED:
                    logger.warning("Rate limited — waiting 2s")
                    await asyncio.sleep(2.0)
                    continue
                if kind == _RejectionKind.AUTH_ERROR:
                    if self.on_auth_error:
                        logger.warning("Auth error — refreshing credentials")
                        self.on_auth_error()
                        continue
                    return OrderResult(False, None, 0, 0, 0, f"auth error: {e}")
                continue

            filled = await self._wait_for_fill(order_id, timeout_sec)
            if filled:
                partial = filled["filled_size"] < num_shares * 0.99
                ratio = filled["filled_size"] / num_shares if num_shares > 0 else 0

                if partial and ratio < self.min_partial_fill_ratio:
                    logger.warning(
                        f"Partial fill too small: {filled['filled_size']:.2f}/{num_shares:.2f} "
                        f"({ratio:.0%} < {self.min_partial_fill_ratio:.0%}) — cancelling, skipping trade"
                    )
                    try:
                        oid = order_id
                        await self._run(lambda: self.client.cancel_order(self.OrderPayload(orderID=oid)))
                    except Exception:
                        pass
                    return OrderResult(False, order_id, filled["filled_size"], filled["filled_price"], 0,
                                       f"partial fill below threshold ({ratio:.0%})")

                if partial:
                    logger.info(
                        f"Partial fill accepted: {filled['filled_size']:.2f}/{num_shares:.2f} ({ratio:.0%})"
                    )

                return OrderResult(
                    success=True, order_id=order_id,
                    filled_size=filled["filled_size"],
                    filled_price=filled["filled_price"],
                    fees_paid=0.0,
                    partial=partial,
                )

            try:
                oid = order_id
                await self._run(lambda: self.client.cancel_order(self.OrderPayload(orderID=oid)))
            except Exception as e:
                logger.debug(f"cancel failed (already filled?): {e}")
            # Return the order_id even on timeout — caller needs it for orphan guard.
            return OrderResult(False, order_id, 0, 0, 0,
                               f"no fill within {timeout_sec}s — order_id preserved for orphan guard")

        return OrderResult(False, None, 0, 0, 0,
                           f"no maker fill after {self.max_entry_attempts} attempts")

    # ── place maker sell ─────────────────────────────────────────────────────

    async def place_maker_sell(self, token_id: str, min_price: float, size_shares: float,
                               timeout_sec: int = 20, allow_taker_fallback: bool = False,
                               taker_min_price: float = 0.01) -> OrderResult:
        for attempt in range(self.max_entry_attempts):
            await _rate_limiter.consume()

            best_bid, best_ask = await self.get_best_bid_ask(token_id)
            limit_price = max(best_ask - (attempt * 0.01), best_bid + 0.01, min_price)
            limit_price = self._round_tick(limit_price)

            if limit_price <= 0 or limit_price >= 1:
                return OrderResult(False, None, 0, 0, 0, f"invalid limit price: {limit_price}")

            sell_shares = max(round(size_shares, 2), 5.0)
            logger.info(f"Maker SELL attempt {attempt+1}: ${limit_price:.3f} x {sell_shares:.2f}")

            try:
                order = self.OrderArgs(
                    token_id=token_id, price=limit_price,
                    size=sell_shares, side="SELL",
                    expiration=int(time.time()) + 150,
                )
                resp = await self._run(
                    lambda o=order: self.client.create_and_post_order(o, order_type=self.OrderType.GTD)
                )
                if not resp:
                    raise RuntimeError("create_and_post_order returned empty response")
                if resp.get("errorMsg") or resp.get("error"):
                    raise RuntimeError(f"sell rejected: {resp.get('errorMsg') or resp.get('error')}")
                order_id = resp.get("orderID") or resp.get("id") or resp.get("orderId")
                if not order_id:
                    raise RuntimeError(f"no order id in sell response: {resp!r}")
            except Exception as e:
                kind = _classify_error(e)
                logger.error(f"Sell order rejected ({kind}): {e}")
                if kind in (_RejectionKind.MARKET_CLOSED, _RejectionKind.INSUFFICIENT_BALANCE):
                    break
                if kind == _RejectionKind.RATE_LIMITED:
                    await asyncio.sleep(2.0)
                if kind == _RejectionKind.AUTH_ERROR and self.on_auth_error:
                    self.on_auth_error()
                continue

            filled = await self._wait_for_fill(order_id, timeout_sec)
            if filled:
                ratio = filled["filled_size"] / sell_shares if sell_shares > 0 else 0
                partial = filled["filled_size"] < sell_shares * 0.99
                if partial:
                    logger.info(
                        f"Partial sell: {filled['filled_size']:.2f}/{sell_shares:.2f} ({ratio:.0%}) "
                        f"— remaining shares will settle at resolution"
                    )
                return OrderResult(
                    success=True, order_id=order_id,
                    filled_size=filled["filled_size"],
                    filled_price=filled["filled_price"],
                    fees_paid=0.0,
                    partial=partial,
                )
            try:
                oid = order_id
                await self._run(lambda: self.client.cancel_order(self.OrderPayload(orderID=oid)))
            except Exception as e:
                logger.debug(f"cancel failed: {e}")

        if allow_taker_fallback:
            logger.warning("Maker sell failed — falling back to taker FOK")
            await _rate_limiter.consume()
            try:
                order = self.OrderArgs(
                    token_id=token_id, price=max(taker_min_price, 0.01),
                    size=round(size_shares, 2), side="SELL",
                )
                resp = await self._run(
                    lambda o=order: self.client.create_and_post_order(o, order_type=self.OrderType.FOK)
                )
                if not resp or resp.get("errorMsg") or resp.get("error"):
                    raise RuntimeError(f"taker FOK rejected: {resp!r}")
                order_id = resp.get("orderID") or resp.get("id") or resp.get("orderId")
                if not order_id:
                    raise RuntimeError(f"no order id in taker response: {resp!r}")
                filled = await self._wait_for_fill(order_id, timeout_sec=10)
                if filled:
                    fee_rate = 0.0156
                    payout = filled["filled_size"] * filled["filled_price"]
                    return OrderResult(
                        success=True, order_id=order_id,
                        filled_size=filled["filled_size"],
                        filled_price=filled["filled_price"],
                        fees_paid=payout * fee_rate,
                    )
            except Exception as e:
                logger.error(f"Taker fallback failed: {e}")

        return OrderResult(False, None, 0, 0, 0,
                           f"no fill after {self.max_entry_attempts} attempts")

    # ── fill poller ──────────────────────────────────────────────────────────

    async def _wait_for_fill(self, order_id: Optional[str], timeout_sec: int) -> Optional[dict]:
        if not order_id:
            return None
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                oid = order_id
                o = await self._run(lambda: self.client.get_order(oid))
                status = (o.get("status") if isinstance(o, dict) else getattr(o, "status", "")) or ""
                size_matched = float((o.get("size_matched") if isinstance(o, dict)
                                      else getattr(o, "size_matched", 0)) or 0)
                price = float((o.get("price") if isinstance(o, dict)
                               else getattr(o, "price", 0)) or 0)
                if status.upper() in ("MATCHED", "FILLED") and size_matched > 0:
                    return {"order_id": order_id, "filled_size": size_matched, "filled_price": price}
            except Exception as e:
                logger.debug(f"order poll error: {e}")
            await asyncio.sleep(1.0)
        return None
