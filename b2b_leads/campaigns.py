"""Campaign management: audiences, templating, compliant sending and event tracking."""

from __future__ import annotations

import os
import re
import smtplib
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import crm
from .models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    PipelineStage,
    RecipientStatus,
    utcnow,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Event name -> (timestamp column, status it implies, pipeline stage it implies)
EVENTS: dict[str, tuple[str, RecipientStatus, PipelineStage | None]] = {
    "sent": ("sent_at", RecipientStatus.SENT, PipelineStage.CONTACTED),
    "bounced": ("bounced_at", RecipientStatus.BOUNCED, None),
    "opened": ("opened_at", RecipientStatus.OPENED, None),
    "clicked": ("clicked_at", RecipientStatus.CLICKED, None),
    "replied": ("replied_at", RecipientStatus.REPLIED, PipelineStage.ENGAGED),
    "converted": ("converted_at", RecipientStatus.CONVERTED, PipelineStage.QUALIFIED),
    "unsubscribed": ("unsubscribed_at", RecipientStatus.UNSUBSCRIBED, None),
}
STATUS_RANK = {s: i for i, s in enumerate(RecipientStatus)}
STAGE_RANK = {s: i for i, s in enumerate(PipelineStage)}


class ComplianceError(RuntimeError):
    """Raised when a send would break CAN-SPAM / GDPR rules configured here."""


# ---------------------------------------------------------------- templating


def render(template: str, contact: Contact) -> str:
    """Fill ``{{placeholder}}`` fields from the contact and its company."""
    company = contact.company
    values = {
        "first_name": contact.first_name or "there",
        "last_name": contact.last_name or "",
        "full_name": contact.full_name or "there",
        "title": contact.title or "",
        "email": contact.email or "",
        "company": company.name if company else "your company",
        "company_name": company.name if company else "your company",
        "industry": (company.industry if company else None) or "",
        "location": (company.location if company else None) or "",
    }
    return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


# ------------------------------------------------------------------- senders


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    body: str
    from_name: str | None
    from_email: str
    unsubscribe_url: str


class Sender(Protocol):
    def send(self, email: OutgoingEmail) -> None: ...


def build_message(email: OutgoingEmail) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = email.to
    msg["From"] = f"{email.from_name} <{email.from_email}>" if email.from_name else email.from_email
    msg["Subject"] = email.subject
    # RFC 8058 one-click unsubscribe, expected by major mailbox providers.
    msg["List-Unsubscribe"] = f"<{email.unsubscribe_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(email.body)
    return msg


class OutboxSender:
    """Dry-run sender: writes each message as an .eml file instead of sending."""

    def __init__(self, outbox_dir: Path) -> None:
        self.outbox_dir = outbox_dir
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.sent: list[OutgoingEmail] = []

    def send(self, email: OutgoingEmail) -> None:
        name = f"{utcnow():%Y%m%dT%H%M%S%f}_{re.sub(r'[^\w.-]', '_', email.to)}.eml"
        (self.outbox_dir / name).write_bytes(bytes(build_message(email)))
        self.sent.append(email)


class SmtpSender:
    """Real sender over SMTP. Credentials come from the environment only."""

    def __init__(self) -> None:
        self.host = os.environ["SMTP_HOST"]
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.user = os.getenv("SMTP_USER")
        self.password = os.getenv("SMTP_PASSWORD")

    def send(self, email: OutgoingEmail) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password or "")
            smtp.send_message(build_message(email))


# ------------------------------------------------------------------ services


def create_campaign(session: Session, **fields: object) -> Campaign:
    campaign = Campaign(**fields)
    session.add(campaign)
    session.flush()
    return campaign


def add_recipients(
    session: Session,
    campaign: Campaign,
    *,
    contact_ids: list[int] | None = None,
    stage: PipelineStage | str | None = None,
    tag: str | None = None,
) -> int:
    """Add an audience to a campaign. Opted-out / do-not-contact people are skipped.

    Returns:
        How many new recipients were added.
    """
    query = select(Contact).where(
        Contact.email.is_not(None),
        Contact.opted_out.is_(False),
        Contact.do_not_contact.is_(False),
    )
    if contact_ids:
        query = query.where(Contact.id.in_(contact_ids))
    if stage:
        query = query.where(Contact.stage == PipelineStage(stage))
    if tag:
        query = query.where(Contact.tags.contains(tag))

    existing = set(
        session.scalars(
            select(CampaignRecipient.contact_id).where(
                CampaignRecipient.campaign_id == campaign.id
            )
        )
    )
    added = 0
    for contact in session.scalars(query):
        if contact.id not in existing:
            session.add(CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id))
            added += 1
    session.flush()
    return added


