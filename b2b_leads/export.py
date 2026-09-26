"""Structured CSV export."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .models import Contact
from .schemas import LEAD_CSV_COLUMNS, LeadRecord

CONTACT_CSV_COLUMNS = [
    "id",
    "first_name",
    "last_name",
    "title",
    "email",
    "phone",
    "linkedin_url",
    "company",
    "company_domain",
    "industry",
    "location",
    "stage",
    "owner",
    "tags",
    "source",
    "source_url",
    "lawful_basis",
    "opted_out",
    "opted_out_at",
    "do_not_contact",
    "created_at",
]


def dedupe(records: Iterable[LeadRecord]) -> list[LeadRecord]:
    """Drop duplicates by (email) or, for records without email, (domain, name, linkedin)."""
    seen: set[tuple[str | None, ...]] = set()
    out = []
    for r in records:
        key = (r.email,) if r.email else (r.company_domain, r.full_name, r.contact_linkedin)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def write_leads_csv(records: Iterable[LeadRecord], path: Path) -> int:
    """Write leads with a fixed header. UTF-8 with BOM so Excel opens it cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = dedupe(records)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEAD_CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r.to_row())
    return len(rows)


def write_contacts_csv(session: Session, path: Path, *, include_opted_out: bool = False) -> int:
    """Export CRM contacts. Opted-out contacts are excluded unless asked for."""
    query = select(Contact).options(joinedload(Contact.company)).order_by(Contact.id)
    if not include_opted_out:
        query = query.where(Contact.opted_out.is_(False), Contact.do_not_contact.is_(False))
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONTACT_CSV_COLUMNS)
        writer.writeheader()
        for c in session.scalars(query).unique():
            co = c.company
            writer.writerow(
                {
                    "id": c.id,
                    "first_name": c.first_name or "",
                    "last_name": c.last_name or "",
                    "title": c.title or "",
                    "email": c.email or "",
                    "phone": c.phone or "",
                    "linkedin_url": c.linkedin_url or "",
                    "company": co.name if co else "",
                    "company_domain": (co.domain if co else "") or "",
                    "industry": (co.industry if co else "") or "",
                    "location": (co.location if co else "") or "",
                    "stage": c.stage.value,
                    "owner": c.owner or "",
                    "tags": c.tags or "",
                    "source": c.source or "",
                    "source_url": c.source_url or "",
                    "lawful_basis": c.lawful_basis.value,
                    "opted_out": c.opted_out,
                    "opted_out_at": c.opted_out_at.isoformat() if c.opted_out_at else "",
                    "do_not_contact": c.do_not_contact,
                    "created_at": c.created_at.isoformat() if c.created_at else "",
                }
            )
            count += 1
    return count


def read_leads_csv(path: Path) -> list[LeadRecord]:
    """Read a lead CSV, e.g. an export from a licensed data provider.

    Unknown columns are ignored; missing ones are left empty.
    """
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [LeadRecord.model_validate(row) for row in csv.DictReader(fh)]
