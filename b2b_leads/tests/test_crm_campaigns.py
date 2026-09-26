from __future__ import annotations

import pytest
from sqlalchemy import select

from b2b_leads import campaigns, crm, kpi
from b2b_leads.db import session_scope
from b2b_leads.models import Contact, PipelineStage, RecipientStatus, StageChange
from b2b_leads.schemas import LeadRecord


def seed(session):
    for i, email in enumerate(["a@acme.test", "b@acme.test", "c@acme.test"]):
        crm.import_lead(
            session,
            LeadRecord(
                company_name="Acme",
                company_website="https://www.acme.test",
                full_name=f"Person {i} Last",
                email=email,
                source_url="https://acme.test/team",
            ),
        )


def test_import_dedupes_and_links_company(ctx):
    with session_scope(ctx.sessions) as s:
        seed(s)
        seed(s)
        contacts = s.scalars(select(Contact)).all()
        assert len(contacts) == 3
        assert {c.company.domain for c in contacts} == {"acme.test"}
        assert contacts[0].first_name == "Person" and contacts[0].last_name == "0 Last"


def test_opt_out_survives_reimport(ctx):
    with session_scope(ctx.sessions) as s:
        seed(s)
        c = s.scalar(select(Contact).where(Contact.email == "a@acme.test"))
        crm.opt_out(s, c, "asked")
        seed(s)
        assert s.scalar(select(Contact).where(Contact.email == "a@acme.test")).opted_out


def test_stage_changes_are_logged(ctx):
    with session_scope(ctx.sessions) as s:
        seed(s)
        c = s.scalar(select(Contact))
        crm.set_stage(s, c, "qualified")
        crm.set_stage(s, c, PipelineStage.WON)
        changes = s.scalars(select(StageChange)).all()
        assert [ch.to_stage for ch in changes] == [PipelineStage.QUALIFIED, PipelineStage.WON]
        with pytest.raises(ValueError):
            crm.set_stage(s, c, "nonsense")


def test_notes_and_erasure(ctx):
    with session_scope(ctx.sessions) as s:
        seed(s)
        c = s.scalar(select(Contact))
        crm.add_note(s, "Met at conference", contact_id=c.id)
        crm.erase_contact(s, c)
        assert c.first_name is None and not c.notes
        assert c.opted_out and c.do_not_contact and c.email  # kept as suppression entry


def test_campaign_flow_respects_opt_out_and_limits(ctx):
    with session_scope(ctx.sessions) as s:
        seed(s)
        camp = campaigns.create_campaign(
            s, name="Q4", from_email="me@us.test", daily_send_limit=1,
            subject_template="Hi {{first_name}} at {{company}}", body_template="Hello",
        )
        crm.opt_out(s, s.scalar(select(Contact).where(Contact.email == "c@acme.test")))
        assert campaigns.add_recipients(s, camp) == 2  # opted-out person excluded

        kwargs = dict(postal_address=ctx.settings.sender_postal_address, unsubscribe_base_url="http://x/unsub")
        assert campaigns.send_batch(s, camp, ctx.sender, **kwargs)["sent"] == 1
        assert campaigns.send_batch(s, camp, ctx.sender, **kwargs)["sent"] == 0  # daily limit

        sent = ctx.sender.sent[0]
        assert sent.subject == "Hi Person at Acme"
        assert "1 Main St" in sent.body and "http://x/unsub/" in sent.body


def test_send_requires_postal_address(ctx):
    with session_scope(ctx.sessions) as s:
        camp = campaigns.create_campaign(s, name="x", from_email="me@us.test")
        with pytest.raises(campaigns.ComplianceError):
            campaigns.send_batch(s, camp, ctx.sender, postal_address="", unsubscribe_base_url="u")


def test_events_drive_kpis_and_pipeline(ctx):
    with session_scope(ctx.sessions) as s:
        seed(s)
        camp = campaigns.create_campaign(s, name="Q4", from_email="me@us.test")
        campaigns.add_recipients(s, camp)
        campaigns.send_batch(
            s, camp, ctx.sender, postal_address="addr", unsubscribe_base_url="http://x/u"
        )
        ids = [c.id for c in s.scalars(select(Contact).order_by(Contact.id))]
        campaigns.record_event(s, campaigns.get_recipient(s, camp.id, ids[0]), "opened")
        campaigns.record_event(s, campaigns.get_recipient(s, camp.id, ids[0]), "replied")
        campaigns.record_event(s, campaigns.get_recipient(s, camp.id, ids[1]), "converted")
        campaigns.record_event(s, campaigns.get_recipient(s, camp.id, ids[2]), "bounced")

        k = kpi.campaign_kpis(s, camp.id)
        assert k["sent"] == 3 and k["delivered"] == 2
        assert k["reply_rate"] == 50.0 and k["conversion_rate"] == 50.0
        assert k["bounce_rate"] == pytest.approx(33.3)
        assert s.get(Contact, ids[0]).stage == PipelineStage.ENGAGED
        assert s.get(Contact, ids[1]).stage == PipelineStage.QUALIFIED


def test_unsubscribe_token_cancels_queued(ctx):
    with session_scope(ctx.sessions) as s:
        seed(s)
        camp = campaigns.create_campaign(s, name="Q4", from_email="me@us.test")
        campaigns.add_recipients(s, camp)
        c = s.scalar(select(Contact))
        crm.opt_out_by_token(s, c.unsubscribe_token)
        rec = campaigns.get_recipient(s, camp.id, c.id)
        assert rec.status == RecipientStatus.UNSUBSCRIBED
