"""theSecondBot — per-asset Kraken 1m engine: candles + session VWAP (H+L+C)/3."""
from __future__ import annotations

import asyncio
import time

from vwap_engine import Candle, session_start_utc, today_utc_str  # noqa: F401 (today_utc_str kept for parity/debug use)
import ud_indicators as ind


class _SessionShim:
    """Minimal stand-in exposing last_ts for the reused telegram menu."""
    def __init__(self):
        self.last_ts = 0


class AssetEngine:
    def __init__(self, asset, kraken_pair, cfg, provider, now_fn=time.time):
        self.asset = asset
        self.kraken_pair = kraken_pair
        self.cfg = cfg
        self._provider = provider
        self._now = now_fn
        self.session_candles: list[Candle] = []   # today's session (ts >= 00:00 UTC)
        self.vwaps: list[float | None] = []        # vwap_prev as of each session candle
        self.closed_1m: dict[int, Candle] = {}     # buffer for window resolution
        self.vwap_reconstructed = False
        self._session_day: int | None = None
        self.session = _SessionShim()

    @property
    def stream_key(self) -> str:
        return self.asset

    async def _fetch(self, since):
        res = self._provider(1, since)
        if asyncio.iscoroutine(res):
            res = await res
        return res

    def _append(self, c: Candle):
        self.session_candles.append(c)
        # vwap_prev for THIS candle = session VWAP of all prior session candles
        self.vwaps.append(ind.session_vwap(self.session_candles[:-1]))
        self.session.last_ts = c.ts
        self.closed_1m[c.ts] = c
        if len(self.closed_1m) > 5000:
            for k in sorted(self.closed_1m)[:2000]:
                del self.closed_1m[k]

    async def warm_start(self):
        now = self._now()
        ss = session_start_utc(now)
        self._session_day = ss
        candles = await self._fetch(None)
        for c in candles:
            if c.ts + 60 > now:
                continue                   # in-progress candle
            self.closed_1m[c.ts] = c
            if c.ts < ss:
                continue                   # older than today's session
            self._append(c)
        self.vwap_reconstructed = True

    async def poll_once(self) -> list[Candle]:
        now = self._now()
        since = self.session.last_ts or None
        new = await self._fetch(since)
        out = []
        for c in sorted(new, key=lambda x: x.ts):
            if self.session.last_ts and c.ts <= self.session.last_ts:
                continue
            if c.ts + 60 > now:
                continue
            cday = session_start_utc(c.ts)
            if self._session_day is None:
                self._session_day = cday
            elif cday > self._session_day:
                self.session_candles.clear()
                self.vwaps.clear()
                self._session_day = cday
            self._append(c)
            self.vwap_reconstructed = False
            out.append(c)
        return out

    def vwap_prev(self) -> float | None:
        return ind.session_vwap(self.session_candles)

    def candle_5m_at(self, window_start_ts: int) -> Candle | None:
        want = [window_start_ts + i * 60 for i in range(5)]
        got = [self.closed_1m.get(ts) for ts in want]
        if any(c is None for c in got):
            return None
        return Candle(
            ts=window_start_ts, open=got[0].open,
            high=max(c.high for c in got), low=min(c.low for c in got),
            close=got[-1].close, volume=sum(c.volume for c in got),
        )


def kraken_ticker_provider(pair: str):
    """Production provider: returns an async callable()->float hitting
    Kraken's public Ticker endpoint for the live last-trade price. Used by
    the confirmation gate to check a VWAP cross still holds between candle
    closes. Imported lazily so unit tests need no network."""
    import aiohttp

    async def provider() -> float:
        url = "https://api.kraken.com/0/public/Ticker"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params={"pair": pair},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                data = await r.json()
        result = data["result"]
        return float(next(iter(result.values()))["c"][0])

    return provider
