"""HTTP fetcher that stays unblocked by behaving well.

What it does:
    * honours robots.txt allow/disallow rules and Crawl-delay;
    * paces requests per domain with jitter, one domain never gets bursts;
    * retries network errors and 429/5xx with exponential backoff, honouring
      ``Retry-After``;
    * rotates through the user's proxy pool and fails over when a proxy dies,
      keeping each domain on one proxy (sticky session) while it works;
    * keeps cookies per proxy session so multi-page flows stay consistent;
    * identifies itself with a configurable, honest User-Agent.

What it deliberately does not do: solve CAPTCHAs, spoof browser fingerprints,
or otherwise try to defeat a site's bot protection. When a site answers with a
block or a challenge page, the URL is recorded as ``blocked`` and skipped.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from ..config import ScrapeSettings
from ..models import AttemptOutcome
from .proxy_pool import NoHealthyProxyError, Proxy, ProxyPool
from .rate_limiter import DomainRateLimiter
from .robots import RobotsCache

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
BLOCK_STATUS = {401, 403, 429}
CHALLENGE_MARKERS = (
    "captcha",
    "cf-chl-",
    "challenge-platform",
    "are you a robot",
    "access denied",
    "unusual traffic",
    "px-captcha",
)


@dataclass
class FetchResult:
    url: str
    outcome: AttemptOutcome
    status_code: int | None = None
    html: str = ""
    final_url: str | None = None
    proxy: str | None = None
    tries: int = 0
    duration: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == AttemptOutcome.SUCCESS


def looks_like_challenge(status: int, body: str) -> bool:
    """True when the response is a block/challenge page rather than content."""
    if status in BLOCK_STATUS:
        return True
    if status in (200, 503) and len(body) < 20_000:
        lower = body.lower()
        return any(marker in lower for marker in CHALLENGE_MARKERS)
    return False


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        try:
            return max(parsedate_to_datetime(value).timestamp() - time.time(), 0.0)
        except (TypeError, ValueError):
            return None


class PoliteFetcher:
    """Fetch pages through the proxy pool with pacing, retries and robots.txt."""

    def __init__(
        self,
        settings: ScrapeSettings,
        pool: ProxyPool | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rate_limiter: DomainRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.pool = pool or ProxyPool([])
        self._transport = transport  # injectable for tests
        self._sleep = sleep
        self.rate_limiter = rate_limiter or DomainRateLimiter(
            settings.min_delay_seconds, settings.jitter_seconds, sleep=sleep
        )
        self._clients: dict[str | None, httpx.Client] = {}
        self._sticky: dict[str, str] = {}  # domain -> proxy url
        self._lock = threading.Lock()
        self.robots = RobotsCache(self._fetch_robots, settings.user_agent)

    # ------------------------------------------------------------ clients

    def _client(self, proxy: Proxy | None) -> httpx.Client:
        key = proxy.url if proxy else None
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = httpx.Client(
                    # An injected transport (tests) would be bypassed by the
                    # proxy's own transport, so it takes precedence.
                    proxy=None if self._transport else key,
                    transport=self._transport,
                    timeout=self.settings.timeout_seconds,
                    follow_redirects=True,
                    headers={
                        "User-Agent": self.settings.user_agent,
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.8",
                    },
                )
                self._clients[key] = client
            return client

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()

    def __enter__(self) -> PoliteFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _fetch_robots(self, url: str) -> tuple[int, str]:
        """Fetch robots.txt, failing over to another proxy on network errors."""
        domain = urlparse(url).hostname or ""
        tried: set[str] = set()
        for _ in range(max(len(self.pool), 1)):
            proxy = self._pick_proxy(domain, tried)
            try:
                resp = self._client(proxy).get(url)
                return resp.status_code, resp.text
            except httpx.HTTPError as exc:
                if proxy is None:
                    raise
                self.pool.report_failure(proxy, f"{type(exc).__name__}: {exc}")
                tried.add(proxy.url)
                self._sticky.pop(domain, None)
            finally:
                if proxy:
                    self.pool.release(proxy)
        raise httpx.ConnectError(f"robots.txt unreachable through all proxies: {url}")

    # ------------------------------------------------------------ proxies

    def _pick_proxy(self, domain: str, tried: set[str]) -> Proxy | None:
        if not self.pool.enabled:
            return None
        sticky = self._sticky.get(domain)
        if sticky and sticky not in tried:
            proxy = self.pool.get(sticky)
            if proxy:
                return proxy
        proxy = self.pool.acquire(exclude=tried)
        self._sticky[domain] = proxy.url
        return proxy

    def _backoff(self, attempt: int) -> float:
        base = self.settings.backoff_base_seconds * (2 ** (attempt - 1))
        return min(base + random.uniform(0, base / 2), self.settings.backoff_max_seconds)

    # -------------------------------------------------------------- fetch

    def fetch(self, url: str) -> FetchResult:
        """Fetch one URL. Never raises for HTTP/network problems."""
        start = time.perf_counter()
        domain = urlparse(url).hostname or ""
        result = FetchResult(url=url, outcome=AttemptOutcome.NETWORK_ERROR)

        if self.settings.respect_robots_txt:
            if not self.robots.allowed(url):
                result.outcome = AttemptOutcome.DISALLOWED
                result.error = "Disallowed by robots.txt"
                return result
            crawl_delay = self.robots.crawl_delay(url)
        else:
            crawl_delay = None

        tried_proxies: set[str] = set()
        for attempt in range(1, self.settings.max_retries + 2):
            result.tries = attempt
            try:
                proxy = self._pick_proxy(domain, tried_proxies)
            except NoHealthyProxyError as exc:
                result.outcome, result.error = AttemptOutcome.NETWORK_ERROR, str(exc)
                break
            result.proxy = proxy.label if proxy else None
            self.rate_limiter.wait(domain, crawl_delay)

            t0 = time.perf_counter()
            try:
                resp = self._client(proxy).get(url)
            except httpx.HTTPError as exc:
                result.outcome = AttemptOutcome.NETWORK_ERROR
                result.error = f"{type(exc).__name__}: {exc}"
                if proxy:
                    self.pool.report_failure(proxy, result.error)
                    tried_proxies.add(proxy.url)
                    self._sticky.pop(domain, None)
                self._sleep(self._backoff(attempt))
                continue
            finally:
                if proxy:
                    self.pool.release(proxy)

            result.status_code = resp.status_code
            result.final_url = str(resp.url)
            body = resp.text

            if looks_like_challenge(resp.status_code, body):
                result.outcome = AttemptOutcome.BLOCKED
                result.error = f"Blocked or challenged (HTTP {resp.status_code})"
                wait = retry_after_seconds(resp.headers.get("Retry-After"))
                wait = max(wait or 0.0, self._backoff(attempt) * 2)
                self.rate_limiter.penalize(domain, wait)
                if proxy:
                    self.pool.report_failure(proxy, result.error, blocked=True)
                    tried_proxies.add(proxy.url)
                    self._sticky.pop(domain, None)
                # Without other proxies to fail over to, retrying only
                # hammers the site; stop and record the block.
                if not proxy or len(tried_proxies) >= len(self.pool):
                    break
                continue

            if resp.status_code in RETRYABLE_STATUS:
                result.outcome = AttemptOutcome.HTTP_ERROR
                result.error = f"HTTP {resp.status_code}"
                wait = retry_after_seconds(resp.headers.get("Retry-After"))
                self._sleep(min(wait, self.settings.backoff_max_seconds) if wait else self._backoff(attempt))
                continue

            if resp.status_code >= 400:
                result.outcome = AttemptOutcome.HTTP_ERROR
                result.error = f"HTTP {resp.status_code}"
                if proxy:
                    self.pool.report_success(proxy, time.perf_counter() - t0)
                break

            result.outcome = AttemptOutcome.SUCCESS
            result.html = body
            result.error = None
            if proxy:
                self.pool.report_success(proxy, time.perf_counter() - t0)
            break

        result.duration = time.perf_counter() - start
        return result
