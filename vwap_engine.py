"""
# ⚠️ RETIRED 2026-07-06 — replaced by the intra-window Up/Down bot (ud_*.py).
# Kept only so historical tests still pass. Not imported by run.py.
# NOTE: ud_engine.py / ud_bot.py still import shared utilities from this module
# (Candle, atomic_write_json, kraken_ohlc_provider, session_start_utc,
# today_utc_str, parse_ohlc) — those functions/classes remain in active use and
# must not be changed or removed. Only the VwapEngine/VwapBot orchestration
# built around them is retired.

theSecondBot — VWAP engine.

Per-asset session-anchored VWAP built from Kraken 1m OHLC, with warm-start
reconstruction from coarser intervals (Kraken REST 1m depth is capped at ~720
candles = 12h; interval=15 reaches 180h, enough to seed from 00:00 UTC), disk
persistence, and a live incremental poller.

This file is split by concern:
  - parsing            (parse_ohlc, select_since, Candle)
  - session VWAP math  (SessionVwap)   [T3]
  - persistence        (save/load)     [T4]
  - async driver       (KrakenOhlcPoller / VwapEngine)  [T12]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("theSecondBot.vwap_engine")


def atomic_write_json(path: str, obj: dict):
    """Write JSON to a temp file in the same dir, then rename over the target.

    os.replace is atomic on POSIX and Windows, so a crash mid-write can never
    leave a half-written file at `path`.
    """
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@dataclass(frozen=True)
class Candle:
    ts: int          # candle open time (unix seconds), aligned to the interval
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def ohlc4(self) -> float:
        return (self.open + self.high + self.low + self.close) / 4.0


@dataclass(frozen=True)
class CandleClose:
    candle: Candle
    vwap_prev: float | None   # session VWAP at the candle's OPEN (before add)
    was_reconstructed: bool   # reconstructed state when this candle closed


def parse_ohlc(data: dict) -> list[Candle]:
    """Parse a Kraken /0/public/OHLC response into Candles.

    Reads the single result key that is not `last` (the pair key varies:
    XXBTZUSD / SOLUSD / XETHZUSD / XXRPZUSD). Raises ValueError on a Kraken
    API error.
    """
    err = data.get("error") or []
    if err:
        raise ValueError(f"Kraken OHLC error: {err}")
    result = data.get("result", {})
    pair_keys = [k for k in result if k != "last"]
    if not pair_keys:
        return []
    rows = result[pair_keys[0]]
    candles: list[Candle] = []
    for r in rows:
        # [time, open, high, low, close, vwap, volume, count]
        candles.append(Candle(
            ts=int(r[0]),
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=float(r[6]),
        ))
    return candles


def select_since(candles: list[Candle], since_ts: int) -> list[Candle]:
    """Return candles strictly newer than since_ts (for incremental polling)."""
    return [c for c in candles if c.ts > since_ts]


class SessionVwap:
    """Cumulative volume-weighted (O+H+L+C)/4, anchored at the session start.

    Holds only running sums so a restart can resume exactly from persisted
    `sum_pv`/`sum_v`/`last_ts` (see persistence). `last_ts` is the newest candle
    open-time accumulated, used by the live poller to dedupe.
    """

    def __init__(self):
        self.sum_pv: float = 0.0   # Σ ohlc4 * volume
        self.sum_v: float = 0.0    # Σ volume
        self.count: int = 0
        self.last_ts: int = 0

    def reset(self):
        self.sum_pv = 0.0
        self.sum_v = 0.0
        self.count = 0
        self.last_ts = 0

    def add_candle(self, candle: Candle):
        self.sum_pv += candle.ohlc4 * candle.volume
        self.sum_v += candle.volume
        self.count += 1
        if candle.ts > self.last_ts:
            self.last_ts = candle.ts

    def value(self) -> float | None:
        if self.sum_v <= 0.0:
            return None
        return self.sum_pv / self.sum_v


def save_session(path: str, session: SessionVwap, utc_date: str):
    """Persist the session VWAP sums (atomic). `utc_date` stamps the file so a
    stale prior-day file can be discarded on load."""
    atomic_write_json(path, {
        "utc_date": utc_date,
        "sum_pv": session.sum_pv,
        "sum_v": session.sum_v,
        "count": session.count,
        "last_ts": session.last_ts,
    })


def load_session(path: str, today_utc_date: str) -> SessionVwap | None:
    """Load a persisted session VWAP. Returns None if the file is missing or
    its UTC date != today (staleness guard — never resume a prior day's sums)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    if data.get("utc_date") != today_utc_date:
        return None
    s = SessionVwap()
    s.sum_pv = float(data.get("sum_pv", 0.0))
    s.sum_v = float(data.get("sum_v", 0.0))
    s.count = int(data.get("count", 0))
    s.last_ts = int(data.get("last_ts", 0))
    return s


# ── T12  Async driver ────────────────────────────────────────────────────────

def session_start_utc(now: float) -> int:
    """00:00 UTC of the current day (a multiple of 900 and 86400)."""
    return int(now // 86400) * 86400


def today_utc_str(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


class VwapEngine:
    """Per-(asset, timeframe) session VWAP driver.

    `provider(interval, since)->list[Candle]` isolates Kraken HTTP. warm_start
    does a single fetch at this timeframe's interval (interval=5 reaches ~60h,
    interval=15 reaches ~180h — both span 00:00 UTC), seeding the session from
    the day's closed candles and flagging vwap_reconstructed. poll_once feeds
    newly closed candles, capturing vwap_prev (pre-add) per close and clearing
    the reconstructed flag after the first live candle.
    """

    def __init__(self, asset: str, timeframe: str, pair: str, config, provider,
                 now_fn=time.time):
        self.asset = asset
        self.timeframe = timeframe
        self.pair = pair
        self.cfg = config
        self._provider = provider
        self._now = now_fn
        interval_min, window_sec = config.timeframes[timeframe]
        self.interval_min = interval_min
        self.window_sec = window_sec
        self._interval_sec = interval_min * 60
        self.session = SessionVwap()
        self.latest_close: float | None = None
        self.vwap_reconstructed = False
        # closed candles kept by open-time so resolution can settle a window on
        # its OWN open→close (not a stale latest_close). Bounded in _remember.
        self.closed_candles: dict[int, Candle] = {}
        # UTC-day anchor of the current session; a candle on a later day resets it.
        self._session_day: int | None = None

    @property
    def stream_key(self) -> str:
        return f"{self.asset} {self.timeframe}"

    # path for today's candle file
    def _candle_path(self) -> str:
        date = today_utc_str(self._now())
        name = self.cfg.candle_file_template.format(
            asset=self.asset, timeframe=self.timeframe, date=date)
        return os.path.join(self.cfg.candle_dir, name)

    async def _fetch(self, since: int | None):
        res = self._provider(self.interval_min, since)
        if asyncio.iscoroutine(res):
            res = await res
        return res

    async def warm_start(self):
        now = self._now()
        ss = session_start_utc(now)
        today = today_utc_str(now)
        path = self._candle_path()

        resumed = load_session(path, today)
        if resumed is not None:
            self.session = resumed
            self.vwap_reconstructed = False
            self._session_day = ss
            recent = await self._fetch(None)
            for c in recent:
                if c.ts + self._interval_sec <= now:
                    self._remember(c)   # buffer recent closed candles for resolution
            if recent:
                self.latest_close = recent[-1].close
            logger.info(f"[{self.stream_key}] resumed session VWAP from {path} "
                        f"(vwap={self._fmt(self.session.value())})")
            return

        candles = await self._fetch(None)
        self.session = SessionVwap()
        self._session_day = ss
        for c in candles:
            if c.ts + self._interval_sec > now:
                continue   # in-progress candle — not closed yet
            self._remember(c)
            if c.ts < ss:
                continue   # older than today's session — buffered but not summed
            self.session.add_candle(c)
        if candles:
            self.latest_close = candles[-1].close
        self.vwap_reconstructed = True
        logger.warning(f"[{self.stream_key}] reconstructed session VWAP "
                       f"(candles={len(candles)} vwap={self._fmt(self.session.value())}) "
                       f"— no LIVE entries until clean")
        self._persist(today)

    async def poll_once(self) -> list[CandleClose]:
        """Add any newly *closed* candles; return one CandleClose per add with
        the pre-add VWAP. Clears vwap_reconstructed after the first live candle."""
        now = self._now()
        new = await self._fetch(self.session.last_ts)
        out: list[CandleClose] = []
        for c in new:
            if c.ts <= self.session.last_ts:
                continue
            if c.ts + self._interval_sec > now:
                continue  # candle still in progress — not closed yet
            cday = session_start_utc(c.ts)
            if self._session_day is None:
                self._session_day = cday
            elif cday > self._session_day:
                # crossed 00:00 UTC — start a fresh daily session VWAP
                self.session.reset()
                self._session_day = cday
            vwap_prev = self.session.value()
            was_recon = self.vwap_reconstructed
            self.session.add_candle(c)
            self.latest_close = c.close
            self._remember(c)
            self.vwap_reconstructed = False   # first live candle clears the flag
            out.append(CandleClose(candle=c, vwap_prev=vwap_prev,
                                   was_reconstructed=was_recon))
        if out:
            self._persist(today_utc_str(now))
        return out

    def _persist(self, today: str):
        try:
            save_session(self._candle_path(), self.session, today)
        except Exception as e:
            logger.warning(f"[{self.stream_key}] persist failed: {e}")

    def vwap(self) -> float | None:
        return self.session.value()

    def candle_at(self, ts: int) -> "Candle | None":
        """The closed candle whose open-time == ts (a window's own candle),
        used at resolution to settle on that window's real open→close."""
        return self.closed_candles.get(int(ts))

    def _remember(self, c: Candle):
        """Retain a closed candle for later resolution; keep the buffer bounded."""
        self.closed_candles[c.ts] = c
        if len(self.closed_candles) > 3000:
            for k in sorted(self.closed_candles)[:1000]:
                del self.closed_candles[k]

    @staticmethod
    def _fmt(v):
        return f"{v:.2f}" if v is not None else "n/a"


def kraken_ohlc_provider(pair: str):
    """Production provider: returns an async callable(interval, since)->[Candle]
    hitting Kraken public OHLC. Imported lazily so unit tests need no network."""
    import aiohttp

    async def provider(interval: int, since: int | None):
        url = "https://api.kraken.com/0/public/OHLC"
        params = {"pair": pair, "interval": str(interval)}
        if since:
            params["since"] = str(int(since))
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params,
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
        return parse_ohlc(data)

    return provider
