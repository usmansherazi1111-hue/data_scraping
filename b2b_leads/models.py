"""ORM models: CRM, campaigns and scrape jobs."""

from __future__ import annotations

import enum
import secrets
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    return secrets.token_urlsafe(24)


# --------------------------------------------------------------------------- CRM


class PipelineStage(str, enum.Enum):
    NEW = "new"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"


class LawfulBasis(str, enum.Enum):
    """GDPR Art. 6 lawful basis for processing a contact's data."""

    LEGITIMATE_INTEREST = "legitimate_interest"
    CONSENT = "consent"
    CONTRACT = "contract"
    UNKNOWN = "unknown"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(500))
    industry: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    contacts: Mapped[list[Contact]] = relationship(back_populates="company")
    notes: Mapped[list[Note]] = relationship(back_populates="company")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(255))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    stage: Mapped[PipelineStage] = mapped_column(
        Enum(PipelineStage), default=PipelineStage.NEW, index=True
    )
    owner: Mapped[str | None] = mapped_column(String(255))
    tags: Mapped[str | None] = mapped_column(String(500))  # comma separated

    # Provenance: where the data came from (GDPR Art. 14 transparency).
    source: Mapped[str | None] = mapped_column(String(64))  # scrape | import | manual | api
    source_url: Mapped[str | None] = mapped_column(String(1000))

    # Compliance: GDPR / CAN-SPAM.
    lawful_basis: Mapped[LawfulBasis] = mapped_column(
        Enum(LawfulBasis), default=LawfulBasis.LEGITIMATE_INTEREST
    )
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opt_out_reason: Mapped[str | None] = mapped_column(String(255))
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    unsubscribe_token: Mapped[str] = mapped_column(
        String(64), unique=True, default=new_token, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    company: Mapped[Company | None] = relationship(back_populates="contacts")
    notes: Mapped[list[Note]] = relationship(back_populates="contact")

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def contactable(self) -> bool:
        return bool(self.email) and not self.opted_out and not self.do_not_contact


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    author: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contact: Mapped[Contact | None] = relationship(back_populates="notes")
    company: Mapped[Company | None] = relationship(back_populates="notes")


class StageChange(Base):
    """Audit trail of pipeline movements, used for conversion KPIs."""

    __tablename__ = "stage_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    from_stage: Mapped[PipelineStage | None] = mapped_column(Enum(PipelineStage))
    to_stage: Mapped[PipelineStage] = mapped_column(Enum(PipelineStage))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------- Campaigns


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class RecipientStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    BOUNCED = "bounced"
    OPENED = "opened"
    CLICKED = "clicked"
    REPLIED = "replied"
    CONVERTED = "converted"
    UNSUBSCRIBED = "unsubscribed"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus), default=CampaignStatus.DRAFT
    )
    channel: Mapped[str] = mapped_column(String(32), default="email")
    from_name: Mapped[str | None] = mapped_column(String(255))
    from_email: Mapped[str | None] = mapped_column(String(320))
    subject_template: Mapped[str] = mapped_column(String(500), default="")
    body_template: Mapped[str] = mapped_column(Text, default="")
    daily_send_limit: Mapped[int] = mapped_column(Integer, default=50)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipients: Mapped[list[CampaignRecipient]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    status: Mapped[RecipientStatus] = mapped_column(
        Enum(RecipientStatus), default=RecipientStatus.QUEUED, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")
    contact: Mapped[Contact] = relationship()


# ------------------------------------------------------------------ Scrape jobs


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AttemptOutcome(str, enum.Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"  # 403/429 or an access challenge page
    DISALLOWED = "disallowed"  # robots.txt says no
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    EXTRACT_ERROR = "extract_error"


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    prompt: Mapped[str] = mapped_column(Text)
    extractor: Mapped[str] = mapped_column(String(32), default="scrapegraph")
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)
    total_urls: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    records: Mapped[int] = mapped_column(Integer, default=0)
    output_csv: Mapped[str | None] = mapped_column(String(1000))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[list[ScrapeAttempt]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class ScrapeAttempt(Base):
    __tablename__ = "scrape_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_jobs.id"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    outcome: Mapped[AttemptOutcome] = mapped_column(Enum(AttemptOutcome), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    proxy: Mapped[str | None] = mapped_column(String(255))  # redacted proxy label
    tries: Mapped[int] = mapped_column(Integer, default=1)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    records: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[ScrapeJob] = relationship(back_populates="attempts")
