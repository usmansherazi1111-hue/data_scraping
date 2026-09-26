"""KPI calculations for campaigns, the sales pipeline and scraping."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import (
    AttemptOutcome,
    Campaign,
    CampaignRecipient,
    Contact,
    PipelineStage,
    ScrapeAttempt,
    ScrapeJob,
)


def rate(numerator: int, denominator: int) -> float:
    """Percentage rounded to one decimal; 0 when there is nothing to divide by."""
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _count_not_null(column: Any) -> Any:
    return func.sum(case((column.is_not(None), 1), else_=0))


def campaign_kpis(session: Session, campaign_id: int | None = None) -> dict[str, Any]:
    """Email funnel for one campaign, or all campaigns when ``campaign_id`` is None.

    Rates use delivered messages (sent minus bounced) as the denominator.
    """
    r = CampaignRecipient
    query = select(
        func.count(r.id),
        _count_not_null(r.sent_at),
        _count_not_null(r.bounced_at),
        _count_not_null(r.opened_at),
        _count_not_null(r.clicked_at),
        _count_not_null(r.replied_at),
        _count_not_null(r.converted_at),
        _count_not_null(r.unsubscribed_at),
        func.sum(case((r.unsubscribed_at.is_not(None) & r.sent_at.is_not(None), 1), else_=0)),
    )
    if campaign_id is not None:
        query = query.where(r.campaign_id == campaign_id)
    total, sent, bounced, opened, clicked, replied, converted, unsub, unsub_sent = (
        int(v or 0) for v in session.execute(query).one()
    )
    delivered = max(sent - bounced, 0)
    return {
        "recipients": total,
        "sent": sent,
        "delivered": delivered,
        "opened": opened,
        "clicked": clicked,
        "replied": replied,
        "converted": converted,
        "bounced": bounced,
        "unsubscribed": unsub,
        "open_rate": rate(opened, delivered),
        "click_rate": rate(clicked, delivered),
        "reply_rate": rate(replied, delivered),
        "conversion_rate": rate(converted, delivered),
        "bounce_rate": rate(bounced, sent),
        "unsubscribe_rate": rate(unsub_sent, delivered),
    }


def per_campaign_kpis(session: Session) -> list[dict[str, Any]]:
    out = []
    for campaign in session.scalars(select(Campaign).order_by(Campaign.id.desc())):
        out.append(
            {
                "id": campaign.id,
                "name": campaign.name,
                "status": campaign.status.value,
                **campaign_kpis(session, campaign.id),
            }
        )
    return out


def pipeline_kpis(session: Session) -> dict[str, Any]:
    rows = session.execute(select(Contact.stage, func.count()).group_by(Contact.stage)).all()
    by_stage = {stage.value: 0 for stage in PipelineStage}
    for stage, count in rows:
        by_stage[stage.value] = count
    total = sum(by_stage.values())
    closed = by_stage["won"] + by_stage["lost"]
    opted_out = session.scalar(select(func.count()).where(Contact.opted_out.is_(True))) or 0
    return {
        "contacts": total,
        "by_stage": by_stage,
        "opted_out": opted_out,
        "win_rate": rate(by_stage["won"], closed),
        "lead_to_qualified_rate": rate(
            by_stage["qualified"] + by_stage["proposal"] + by_stage["won"], total
        ),
    }


def scrape_kpis(session: Session, job_id: int | None = None) -> dict[str, Any]:
    """Scrape success, block and robots-disallowed rates across attempts."""
    filters = [ScrapeAttempt.job_id == job_id] if job_id is not None else []
    counts = {o.value: 0 for o in AttemptOutcome}
    for outcome, count in session.execute(
        select(ScrapeAttempt.outcome, func.count())
        .where(*filters)
        .group_by(ScrapeAttempt.outcome)
    ).all():
        counts[outcome.value] = count
    total = sum(counts.values())
    records, avg_duration = session.execute(
        select(
            func.coalesce(func.sum(ScrapeAttempt.records), 0),
            func.avg(ScrapeAttempt.duration_seconds),
        ).where(*filters)
    ).one()
    return {
        "attempts": total,
        "by_outcome": counts,
        "records": int(records or 0),
        "success_rate": rate(counts["success"], total),
        "block_rate": rate(counts["blocked"], total),
        "disallowed_rate": rate(counts["disallowed"], total),
        "avg_duration_seconds": round(float(avg_duration or 0.0), 2),
        "jobs": session.scalar(select(func.count(ScrapeJob.id))) or 0,
    }


def dashboard(session: Session, proxy_stats: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "campaigns": campaign_kpis(session),
        "per_campaign": per_campaign_kpis(session),
        "pipeline": pipeline_kpis(session),
        "scraping": scrape_kpis(session),
        "proxies": proxy_stats or [],
    }
