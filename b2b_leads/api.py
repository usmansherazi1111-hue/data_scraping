"""REST API and dashboard (FastAPI)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from . import campaigns as camp
from . import crm, kpi
from .app_context import AppContext
from .export import write_contacts_csv
from .models import (
    Campaign,
    CampaignRecipient,
    Company,
    Contact,
    LawfulBasis,
    Note,
    PipelineStage,
    ScrapeJob,
)
from .pipeline import create_job, run_job
from .schemas import (
    AddRecipientsIn,
    CampaignIn,
    CompanyIn,
    ContactIn,
    ContactPatch,
    NoteIn,
    OptOutIn,
    RecipientEventIn,
    ScrapeJobIn,
)
from .scraping.extractors import make_extractor

STATIC_DIR = Path(__file__).parent / "static"
# 1x1 transparent GIF for open tracking.
PIXEL = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


def company_dict(c: Company) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "domain": c.domain,
        "website": c.website,
        "industry": c.industry,
        "size": c.size,
        "location": c.location,
        "description": c.description,
        "linkedin_url": c.linkedin_url,
        "contacts": len(c.contacts),
    }


def contact_dict(c: Contact) -> dict[str, Any]:
    return {
        "id": c.id,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "full_name": c.full_name,
        "email": c.email,
        "phone": c.phone,
        "title": c.title,
        "linkedin_url": c.linkedin_url,
        "company_id": c.company_id,
        "company": c.company.name if c.company else None,
        "stage": c.stage.value,
        "owner": c.owner,
        "tags": c.tags,
        "source": c.source,
        "source_url": c.source_url,
        "lawful_basis": c.lawful_basis.value,
        "opted_out": c.opted_out,
        "opted_out_at": c.opted_out_at.isoformat() if c.opted_out_at else None,
        "opt_out_reason": c.opt_out_reason,
        "do_not_contact": c.do_not_contact,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def campaign_dict(c: Campaign) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status.value,
        "from_name": c.from_name,
        "from_email": c.from_email,
        "subject_template": c.subject_template,
        "body_template": c.body_template,
        "daily_send_limit": c.daily_send_limit,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def job_dict(j: ScrapeJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "name": j.name,
        "status": j.status.value,
        "extractor": j.extractor,
        "total_urls": j.total_urls,
        "succeeded": j.succeeded,
        "failed": j.failed,
        "records": j.records,
        "has_csv": bool(j.output_csv),
        "error": j.error,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


def create_app(ctx: AppContext | None = None) -> FastAPI:
    ctx = ctx or AppContext.build()
    app = FastAPI(title="B2B Leads", version="0.1.0")
    app.state.ctx = ctx
    job_lock = threading.Lock()  # one scrape job at a time keeps pacing predictable

    def db() -> Iterator[Session]:
        session = ctx.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_or_404(session: Session, model: type, obj_id: int) -> Any:
        obj = session.get(model, obj_id)
        if obj is None:
            raise HTTPException(404, f"{model.__name__} {obj_id} not found")
        return obj

    # ------------------------------------------------------------ dashboard

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/kpis")
    def kpis(session: Session = Depends(db)) -> dict[str, Any]:
        return kpi.dashboard(session, ctx.pool.stats())

    # ------------------------------------------------------------ companies

    @app.get("/api/companies")
    def list_companies(q: str | None = None, session: Session = Depends(db)) -> list[dict[str, Any]]:
        query = select(Company).options(joinedload(Company.contacts)).order_by(Company.name)
        if q:
            query = query.where(or_(Company.name.ilike(f"%{q}%"), Company.domain.ilike(f"%{q}%")))
        return [company_dict(c) for c in session.scalars(query).unique()]

    @app.post("/api/companies", status_code=201)
    def create_company(body: CompanyIn, session: Session = Depends(db)) -> dict[str, Any]:
        fields = body.model_dump(exclude={"name", "domain"})
        company = crm.get_or_create_company(session, body.name, body.domain or body.website, **fields)
        assert company is not None
        return company_dict(company)

    @app.get("/api/companies/{company_id}")
    def get_company(company_id: int, session: Session = Depends(db)) -> dict[str, Any]:
        c = get_or_404(session, Company, company_id)
        return {
            **company_dict(c),
            "contacts_list": [contact_dict(x) for x in c.contacts],
            "notes": [
                {"id": n.id, "body": n.body, "author": n.author, "created_at": n.created_at.isoformat()}
                for n in c.notes
            ],
        }

    @app.post("/api/companies/{company_id}/notes", status_code=201)
    def add_company_note(company_id: int, body: NoteIn, session: Session = Depends(db)) -> dict[str, Any]:
        get_or_404(session, Company, company_id)
        note = crm.add_note(session, body.body, company_id=company_id, author=body.author)
        return {"id": note.id}

    # ------------------------------------------------------------- contacts

    @app.get("/api/contacts")
    def list_contacts(
        q: str | None = None,
        stage: str | None = None,
        include_opted_out: bool = True,
        limit: int = 200,
        offset: int = 0,
        session: Session = Depends(db),
    ) -> dict[str, Any]:
        query = select(Contact).options(joinedload(Contact.company))
        if q:
            like = f"%{q}%"
            query = query.where(
                or_(
                    Contact.email.ilike(like),
                    Contact.first_name.ilike(like),
                    Contact.last_name.ilike(like),
                    Contact.title.ilike(like),
                )
            )
        if stage:
            query = query.where(Contact.stage == PipelineStage(stage))
        if not include_opted_out:
            query = query.where(Contact.opted_out.is_(False))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(Contact.id.desc()).limit(limit).offset(offset))
        return {"total": total, "items": [contact_dict(c) for c in rows]}

    @app.post("/api/contacts", status_code=201)
    def create_contact(body: ContactIn, session: Session = Depends(db)) -> dict[str, Any]:
        data = body.model_dump(exclude={"email", "lawful_basis"})
        contact = crm.upsert_contact(
            session,
            email=body.email,
            source="manual",
            lawful_basis=LawfulBasis(body.lawful_basis),
            **data,
        )
        return contact_dict(contact)

    @app.get("/api/contacts/{contact_id}")
    def get_contact(contact_id: int, session: Session = Depends(db)) -> dict[str, Any]:
        c = get_or_404(session, Contact, contact_id)
        return {
            **contact_dict(c),
            "notes": [
                {"id": n.id, "body": n.body, "author": n.author, "created_at": n.created_at.isoformat()}
                for n in sorted(c.notes, key=lambda n: n.created_at, reverse=True)
            ],
        }

    @app.patch("/api/contacts/{contact_id}")
    def update_contact(contact_id: int, body: ContactPatch, session: Session = Depends(db)) -> dict[str, Any]:
        c = get_or_404(session, Contact, contact_id)
        data = body.model_dump(exclude_unset=True)
        stage = data.pop("stage", None)
        for key, val in data.items():
            setattr(c, key, val)
        if stage:
            try:
                crm.set_stage(session, c, stage)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return contact_dict(c)

    @app.post("/api/contacts/{contact_id}/notes", status_code=201)
    def add_contact_note(contact_id: int, body: NoteIn, session: Session = Depends(db)) -> dict[str, Any]:
        get_or_404(session, Contact, contact_id)
        note = crm.add_note(session, body.body, contact_id=contact_id, author=body.author)
        return {"id": note.id}

    @app.post("/api/contacts/{contact_id}/opt-out")
    def contact_opt_out(contact_id: int, body: OptOutIn, session: Session = Depends(db)) -> dict[str, Any]:
        c = get_or_404(session, Contact, contact_id)
        return contact_dict(crm.opt_out(session, c, body.reason))

    @app.post("/api/contacts/{contact_id}/erase")
    def contact_erase(contact_id: int, session: Session = Depends(db)) -> dict[str, Any]:
        c = get_or_404(session, Contact, contact_id)
        crm.erase_contact(session, c)
        return contact_dict(c)

    @app.get("/api/contacts.csv")
    def export_contacts(include_opted_out: bool = False, session: Session = Depends(db)) -> FileResponse:
        path = ctx.settings.exports_dir / "contacts.csv"
        write_contacts_csv(session, path, include_opted_out=include_opted_out)
        return FileResponse(path, media_type="text/csv", filename="contacts.csv")

    @app.get("/api/notes/{note_id}")
    def get_note(note_id: int, session: Session = Depends(db)) -> dict[str, Any]:
        n = get_or_404(session, Note, note_id)
        return {"id": n.id, "body": n.body, "author": n.author}

    # ---------------------------------------------------- public unsubscribe

    @app.get("/unsubscribe/{token}", response_class=HTMLResponse, include_in_schema=False)
    @app.post("/unsubscribe/{token}", response_class=HTMLResponse, include_in_schema=False)
    def unsubscribe(token: str, session: Session = Depends(db)) -> str:
        crm.opt_out_by_token(session, token)
        # Same answer whether or not the token exists, to avoid leaking data.
        return "<p>You have been unsubscribed and will not receive further emails.</p>"

    @app.get("/t/open/{campaign_id}/{token}.gif", include_in_schema=False)
    def track_open(campaign_id: int, token: str, session: Session = Depends(db)) -> Response:
        rec = session.scalar(
            select(CampaignRecipient)
            .join(Contact)
            .where(CampaignRecipient.campaign_id == campaign_id, Contact.unsubscribe_token == token)
        )
        if rec is not None:
            camp.record_event(session, rec, "opened")
        return Response(PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ campaigns

    @app.get("/api/campaigns")
    def list_campaigns(session: Session = Depends(db)) -> list[dict[str, Any]]:
        return [
            {**campaign_dict(c), **kpi.campaign_kpis(session, c.id)}
            for c in session.scalars(select(Campaign).order_by(Campaign.id.desc()))
        ]

    @app.post("/api/campaigns", status_code=201)
    def create_campaign(body: CampaignIn, session: Session = Depends(db)) -> dict[str, Any]:
        return campaign_dict(camp.create_campaign(session, **body.model_dump()))

    @app.get("/api/campaigns/{campaign_id}")
    def get_campaign(campaign_id: int, session: Session = Depends(db)) -> dict[str, Any]:
        c = get_or_404(session, Campaign, campaign_id)
        return {
            **campaign_dict(c),
            "kpis": kpi.campaign_kpis(session, c.id),
            "recipients": [
                {
                    "contact_id": r.contact_id,
                    "email": r.contact.email,
                    "name": r.contact.full_name,
                    "status": r.status.value,
                    "error": r.error,
                }
                for r in c.recipients
            ],
        }

    @app.post("/api/campaigns/{campaign_id}/recipients")
    def add_recipients(campaign_id: int, body: AddRecipientsIn, session: Session = Depends(db)) -> dict[str, int]:
        c = get_or_404(session, Campaign, campaign_id)
        added = camp.add_recipients(session, c, contact_ids=body.contact_ids, stage=body.stage, tag=body.tag)
        return {"added": added}

    @app.get("/api/campaigns/{campaign_id}/preview/{contact_id}")
    def preview(campaign_id: int, contact_id: int, session: Session = Depends(db)) -> dict[str, str]:
        c = get_or_404(session, Campaign, campaign_id)
        contact = get_or_404(session, Contact, contact_id)
        email = camp.compose(
            c,
            contact,
            postal_address=ctx.settings.sender_postal_address or "[postal address not set]",
            unsubscribe_base_url=ctx.settings.unsubscribe_base_url,
        )
        return {"to": email.to, "subject": email.subject, "body": email.body}

    @app.post("/api/campaigns/{campaign_id}/send")
    def send(campaign_id: int, session: Session = Depends(db)) -> dict[str, Any]:
        c = get_or_404(session, Campaign, campaign_id)
        try:
            stats = camp.send_batch(
                session,
                c,
                ctx.sender,
                postal_address=ctx.settings.sender_postal_address,
                unsubscribe_base_url=ctx.settings.unsubscribe_base_url,
            )
        except camp.ComplianceError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {**stats, "mode": type(ctx.sender).__name__}

    @app.post("/api/campaigns/{campaign_id}/status/{status}")
    def set_campaign_status(campaign_id: int, status: str, session: Session = Depends(db)) -> dict[str, Any]:
        from .models import CampaignStatus

        c = get_or_404(session, Campaign, campaign_id)
        try:
            c.status = CampaignStatus(status)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return campaign_dict(c)

    @app.post("/api/campaigns/{campaign_id}/events")
    def campaign_event(campaign_id: int, body: RecipientEventIn, session: Session = Depends(db)) -> dict[str, str]:
        try:
            rec = camp.get_recipient(session, campaign_id, body.contact_id)
            camp.record_event(session, rec, body.event)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"status": rec.status.value}

    # -------------------------------------------------------------- scraping

    @app.get("/api/scrape-jobs")
    def list_jobs(session: Session = Depends(db)) -> list[dict[str, Any]]:
        return [job_dict(j) for j in session.scalars(select(ScrapeJob).order_by(ScrapeJob.id.desc()))]

    @app.post("/api/scrape-jobs", status_code=202)
    def start_job(body: ScrapeJobIn, tasks: BackgroundTasks, session: Session = Depends(db)) -> dict[str, Any]:
        try:
            extractor = make_extractor(body.extractor, ctx.settings.llm)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = create_job(session, name=body.name, prompt=body.prompt, urls=body.urls, extractor=body.extractor)
        session.commit()

        def work() -> None:
            with job_lock:
                run_job(
                    ctx.sessions,
                    job.id,
                    body.urls,
                    fetcher=ctx.fetcher,
                    extractor=extractor,
                    exports_dir=ctx.settings.exports_dir,
                    import_to_crm=body.import_to_crm,
                    max_concurrent_domains=ctx.settings.scrape.max_concurrent_domains,
                )

        tasks.add_task(work)
        return job_dict(job)

    @app.get("/api/scrape-jobs/{job_id}")
    def get_job(job_id: int, session: Session = Depends(db)) -> dict[str, Any]:
        j = get_or_404(session, ScrapeJob, job_id)
        return {
            **job_dict(j),
            "kpis": kpi.scrape_kpis(session, j.id),
            "attempts": [
                {
                    "url": a.url,
                    "outcome": a.outcome.value,
                    "http_status": a.http_status,
                    "proxy": a.proxy,
                    "tries": a.tries,
                    "records": a.records,
                    "error": a.error,
                }
                for a in j.attempts
            ],
        }

    @app.get("/api/scrape-jobs/{job_id}/csv")
    def job_csv(job_id: int, session: Session = Depends(db)) -> FileResponse:
        j = get_or_404(session, ScrapeJob, job_id)
        if not j.output_csv or not Path(j.output_csv).exists():
            raise HTTPException(404, "CSV not ready")
        return FileResponse(j.output_csv, media_type="text/csv", filename=f"leads_job_{j.id}.csv")

    # --------------------------------------------------------------- proxies

    @app.get("/api/proxies")
    def proxies() -> list[dict[str, Any]]:
        return ctx.pool.stats()

    @app.post("/api/proxies/check")
    def check_proxies() -> list[dict[str, Any]]:
        ctx.pool.check_all()
        return ctx.pool.stats()

    return app
