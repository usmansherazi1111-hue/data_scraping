"""Turn fetched HTML into :class:`LeadRecord` rows.

Two extractors are provided:

* ``ScrapeGraphExtractor`` uses ScrapeGraphAI's ``SmartScraperGraph`` with an
  LLM and a typed output schema. The page is fetched by :class:`PoliteFetcher`
  first and the HTML is handed to the graph, so pacing, proxies and robots.txt
  apply to every request.
* ``BasicExtractor`` needs no LLM: it reads the page title/meta tags and
  publicly listed ``mailto:``/``tel:`` links and LinkedIn URLs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..crm import normalize_domain
from ..schemas import LeadRecord


class Extractor(Protocol):
    name: str

    def extract(self, html: str, url: str, prompt: str) -> list[LeadRecord]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ basic

MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.I)
EMAIL_TEXT_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TEL_RE = re.compile(r"tel:([+\d\-\s().]{6,})", re.I)
LINKEDIN_COMPANY_RE = re.compile(r"https?://(?:[a-z]+\.)?linkedin\.com/company/[^\"'<>\s?#]+", re.I)
LINKEDIN_PERSON_RE = re.compile(r"https?://(?:[a-z]+\.)?linkedin\.com/in/[^\"'<>\s?#]+", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META_RE = re.compile(
    r"<meta\s+[^>]*(?:name|property)=[\"']([^\"']+)[\"'][^>]*content=[\"']([^\"']*)[\"']",
    re.I,
)
ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")


class BasicExtractor:
    """Heuristic, LLM-free extraction of company info and public contact points."""

    name = "basic"

    def extract(self, html: str, url: str, prompt: str = "") -> list[LeadRecord]:
        meta = {k.lower(): unescape(v) for k, v in META_RE.findall(html)}
        title_match = TITLE_RE.search(html)
        title = unescape(title_match.group(1)).strip() if title_match else None
        company_name = meta.get("og:site_name") or (
            re.split(r"\s[|\-–—]\s", title)[0] if title else None
        )
        domain = normalize_domain(url)

        emails = {m.lower() for m in MAILTO_RE.findall(html)}
        emails |= {
            m.lower()
            for m in EMAIL_TEXT_RE.findall(re.sub(r"<[^>]+>", " ", html))
            if not m.lower().endswith(ASSET_SUFFIXES)
        }
        phones = [re.sub(r"\s+", " ", p).strip() for p in TEL_RE.findall(html)]
        company_li = LINKEDIN_COMPANY_RE.search(html)

        base = {
            "company_name": company_name,
            "company_domain": domain,
            "company_website": f"{urlparse(url).scheme}://{urlparse(url).netloc}",
            "company_description": meta.get("description") or meta.get("og:description"),
            "company_linkedin": company_li.group(0) if company_li else None,
            "phone": phones[0] if phones else None,
            "source_url": url,
            "scraped_at": _now(),
        }
        records = [LeadRecord(**base, email=e) for e in sorted(emails)]
        records += [
            LeadRecord(**base, contact_linkedin=li)
            for li in dict.fromkeys(LINKEDIN_PERSON_RE.findall(html))
        ]
        return records or [LeadRecord(**base)]


# ------------------------------------------------------------ scrapegraph


class ContactOut(BaseModel):
    full_name: str | None = None
    title: str | None = None
    email: str | None = Field(None, description="Business email, only if shown on the page")
    phone: str | None = None
    linkedin_url: str | None = None


class CompanyOut(BaseModel):
    name: str | None = None
    website: str | None = None
    description: str | None = Field(None, description="What the company does, one sentence")
    industry: str | None = None
    size: str | None = Field(None, description="Employee count or range if stated")
    location: str | None = None
    linkedin_url: str | None = None


class PageLeads(BaseModel):
    """Output schema given to the LLM."""

    company: CompanyOut = Field(default_factory=CompanyOut)
    contacts: list[ContactOut] = Field(default_factory=list)


def leads_from_payload(payload: Any, url: str) -> list[LeadRecord]:
    """Normalize the graph output (dict, possibly wrapped in ``content``)."""
    if isinstance(payload, dict) and "content" in payload and "company" not in payload:
        payload = payload["content"]
    if isinstance(payload, list):  # a list of companies
        return [r for item in payload for r in leads_from_payload(item, url)]
    try:
        page = PageLeads.model_validate(payload or {})
    except Exception:  # noqa: BLE001 - LLM output that does not fit the schema
        return []
    c = page.company
    base = {
        "company_name": c.name,
        "company_domain": normalize_domain(c.website or url),
        "company_website": c.website,
        "industry": c.industry,
        "company_size": c.size,
        "location": c.location,
        "company_description": c.description,
        "company_linkedin": c.linkedin_url,
        "source_url": url,
        "scraped_at": _now(),
    }
    records = [
        LeadRecord(
            **base,
            full_name=p.full_name,
            title=p.title,
            email=p.email,
            phone=p.phone,
            contact_linkedin=p.linkedin_url,
        )
        for p in page.contacts
        if any([p.full_name, p.email, p.linkedin_url])
    ]
    return records or [LeadRecord(**base)]


class ScrapeGraphExtractor:
    """LLM extraction through ScrapeGraphAI's SmartScraperGraph."""

    name = "scrapegraph"

    def __init__(self, llm_config: dict[str, Any], *, verbose: bool = False) -> None:
        if not llm_config:
            raise ValueError(
                "No LLM configured: set OPENROUTER_API_KEY or the 'llm' block in the config"
            )
        self.config = {"llm": llm_config, "verbose": verbose, "headless": True}

    def extract(self, html: str, url: str, prompt: str) -> list[LeadRecord]:
        from scrapegraphai.graphs import SmartScraperGraph

        # Passing HTML (not a URL) makes the graph skip its own fetch, so all
        # network access stays inside PoliteFetcher.
        graph = SmartScraperGraph(
            prompt=prompt,
            source=html,
            config=self.config,
            schema=PageLeads,
        )
        return leads_from_payload(graph.run(), url)


def make_extractor(name: str, llm_config: dict[str, Any]) -> Extractor:
    if name == "basic":
        return BasicExtractor()
    if name == "scrapegraph":
        return ScrapeGraphExtractor(llm_config)
    raise ValueError(f"Unknown extractor {name!r}")