def compose(
    campaign: Campaign, contact: Contact, *, postal_address: str, unsubscribe_base_url: str
) -> OutgoingEmail:
    """Render the message and append the mandatory CAN-SPAM footer."""
    unsubscribe_url = f"{unsubscribe_base_url.rstrip('/')}/{contact.unsubscribe_token}"
    footer = (
        "\n\n--\n"
        f"{postal_address}\n"
        f"You received this because of your role at "
        f"{contact.company.name if contact.company else 'your company'}. "
        f"To stop receiving these emails, unsubscribe here: {unsubscribe_url}"
    )
    return OutgoingEmail(
        to=contact.email or "",
        subject=render(campaign.subject_template, contact),
        body=render(campaign.body_template, contact) + footer,
        from_name=campaign.from_name,
        from_email=campaign.from_email or "",
        unsubscribe_url=unsubscribe_url,
    )


def sent_last_24h(session: Session, campaign: Campaign) -> int:
    since = utcnow() - timedelta(hours=24)
    return session.scalar(
        select(func.count()).where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.sent_at >= since,
        )
    ) or 0


def send_batch(
    session: Session,
    campaign: Campaign,
    sender: Sender,
    *,
    postal_address: str,
    unsubscribe_base_url: str,
) -> dict[str, int]:
    """Send queued messages, up to the campaign's rolling 24h limit.

    Raises:
        ComplianceError: if the sender identity or postal address is missing.
    """
    if not postal_address.strip():
        raise ComplianceError(
            "Set sender_postal_address (CAN-SPAM requires a physical postal address)."
        )
    if not campaign.from_email:
        raise ComplianceError("The campaign needs a from_email.")
    if campaign.status in (CampaignStatus.PAUSED, CampaignStatus.COMPLETED):
        return {"sent": 0, "skipped": 0, "failed": 0}

    if campaign.status == CampaignStatus.DRAFT:
        campaign.status = CampaignStatus.ACTIVE
        campaign.started_at = utcnow()

    budget = max(campaign.daily_send_limit - sent_last_24h(session, campaign), 0)
    queued = session.scalars(
        select(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status == RecipientStatus.QUEUED,
        )
        .order_by(CampaignRecipient.id)
    ).all()

    stats = {"sent": 0, "skipped": 0, "failed": 0}
    for rec in queued:
        if stats["sent"] >= budget:
            break
        contact = rec.contact
        if not contact.contactable:  # opted out after being queued
            rec.status = RecipientStatus.UNSUBSCRIBED
            stats["skipped"] += 1
            continue
        try:
            sender.send(
                compose(
                    campaign,
                    contact,
                    postal_address=postal_address,
                    unsubscribe_base_url=unsubscribe_base_url,
                )
            )
        except Exception as exc:  # noqa: BLE001 - record and continue
            rec.error = str(exc)
            stats["failed"] += 1
            continue
        record_event(session, rec, "sent")
        stats["sent"] += 1

    remaining = session.scalar(
        select(func.count()).where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status == RecipientStatus.QUEUED,
        )
    )
    if not remaining:
        campaign.status = CampaignStatus.COMPLETED
    session.flush()
    return stats


def record_event(session: Session, rec: CampaignRecipient, event: str) -> CampaignRecipient:
    """Record an engagement event (from a webhook, tracking pixel or manually).

    Timestamps are kept per event so rates stay correct even when events arrive
    out of order; ``status`` always holds the furthest step reached.
    """
    if event not in EVENTS:
        raise ValueError(f"Unknown event {event!r}; expected one of {sorted(EVENTS)}")
    column, status, stage = EVENTS[event]
    if getattr(rec, column) is None:
        setattr(rec, column, utcnow())
    if event == "bounced" or STATUS_RANK[status] > STATUS_RANK[rec.status]:
        rec.status = status
    if event == "unsubscribed":
        crm.opt_out(session, rec.contact, reason=f"campaign {rec.campaign_id}")
    if stage and STAGE_RANK[stage] > STAGE_RANK[rec.contact.stage]:
        crm.set_stage(session, rec.contact, stage)
    session.flush()
    return rec


def get_recipient(session: Session, campaign_id: int, contact_id: int) -> CampaignRecipient:
    rec = session.scalar(
        select(CampaignRecipient).where(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.contact_id == contact_id,
        )
    )
    if rec is None:
        raise LookupError("Contact is not a recipient of this campaign")
    return rec
