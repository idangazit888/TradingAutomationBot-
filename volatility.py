"""Rolling 60-min sigma estimator from BTC ticks."""

from collections import deque
from math import log, sqrt
from typing import Optional


class VolatilityEstimator:
    def __init__(self, lookback_seconds: int = 3600):
        self.lookback = lookback_seconds
        self.prices: deque[tuple[float, float]] = deque()
        self.log_returns: deque[float] = deque()
        self._cached_sigma: Optional[float] = None
        self._cache_valid = False

    def update(self, timestamp: float, price: float) -> None:
        if price <= 0:
            return
        if self.prices:
            prev_price = self.prices[-1][1]
            if prev_price > 0:
                self.log_returns.append(log(price / prev_price))
        self.prices.append((timestamp, price))
        cutoff = timestamp - self.lookback
        while self.prices and self.prices[0][0] < cutoff:
            self.prices.popleft()
            if self.log_returns:
                self.log_returns.popleft()
        self._cache_valid = False

    def get_sigma_5min_usd(self) -> Optional[float]:
        if self._cache_valid:
            return self._cached_sigma
        if len(self.prices) < 2:
            return None
        # Tick-rate-agnostic scaling: normalize per-tick sigma to a 5-minute (300s)
        # horizon using the REAL elapsed time spanned by the return buffer.
        # sqrt(300) alone only holds at exactly 1 tick/sec; Binance streams 5-20/sec
        # (under-scaled $5-11 vs true ~$40), Kraken REST polls every 2s (~0.5/sec).
        # 150 returns = ~5 min warmup at Kraken's rate (one full window).
        t_span = self.prices[-1][0] - self.prices[0][0]
        n_returns = len(self.log_returns)
        if t_span <= 0 or n_returns < 150:
            return None
        mean = sum(self.log_returns) / n_returns
        variance = sum((r - mean) ** 2 for r in self.log_returns) / (n_returns - 1)
        sigma_per_tick = sqrt(variance)
        current_price = self.prices[-1][1]
        rate = n_returns / t_span  # actual ticks per second
        self._cached_sigma = current_price * sigma_per_tick * sqrt(300.0 * rate)
        self._cache_valid = True
        return self._cached_sigma

    def get_current_price(self) -> Optional[float]:
        return self.prices[-1][1] if self.prices else None

    def get_sample_count(self) -> int:
        return len(self.prices)


class DailyAverageSigmaTracker:
    def __init__(self):
        self.sigma_samples: list[float] = []
        self.last_update_ts: float = 0
        self._cached_avg: float = 50.0

    def seed(self, sigma_usd: float, weight: int = 10) -> None:
        """Fix 2: pre-populate the running average with a sigma computed from real
        historical (pre-startup) data, so the regime filter is calibrated from
        trade #1 instead of using the blind $50 default.

        The seed is inserted as `weight` identical samples. Live samples arrive
        ~1/min and append normally, so the seed's influence decays over roughly
        `weight` minutes — enough inertia that one noisy early reading can't swing
        the regime band, without permanently pinning the average.
        """
        if not sigma_usd or sigma_usd <= 0:
            return
        self.sigma_samples = [sigma_usd] * max(1, weight)
        self._cached_avg = sigma_usd

    def update(self, timestamp: float, current_sigma_usd: float) -> None:
        if timestamp - self.last_update_ts < 60:
            return
        self.sigma_samples.append(current_sigma_usd)
        if len(self.sigma_samples) > 1440:
            self.sigma_samples.pop(0)
        self.last_update_ts = timestamp
        if self.sigma_samples:
            self._cached_avg = sum(self.sigma_samples) / len(self.sigma_samples)

    def get_average(self) -> float:
        return self._cached_avg
