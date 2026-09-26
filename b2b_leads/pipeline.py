"""Scrape pipeline: fetch -> extract -> dedupe -> CSV (+ optional CRM import)."""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session, sessionmaker

from . import crm
from .db import session_scope
from .export import dedupe, write_leads_csv
from .models import AttemptOutcome, JobStatus, ScrapeAttempt, ScrapeJob, utcnow
from .schemas import LeadRecord
from .scraping.extractors import Extractor
from .scraping.fetcher import FetchResult, PoliteFetcher

log = logging.getLogger(__name__)


def create_job(session: Session, *, name: str, prompt: str, urls: list[str], extractor: str) -> ScrapeJob:
    job = ScrapeJob(name=name, prompt=prompt, extractor=extractor, total_urls=len(urls))
    session.add(job)
    session.flush()
    return job


def _scrape_domain(
    urls: list[str], fetcher: PoliteFetcher, extractor: Extractor, prompt: str
) -> list[tuple[FetchResult, list[LeadRecord]]]:
    """Scrape one domain's URLs sequentially (pacing is per domain)."""
    out = []
    for url in urls:
        result = fetcher.fetch(url)
        records: list[LeadRecord] = []
        if result.ok:
            try:
                records = extractor.extract(result.html, result.final_url or url, prompt)
            except Exception as exc:  # noqa: BLE001 - keep the job going
                log.warning("Extraction failed for %s: %s", url, exc)
                result.outcome = AttemptOutcome.EXTRACT_ERROR
                result.error = f"{type(exc).__name__}: {exc}"
        out.append((result, records))
    return out


def run_job(
    factory: sessionmaker[Session],
    job_id: int,
    urls: list[str],
    *,
    fetcher: PoliteFetcher,
    extractor: Extractor,
    exports_dir: Path,
    import_to_crm: bool = True,
    max_concurrent_domains: int = 4,
) -> ScrapeJob:
    """Run a scrape job to completion and write ``exports_dir/job_<id>.csv``.

    Different domains are scraped in parallel; URLs on the same domain are
    fetched one at a time so the per-domain rate limit holds.
    """
    with session_scope(factory) as session:
        job = session.get(ScrapeJob, job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        prompt = job.prompt

    by_domain: dict[str, list[str]] = defaultdict(list)
    for url in dict.fromkeys(u.strip() for u in urls if u.strip()):
        by_domain[urlparse(url).hostname or url].append(url)

    all_records: list[LeadRecord] = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, max_concurrent_domains)) as pool:
            futures = [
                pool.submit(_scrape_domain, domain_urls, fetcher, extractor, prompt)
                for domain_urls in by_domain.values()
            ]
            with session_scope(factory) as session:
                for future in futures:
                    for result, records in future.result():
                        all_records.extend(records)
                        session.add(
                            ScrapeAttempt(
                                job_id=job_id,
                                url=result.url,
                                outcome=result.outcome,
                                http_status=result.status_code,
                                proxy=result.proxy,
                                tries=result.tries,
                                duration_seconds=round(result.duration, 3),
                                records=len(records),
                                error=result.error,
                            )
                        )

        rows = dedupe(all_records)
        csv_path = exports_dir / f"job_{job_id}.csv"
        write_leads_csv(rows, csv_path)

        with session_scope(factory) as session:
            if import_to_crm:
                for record in rows:
                    crm.import_lead(session, record, source="scrape")
            job = session.get(ScrapeJob, job_id)
            assert job is not None
            job.succeeded = sum(1 for a in job.attempts if a.outcome == AttemptOutcome.SUCCESS)
            job.failed = len(job.attempts) - job.succeeded
            job.records = len(rows)
            job.output_csv = str(csv_path)
            job.status = JobStatus.COMPLETED
            job.finished_at = utcnow()
    except Exception as exc:
        log.exception("Scrape job %s failed", job_id)
        with session_scope(factory) as session:
            job = session.get(ScrapeJob, job_id)
            assert job is not None
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = utcnow()

    with session_scope(factory) as session:
        job = session.get(ScrapeJob, job_id)
        assert job is not None
        return job
