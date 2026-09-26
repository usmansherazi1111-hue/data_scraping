"""CRM service layer: companies, contacts, pipeline stages, notes and opt-outs."""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    CampaignRecipient,
    Company,
    Contact,
    LawfulBasis,
    Note,
    PipelineStage,
    RecipientStatus,
    StageChange,
    utcnow,
)
from .schemas import LeadRecord


def normalize_domain(value: str | None) -> str | None:
    """Turn a URL, email or bare host into a lowercase domain without ``www.``."""
    if not value:
        return None
    value = value.strip().lower()
    if "@" in value and "/" not in value:
        value = value.split("@", 1)[1]
    if "://" not in value:
        value = "http://" + value
    host = urlparse(value).hostname or ""
    return host.removeprefix("www.") or None


def get_or_create_company(
    session: Session, name: str | None, domain: str | None = None, **fields: str | None
) -> Company | None:
    """Find a company by domain (preferred) or exact name, creating it if missing.

    Existing empty fields are filled in; populated fields are never overwritten.
    """
    domain = normalize_domain(domain)
    if not name and not domain:
        return None

    company = None
    if domain:
        company = session.scalar(select(Company).where(Company.domain == domain))
    if company is None and name:
        company = session.scalar(select(Company).where(Company.name == name))
    if company is None:
        company = Company(name=name or domain, domain=domain)
        session.add(company)

    if domain and not company.domain:
        company.domain = domain
    for key, val in fields.items():
        if val and not getattr(company, key, None):
            setattr(company, key, val)
    session.flush()
    return company


def upsert_contact(
    session: Session,
    *,
    email: str | None,
    source: str = "manual",
    **fields: object,
) -> Contact:
    """Create a contact, or enrich the existing one with the same email.

    Opt-out state is never reset by an upsert.
    """
    contact = None
    if email:
        email = email.strip().lower()
        contact = session.scalar(select(Contact).where(Contact.email == email))
    if contact is None:
        contact = Contact(email=email, source=source)
        session.add(contact)
    for key, val in fields.items():
        if val is not None and not getattr(contact, key, None):
            setattr(contact, key, val)
    session.flush()
    return contact


def import_lead(session: Session, lead: LeadRecord, source: str = "scrape") -> Contact | None:
    """Store one scraped lead in the CRM. Returns the contact if one was created."""
    company = get_or_create_company(
        session,
        lead.company_name,
        lead.company_domain or lead.company_website or lead.source_url,
        website=lead.company_website,
        industry=lead.industry,
        size=lead.company_size,
        location=lead.location,
        description=lead.company_description,
        linkedin_url=lead.company_linkedin,
        source_url=lead.source_url,
    )

    has_person = any([lead.email, lead.full_name, lead.first_name, lead.contact_linkedin])
    if not has_person:
        return None

    first, last = lead.first_name, lead.last_name
    if not (first or last) and lead.full_name:
        parts = lead.full_name.split(" ", 1)
        first, last = parts[0], parts[1] if len(parts) > 1 else None

    return upsert_contact(
        session,
        email=lead.email,
        source=source,
        first_name=first,
        last_name=last,
        title=lead.title,
        phone=lead.phone,
        linkedin_url=lead.contact_linkedin,
        company_id=company.id if company else None,
        source_url=lead.source_url,
        lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
    )


def set_stage(session: Session, contact: Contact, stage: PipelineStage | str) -> Contact:
    """Move a contact through the pipeline and log the change."""
    stage = PipelineStage(stage)
    if contact.stage != stage:
        session.add(StageChange(contact_id=contact.id, from_stage=contact.stage, to_stage=stage))
        contact.stage = stage
        session.flush()
    return contact


def add_note(
    session: Session,
    body: str,
    *,
    contact_id: int | None = None,
    company_id: int | None = None,
    author: str | None = None,
) -> Note:
    if contact_id is None and company_id is None:
        raise ValueError("A note needs a contact_id or a company_id")
    note = Note(body=body, contact_id=contact_id, company_id=company_id, author=author)
    session.add(note)
    session.flush()
    return note


def opt_out(session: Session, contact: Contact, reason: str | None = None) -> Contact:
    """Record an opt-out and cancel any queued campaign sends for the contact.

    Covers CAN-SPAM unsubscribe requests and GDPR Art. 21 objections.
    """
    if not contact.opted_out:
        contact.opted_out = True
        contact.opted_out_at = utcnow()
        contact.opt_out_reason = reason
    queued = session.scalars(
        select(CampaignRecipient).where(
            CampaignRecipient.contact_id == contact.id,
            CampaignRecipient.status == RecipientStatus.QUEUED,
        )
    )
    for rec in queued:
        rec.status = RecipientStatus.UNSUBSCRIBED
        rec.unsubscribed_at = utcnow()
    session.flush()
    return contact


def opt_out_by_token(session: Session, token: str) -> Contact | None:
    contact = session.scalar(select(Contact).where(Contact.unsubscribe_token == token))
    if contact is not None:
        opt_out(session, contact, reason="unsubscribe link")
    return contact


def erase_contact(session: Session, contact: Contact) -> None:
    """GDPR Art. 17 erasure: delete personal data but keep a suppression entry.

    Only the email is kept, on an opted-out, do-not-contact tombstone, acting as
    a suppression list entry so that a future scrape cannot re-add them.
    """
    for note in list(contact.notes):
        session.delete(note)
    session.flush()
    session.expire(contact, ["notes"])
    contact.first_name = contact.last_name = contact.phone = None
    contact.title = contact.linkedin_url = contact.source_url = contact.tags = None
    contact.opted_out = True
    contact.do_not_contact = True
    contact.opted_out_at = contact.opted_out_at or utcnow()
    contact.opt_out_reason = "erasure request"
    session.flush()
