"""robots.txt awareness (RFC 9309)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

# Returns (status_code, body). Raises on network errors.
RobotsFetch = Callable[[str], tuple[int, str]]


class RobotsCache:
    """Fetches and caches robots.txt per origin.

    Per RFC 9309: a 4xx means no restrictions, a 5xx or unreachable file means
    the whole site is treated as disallowed until the next refresh.
    """

    def __init__(self, fetch: RobotsFetch, user_agent: str, ttl_seconds: float = 3600.0):
        self._fetch = fetch
        self.user_agent = user_agent
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple[float, RobotFileParser]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    def _parser_for(self, url: str) -> RobotFileParser:
        origin = self.origin(url)
        with self._lock:
            cached = self._cache.get(origin)
            if cached and time.monotonic() - cached[0] < self.ttl:
                return cached[1]

        parser = RobotFileParser()
        try:
            status, body = self._fetch(f"{origin}/robots.txt")
        except Exception:  # noqa: BLE001 - unreachable robots.txt
            status, body = 503, ""
        if status >= 500:
            parser.disallow_all = True
        elif status >= 400:
            parser.allow_all = True
        else:
            parser.parse(body.splitlines())
        parser.modified()  # mark as read so can_fetch() evaluates the rules

        with self._lock:
            self._cache[origin] = (time.monotonic(), parser)
        return parser

    def allowed(self, url: str) -> bool:
        return self._parser_for(url).can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        delay = self._parser_for(url).crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None
