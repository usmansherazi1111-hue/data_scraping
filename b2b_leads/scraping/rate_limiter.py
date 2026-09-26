"""Per-domain request pacing with jitter."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable


class DomainRateLimiter:
    """Keeps a minimum, slightly randomized gap between requests to one domain.

    The gap is ``max(min_delay, crawl_delay) + uniform(0, jitter)``, and a
    domain can be slowed down further after a 429/503 with :meth:`penalize`.
    """

    def __init__(
        self,
        min_delay: float = 2.0,
        jitter: float = 1.5,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_delay = min_delay
        self.jitter = jitter
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str, crawl_delay: float | None = None) -> float:
        """Block until a request to ``domain`` is allowed. Returns seconds waited."""
        delay = max(self.min_delay, crawl_delay or 0.0) + random.uniform(0, self.jitter)
        with self._lock:
            now = self._clock()
            start_at = max(now, self._next_allowed.get(domain, now))
            self._next_allowed[domain] = start_at + delay
        waited = start_at - now
        if waited > 0:
            self._sleep(waited)
        return waited

    def penalize(self, domain: str, seconds: float) -> None:
        """Push the next allowed request for ``domain`` at least ``seconds`` out."""
        with self._lock:
            target = self._clock() + seconds
            self._next_allowed[domain] = max(self._next_allowed.get(domain, 0.0), target)
