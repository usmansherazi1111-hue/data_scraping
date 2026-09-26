# B2B Leads

Campaign management, a built-in CRM, KPI dashboard and a scraping pipeline that
exports structured CSV, built on top of ScrapeGraphAI.

This folder is a standalone app. It uses the `scrapegraphai` package from this
repo as its extraction engine and does not change the library itself.

## Features

| Area | What you get |
| --- | --- |
| **CRM** | Companies, contacts, pipeline stages (new → contacted → engaged → qualified → proposal → won/lost) with a stage history, notes on contacts and companies, tags and owners, dedupe by email and company domain. |
| **Compliance** | Per-contact data source and lawful basis (GDPR), opt-out with timestamp and reason, do-not-contact flag, unsubscribe link per contact, one-click `List-Unsubscribe` header, mandatory postal address in every email (CAN-SPAM), right-to-erasure that keeps only a suppression entry. Opted-out people are never re-added by a later scrape or included in an audience. |
| **Campaigns** | Email campaigns with `{{first_name}}`, `{{company}}`… templates, audiences by stage or tag, per-campaign rolling 24h send limit, pause/resume, previews, dry-run outbox or SMTP, engagement events (sent, opened, clicked, replied, converted, bounced, unsubscribed). |
| **KPIs** | Open, click, reply, conversion, bounce and unsubscribe rates (overall and per campaign), pipeline by stage and win rate, scrape success, block and robots-disallowed rates, and per-proxy health. |
| **Scraping** | ScrapeGraphAI LLM extraction with a typed schema, or a no-LLM basic extractor. Output is a CSV with fixed columns, deduplicated, optionally imported into the CRM. |
| **Proxies** | A pool of your own provider's proxies with round-robin, random or least-used rotation, health checks, automatic cooldown with growing backoff, failover, and sticky sessions per domain. |
| **Reliability** | robots.txt allow/disallow and Crawl-delay, per-domain pacing with jitter, retries with exponential backoff and `Retry-After`, cookie sessions per proxy, a configurable and honest User-Agent. |

### How "not getting blocked" works here

The fetcher avoids blocks by behaving like a considerate client: slow and
jittered per-domain pacing, honoring robots.txt and `Retry-After`, spreading
load over your proxy pool and backing off when a site pushes back.

It deliberately does **not** solve CAPTCHAs, spoof browser fingerprints or
otherwise defeat bot protection. When a site answers with a block or challenge
page, the URL is recorded as `blocked`, the proxy is rested, and the job moves
on. The block rate shows up in the KPI dashboard so you can tune pacing.

For sites that don't allow scraping, use their official API or a licensed data
provider. Export the provider's data to CSV and load it with
`python -m b2b_leads import provider.csv --source provider`. Columns that match
the lead CSV header are picked up and the rest are ignored.

## Setup

From the repo root, using the existing virtualenv:

```bash
.venv/Scripts/python -m pip install -r b2b_leads/requirements.txt
```

Settings come from `b2b_leads/config.example.json` (copy it and point
`B2B_LEADS_CONFIG` at the copy) plus environment variables. Keep secrets in `.env`:

```
OPENROUTER_API_KEY=...                # LLM for the ScrapeGraphAI extractor
B2B_LEADS_PROXIES=http://user:pass@gw.provider.example:8000,http://...
B2B_LEADS_POSTAL_ADDRESS=Your Co, 1 Example St, City, Country
SMTP_HOST=smtp.example.com            # optional; without it emails go to b2b_leads/data/outbox/
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
```

## Usage

```bash
# Web dashboard + API on http://127.0.0.1:8000 (API docs at /docs)
python -m b2b_leads serve

# Scrape a list of URLs to CSV and the CRM
python -m b2b_leads scrape --url-file urls.txt --name "SaaS in Berlin"
python -m b2b_leads scrape https://example.com/about --extractor basic --no-crm

# Other commands
python -m b2b_leads check-proxies
python -m b2b_leads kpi
python -m b2b_leads export contacts.csv
python -m b2b_leads import provider_export.csv --source provider
```

Scrape output goes to `b2b_leads/data/exports/job_<id>.csv` with the columns
`company_name, company_domain, company_website, industry, company_size,
location, company_description, company_linkedin, first_name, last_name,
full_name, title, email, phone, contact_linkedin, source_url, scraped_at`.

### Tracking engagement

Sending marks recipients as `sent` and moves them to the `contacted` stage.
Opens, replies, conversions and bounces come from:

- `POST /api/campaigns/{id}/events` with `{"contact_id": 1, "event": "replied"}`, which you can
  wire to your email provider's webhook,
- the buttons on the campaign page in the dashboard,
- the open-tracking pixel at `/t/open/{campaign_id}/{token}.gif` for HTML emails.

A reply moves the contact to `engaged` and a conversion to `qualified`.

## Tests

```bash
.venv/Scripts/python -m pytest b2b_leads/tests -q
```

## Layout

```
b2b_leads/
├── api.py            FastAPI app (REST + dashboard)
├── cli.py            command line
├── campaigns.py      audiences, templating, compliant sending, events
├── crm.py            companies, contacts, stages, notes, opt-out, erasure
├── kpi.py            KPI queries
├── pipeline.py       fetch → extract → dedupe → CSV → CRM
├── export.py         CSV import/export
├── models.py         SQLAlchemy models
├── scraping/
│   ├── proxy_pool.py rotation, health checks, cooldown, failover
│   ├── fetcher.py    robots.txt, pacing, retries, block handling
│   ├── rate_limiter.py
│   ├── robots.py
│   └── extractors.py ScrapeGraphAI + basic extractors
└── static/           dashboard (HTML/CSS/JS, no build step)
```
