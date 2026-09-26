from __future__ import annotations

import csv

import httpx
import pytest

from b2b_leads import kpi
from b2b_leads.config import ScrapeSettings
from b2b_leads.db import session_scope
from b2b_leads.models import AttemptOutcome
from b2b_leads.pipeline import create_job, run_job
from b2b_leads.schemas import LEAD_CSV_COLUMNS
from b2b_leads.scraping.extractors import BasicExtractor, leads_from_payload
from b2b_leads.scraping.fetcher import looks_like_challenge, retry_after_seconds
from b2b_leads.scraping.proxy_pool import NoHealthyProxyError, ProxyPool
from b2b_leads.scraping.rate_limiter import DomainRateLimiter

from .conftest import ACME_HTML, make_fetcher

SETTINGS = ScrapeSettings(min_delay_seconds=0, jitter_seconds=0, backoff_base_seconds=0)


# ------------------------------------------------------------------ proxy pool


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_round_robin_rotates():
    pool = ProxyPool(["http://a:1", "http://b:1", "http://c:1"])
    picks = []
    for _ in range(4):
        p = pool.acquire()
        picks.append(p.url)
        pool.release(p)
    assert picks == ["http://a:1", "http://b:1", "http://c:1", "http://a:1"]


def test_failover_and_cooldown():
    clock = FakeClock()
    pool = ProxyPool(["http://a:1", "http://b:1"], failure_threshold=2, cooldown_seconds=60, clock=clock)
    a = pool.proxies[0]
    pool.report_failure(a, "timeout")
    pool.report_failure(a, "timeout")
    # a is cooling down, so every pick fails over to b
    assert {pool.acquire().url for _ in range(3)} == {"http://b:1"}
    clock.t = 61
    assert "http://a:1" in {pool.acquire().url for _ in range(2)}


def test_block_counts_double_and_cooldown_grows():
    clock = FakeClock()
    pool = ProxyPool(["http://a:1"], failure_threshold=2, cooldown_seconds=10, clock=clock)
    a = pool.proxies[0]
    pool.report_failure(a, "403", blocked=True)
    with pytest.raises(NoHealthyProxyError):
        pool.acquire()
    clock.t = 11
    pool.report_failure(a, "403", blocked=True)
    assert a.cooldown_until == pytest.approx(11 + 20)  # doubled on second trip


def test_label_hides_credentials():
    pool = ProxyPool(["http://user:secret@gw.example:8000"])
    assert pool.proxies[0].label == "http://gw.example:8000"
    assert "secret" not in str(pool.stats())


def test_health_check_marks_unhealthy(monkeypatch):
    pool = ProxyPool(["http://a:1"])

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "get", boom)
    assert pool.check_all() == {"http://a:1": False}
    with pytest.raises(NoHealthyProxyError):
        pool.acquire()


# ------------------------------------------------------------ rate limiting


def test_rate_limiter_spaces_requests_per_domain():
    clock = FakeClock()
    slept = []

    def sleep(s):
        slept.append(s)
        clock.t += s

    rl = DomainRateLimiter(2.0, 0.0, clock=clock, sleep=sleep)
    rl.wait("a.test")
    rl.wait("a.test")
    rl.wait("b.test")  # other domain is not delayed
    assert slept == [2.0]
    rl.wait("a.test", crawl_delay=5)
    assert slept[-1] == pytest.approx(2.0)


# ------------------------------------------------------------------ fetcher


def test_robots_disallow_and_success():
    f = make_fetcher(SETTINGS)
    assert f.fetch("https://acme.test/private/page").outcome == AttemptOutcome.DISALLOWED
    ok = f.fetch("https://acme.test/about")
    assert ok.ok and "Acme" in ok.html


def test_robots_5xx_means_disallow_all():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(503)
        return httpx.Response(200, html="hi")

    assert make_fetcher(SETTINGS, handler).fetch("https://x.test/").outcome == AttemptOutcome.DISALLOWED


def test_retries_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(502) if calls["n"] < 3 else httpx.Response(200, html=ACME_HTML)

    result = make_fetcher(SETTINGS, handler).fetch("https://acme.test/")
    assert result.ok and result.tries == 3


def test_block_without_proxies_is_recorded_not_retried():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(200, html="<p>Please complete the CAPTCHA</p>")

    result = make_fetcher(SETTINGS, handler).fetch("https://acme.test/")
    assert result.outcome == AttemptOutcome.BLOCKED
    assert calls["n"] == 1


def test_block_fails_over_to_next_proxy():
    pool = ProxyPool(["http://p1:1", "http://p2:1"])
    seen = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(len(seen))
        return httpx.Response(403) if len(seen) == 1 else httpx.Response(200, html="ok")

    result = make_fetcher(SETTINGS, handler, pool).fetch("https://acme.test/")
    assert result.ok and result.tries == 2
    assert pool.proxies[0].blocks == 1


def test_challenge_detection_and_retry_after():
    assert looks_like_challenge(429, "")
    assert looks_like_challenge(200, "<div id='cf-chl-widget'>")
    assert not looks_like_challenge(200, "<h1>Welcome</h1>")
    assert retry_after_seconds("7") == 7
    assert retry_after_seconds(None) is None


# --------------------------------------------------------------- extractors


def test_basic_extractor():
    rows = BasicExtractor().extract(ACME_HTML, "https://www.acme.test/", "")
    assert {r.email for r in rows} == {"sales@acme.test", "jane@acme.test"}
    assert rows[0].company_name == "Acme Corp"
    assert rows[0].company_domain == "acme.test"
    assert rows[0].phone == "+1 555 0100"


def test_leads_from_llm_payload():
    payload = {
        "content": {
            "company": {"name": "Acme", "website": "https://acme.test", "industry": "Widgets"},
            "contacts": [
                {"full_name": "Jane Doe", "title": "CEO", "email": "JANE@acme.test"},
                {"full_name": None},
            ],
        }
    }
    rows = leads_from_payload(payload, "https://acme.test/team")
    assert len(rows) == 1
    assert rows[0].email == "jane@acme.test" and rows[0].industry == "Widgets"
    assert leads_from_payload("garbage", "https://x.test") == []


# ------------------------------------------------------------------ pipeline


def test_pipeline_writes_csv_and_imports(ctx):
    urls = ["https://acme.test/", "https://acme.test/private/x", "https://blocked.test/"]
    with session_scope(ctx.sessions) as s:
        job_id = create_job(s, name="t", prompt="p", urls=urls, extractor="basic").id

    job = run_job(
        ctx.sessions, job_id, urls, fetcher=ctx.fetcher, extractor=BasicExtractor(),
        exports_dir=ctx.settings.exports_dir,
    )
    assert job.status.value == "completed"
    assert job.succeeded == 1 and job.failed == 2 and job.records == 2

    with open(job.output_csv, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == LEAD_CSV_COLUMNS
        assert {r["email"] for r in reader} == {"sales@acme.test", "jane@acme.test"}

    with session_scope(ctx.sessions) as s:
        k = kpi.scrape_kpis(s, job_id)
        assert k["by_outcome"]["success"] == 1
        assert k["by_outcome"]["blocked"] == 1
        assert k["by_outcome"]["disallowed"] == 1
        assert kpi.pipeline_kpis(s)["contacts"] == 2
