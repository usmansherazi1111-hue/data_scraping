from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from b2b_leads.app_context import AppContext
from b2b_leads.campaigns import OutboxSender
from b2b_leads.config import ScrapeSettings, Settings
from b2b_leads.scraping.fetcher import PoliteFetcher
from b2b_leads.scraping.rate_limiter import DomainRateLimiter

ACME_HTML = """<html><head><title>Acme Corp | Widgets</title>
<meta name="description" content="We make widgets"></head>
<body><a href="mailto:sales@acme.test">Sales</a> Jane: jane@acme.test
<a href="tel:+1 555 0100">call</a>
<a href="https://www.linkedin.com/company/acme">LinkedIn</a></body></html>"""


def default_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    if request.url.host == "blocked.test":
        return httpx.Response(403, text="Access denied")
    return httpx.Response(200, html=ACME_HTML)


def fast_settings(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path, sender_postal_address="1 Main St, Springfield, USA")
    s.scrape = ScrapeSettings(min_delay_seconds=0, jitter_seconds=0, backoff_base_seconds=0)
    s.exports_dir.mkdir(parents=True, exist_ok=True)
    return s


def make_fetcher(
    settings: ScrapeSettings,
    handler: Callable[[httpx.Request], httpx.Response] = default_handler,
    pool=None,
) -> PoliteFetcher:
    noop = lambda _s: None  # noqa: E731
    return PoliteFetcher(
        settings,
        pool,
        transport=httpx.MockTransport(handler),
        sleep=noop,
        rate_limiter=DomainRateLimiter(0, 0, sleep=noop),
    )


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    settings = fast_settings(tmp_path)
    context = AppContext.build(settings=settings)
    context.fetcher = make_fetcher(settings.scrape, pool=context.pool)
    context._sender = OutboxSender(tmp_path / "outbox")
    return context
