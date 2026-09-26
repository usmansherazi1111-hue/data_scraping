"""Proxy pool with rotation, health checks, cooldown and failover.

Proxies come from providers the user has an account with (residential,
datacenter, ISP...). Each entry is a normal proxy URL, e.g.
``http://user:pass@gw.provider.example:8000``.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx


class NoHealthyProxyError(RuntimeError):
    """Every proxy in the pool is cooling down or unhealthy."""


@dataclass
class Proxy:
    url: str
    successes: int = 0
    failures: int = 0
    blocks: int = 0
    consecutive_failures: int = 0
    in_use: int = 0
    cooldown_until: float = 0.0
    healthy: bool = True
    last_latency: float | None = None
    last_checked: float | None = None
    last_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Proxy identity with credentials removed, safe for logs and the UI."""
        parsed = urlparse(self.url)
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ''}".rstrip(":")

    def available(self, now: float) -> bool:
        return self.healthy and now >= self.cooldown_until

    def stats(self, now: float) -> dict[str, Any]:
        total = self.successes + self.failures
        return {
            "proxy": self.label,
            "healthy": self.healthy,
            "cooling_down": now < self.cooldown_until,
            "successes": self.successes,
            "failures": self.failures,
            "blocks": self.blocks,
            "success_rate": round(100.0 * self.successes / total, 1) if total else None,
            "last_latency_ms": round(self.last_latency * 1000) if self.last_latency else None,
            "last_error": self.last_error,
        }


class ProxyPool:
    """Thread-safe proxy rotation.

    Strategies:
        round_robin: cycle through available proxies.
        random: pick uniformly at random.
        least_used: prefer the proxy with the fewest in-flight requests,
            then the best success rate.

    A proxy that fails ``failure_threshold`` times in a row is put on cooldown
    for ``cooldown_seconds`` (doubling for repeated trips, capped at 1h) and
    traffic fails over to the rest of the pool.
    """

    def __init__(
        self,
        urls: list[str],
        *,
        strategy: str = "round_robin",
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        health_check_url: str = "https://httpbin.org/ip",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if strategy not in {"round_robin", "random", "least_used"}:
            raise ValueError(f"Unknown proxy strategy {strategy!r}")
        self.proxies = [Proxy(u.strip()) for u in dict.fromkeys(urls) if u.strip()]
        self.strategy = strategy
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.health_check_url = health_check_url
        self._clock = clock
        self._lock = threading.Lock()
        self._rr_index = 0
        self._trips: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self.proxies)

    @property
    def enabled(self) -> bool:
        return bool(self.proxies)

    def acquire(self, exclude: set[str] | None = None) -> Proxy:
        """Pick the next proxy, skipping unhealthy, cooling and excluded ones.

        Raises:
            NoHealthyProxyError: if nothing is available.
        """
        exclude = exclude or set()
        with self._lock:
            now = self._clock()
            candidates = [
                p for p in self.proxies if p.available(now) and p.url not in exclude
            ]
            if not candidates:
                raise NoHealthyProxyError("No healthy proxy available")
            if self.strategy == "random":
                proxy = random.choice(candidates)
            elif self.strategy == "least_used":
                proxy = min(
                    candidates,
                    key=lambda p: (p.in_use, p.consecutive_failures, -p.successes),
                )
            else:
                ordered = self.proxies[self._rr_index :] + self.proxies[: self._rr_index]
                proxy = next(p for p in ordered if p in candidates)
                self._rr_index = (self.proxies.index(proxy) + 1) % len(self.proxies)
            proxy.in_use += 1
            return proxy

    def get(self, url: str) -> Proxy | None:
        """Return the proxy with this URL if it is currently usable."""
        with self._lock:
            now = self._clock()
            for p in self.proxies:
                if p.url == url and p.available(now):
                    p.in_use += 1
                    return p
        return None

    def release(self, proxy: Proxy) -> None:
        with self._lock:
            proxy.in_use = max(proxy.in_use - 1, 0)

    def report_success(self, proxy: Proxy, latency: float | None = None) -> None:
        with self._lock:
            proxy.successes += 1
            proxy.consecutive_failures = 0
            proxy.last_error = None
            self._trips.pop(proxy.url, None)
            if latency is not None:
                proxy.last_latency = latency

    def report_failure(self, proxy: Proxy, error: str, *, blocked: bool = False) -> None:
        """Record a failure. Blocks count double towards the cooldown threshold."""
        with self._lock:
            proxy.failures += 1
            proxy.consecutive_failures += 2 if blocked else 1
            proxy.last_error = error
            if blocked:
                proxy.blocks += 1
            if proxy.consecutive_failures >= self.failure_threshold:
                trips = self._trips.get(proxy.url, 0)
                self._trips[proxy.url] = trips + 1
                proxy.cooldown_until = self._clock() + min(
                    self.cooldown_seconds * (2**trips), 3600.0
                )
                proxy.consecutive_failures = 0

    # ---------------------------------------------------------- health checks

    def check(self, proxy: Proxy, timeout: float = 10.0) -> bool:
        """Probe one proxy against ``health_check_url``."""
        start = time.perf_counter()
        try:
            with httpx.Client(proxy=proxy.url, timeout=timeout) as client:
                resp = client.get(self.health_check_url)
            ok = resp.status_code < 400
            error = None if ok else f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"
        with self._lock:
            proxy.healthy = ok
            proxy.last_checked = self._clock()
            proxy.last_error = error
            if ok:
                proxy.last_latency = time.perf_counter() - start
                proxy.cooldown_until = 0.0
        return ok

    def check_all(self, timeout: float = 10.0) -> dict[str, bool]:
        """Probe every proxy in parallel. Unhealthy ones are taken out of rotation."""
        results: dict[str, bool] = {}
        threads = []

        def run(p: Proxy) -> None:
            results[p.label] = self.check(p, timeout)

        for p in self.proxies:
            t = threading.Thread(target=run, args=(p,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return results

    def start_background_checks(self, interval: float) -> threading.Event:
        """Re-check all proxies every ``interval`` seconds. Set the event to stop."""
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(interval):
                self.check_all()

        threading.Thread(target=loop, daemon=True, name="proxy-health").start()
        return stop

    def stats(self) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            return [p.stats(now) for p in self.proxies]
