"""Command line entry point: ``python -m b2b_leads <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import crm, kpi
from .app_context import AppContext
from .db import session_scope
from .export import read_leads_csv, write_contacts_csv
from .pipeline import create_job, run_job
from .scraping.extractors import make_extractor


def _urls(args: argparse.Namespace) -> list[str]:
    urls = list(args.urls or [])
    if args.url_file:
        urls += [
            line.strip()
            for line in Path(args.url_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return urls


def cmd_scrape(ctx: AppContext, args: argparse.Namespace) -> int:
    urls = _urls(args)
    if not urls:
        print("No URLs given (use positional URLs or --url-file)", file=sys.stderr)
        return 2
    extractor = make_extractor(args.extractor, ctx.settings.llm)
    with session_scope(ctx.sessions) as session:
        job = create_job(
            session,
            name=args.name,
            prompt=args.prompt or "Extract company info and publicly listed business contacts.",
            urls=urls,
            extractor=args.extractor,
        )
        job_id = job.id
    if ctx.pool.enabled:
        print("Health-checking proxies...", json.dumps(ctx.pool.check_all()))
    job = run_job(
        ctx.sessions,
        job_id,
        urls,
        fetcher=ctx.fetcher,
        extractor=extractor,
        exports_dir=ctx.settings.exports_dir,
        import_to_crm=not args.no_crm,
        max_concurrent_domains=ctx.settings.scrape.max_concurrent_domains,
    )
    with session_scope(ctx.sessions) as session:
        stats = kpi.scrape_kpis(session, job_id)
    print(json.dumps({"job": job_id, "status": job.status.value, "csv": job.output_csv, **stats}, indent=2))
    return 0 if job.status.value == "completed" else 1


def cmd_import(ctx: AppContext, args: argparse.Namespace) -> int:
    records = read_leads_csv(Path(args.csv))
    with session_scope(ctx.sessions) as session:
        created = sum(1 for r in records if crm.import_lead(session, r, source=args.source))
    print(f"Imported {len(records)} rows ({created} contacts)")
    return 0


def cmd_export(ctx: AppContext, args: argparse.Namespace) -> int:
    with session_scope(ctx.sessions) as session:
        n = write_contacts_csv(session, Path(args.out), include_opted_out=args.include_opted_out)
    print(f"Wrote {n} contacts to {args.out}")
    return 0


def cmd_kpi(ctx: AppContext, args: argparse.Namespace) -> int:
    with session_scope(ctx.sessions) as session:
        print(json.dumps(kpi.dashboard(session, ctx.pool.stats()), indent=2))
    return 0


def cmd_check_proxies(ctx: AppContext, args: argparse.Namespace) -> int:
    if not ctx.pool.enabled:
        print("No proxies configured (set B2B_LEADS_PROXIES or proxy.urls)")
        return 1
    ctx.pool.check_all()
    print(json.dumps(ctx.pool.stats(), indent=2))
    return 0


def cmd_serve(ctx: AppContext, args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(ctx), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="b2b_leads", description=__doc__)
    parser.add_argument("--config", help="JSON config file (default: $B2B_LEADS_CONFIG)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the API and dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("scrape", help="scrape URLs into a CSV (and the CRM)")
    p.add_argument("urls", nargs="*")
    p.add_argument("--url-file")
    p.add_argument("--name", default="CLI scrape")
    p.add_argument("--prompt")
    p.add_argument("--extractor", choices=["scrapegraph", "basic"], default="scrapegraph")
    p.add_argument("--no-crm", action="store_true", help="only write the CSV")
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("import", help="import a lead CSV, e.g. from a licensed data provider")
    p.add_argument("csv")
    p.add_argument("--source", default="import")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export", help="export CRM contacts to CSV")
    p.add_argument("out")
    p.add_argument("--include-opted-out", action="store_true")
    p.set_defaults(func=cmd_export)

    sub.add_parser("kpi", help="print KPIs as JSON").set_defaults(func=cmd_kpi)
    sub.add_parser("check-proxies", help="health-check the proxy pool").set_defaults(
        func=cmd_check_proxies
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    ctx = AppContext.build(args.config)
    try:
        return int(args.func(ctx, args))
    finally:
        ctx.fetcher.close()


if __name__ == "__main__":
    raise SystemExit(main())
