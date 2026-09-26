"""Polite, reliable scraping: proxy pool, rate limiting, robots.txt and extraction."""

from .fetcher import FetchResult, PoliteFetcher
from .proxy_pool import Proxy, ProxyPool
from .rate_limiter import DomainRateLimiter
from .robots import RobotsCache

__all__ = [
    "DomainRateLimiter",
    "FetchResult",
    "PoliteFetcher",
    "Proxy",
    "ProxyPool",
    "RobotsCache",
]
