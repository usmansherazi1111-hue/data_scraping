"""Pydantic schemas shared by the pipeline, CSV export, CRM import and the API."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Column order of every exported CSV.
LEAD_CSV_COLUMNS = [
    "company_name",
    "company_domain",
    "company_website",
    "industry",
    "company_size",
    "location",
    "company_description",
    "company_linkedin",
    "first_name",
    "last_name",
    "full_name",
    "title",
    "email",
    "phone",
    "contact_linkedin",
    "source_url",
    "scraped_at",
]


class LeadRecord(BaseModel):
    """One normalized lead: a company, optionally with one person at it."""

    model_config = ConfigDict(extra="ignore")

    company_name: str | None = None
    company_domain: str | None = None
    company_website: str | None = None
    industry: str | None = None
    company_size: str | None = None
    location: str | None = None
    company_description: str | None = None
    company_linkedin: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    contact_linkedin: str | None = None
    source_url: str | None = None
    scraped_at: str | None = None

    @field_validator("email")
    @classmethod
    def _clean_email(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip().lower().removeprefix("mailto:")
        return v if EMAIL_RE.match(v) else None

    @field_validator("*", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x)
        if isinstance(v, str):
            v = v.strip()
            return v or None
        if v is None or isinstance(v, str):
            return v
        return str(v)

    def to_row(self) -> dict[str, str]:
        data = self.model_dump()
        return {col: data.get(col) or "" for col in LEAD_CSV_COLUMNS}


# ------------------------------------------------------------------ API inputs


class CompanyIn(BaseModel):
    name: str
    domain: str | None = None
    website: str | None = None
    industry: str | None = None
    size: str | None = None
    location: str | None = None
    description: str | None = None
    linkedin_url: str | None = None


class ContactIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    company_id: int | None = None
    owner: str | None = None
    tags: str | None = None
    lawful_basis: str = "legitimate_interest"


class ContactPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    title: str | None = None
    owner: str | None = None
    tags: str | None = None
    stage: str | None = None


class NoteIn(BaseModel):
    body: str = Field(min_length=1)
    author: str | None = None


class OptOutIn(BaseModel):
    reason: str | None = None


class CampaignIn(BaseModel):
    name: str
    from_name: str | None = None
    from_email: str | None = None
    subject_template: str = ""
    body_template: str = ""
    daily_send_limit: int = 50


class AddRecipientsIn(BaseModel):
    contact_ids: list[int] | None = None
    stage: str | None = None
    tag: str | None = None


class RecipientEventIn(BaseModel):
    contact_id: int
    event: str  # sent | opened | clicked | replied | converted | bounced


class ScrapeJobIn(BaseModel):
    name: str = "Scrape job"
    prompt: str = (
        "Extract the company name, what the company does, industry, location, "
        "and any publicly listed business contacts (name, title, business email, "
        "phone, LinkedIn URL)."
    )
    urls: list[str] = Field(min_length=1)
    extractor: str = "scrapegraph"  # scrapegraph | basic
    import_to_crm: bool = True
