from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from b2b_leads.api import create_app  # noqa: E402


@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx)) as c:
        yield c


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "B2B Leads" in r.text
    assert client.get("/api/kpis").json()["pipeline"]["contacts"] == 0


def test_crm_endpoints(client):
    co = client.post("/api/companies", json={"name": "Acme", "website": "https://acme.test"}).json()
    c = client.post(
        "/api/contacts",
        json={"first_name": "Jane", "email": "Jane@acme.test", "company_id": co["id"]},
    ).json()
    assert c["email"] == "jane@acme.test" and c["company"] == "Acme"

    assert client.patch(f"/api/contacts/{c['id']}", json={"stage": "qualified"}).json()["stage"] == "qualified"
    assert client.patch(f"/api/contacts/{c['id']}", json={"stage": "bogus"}).status_code == 422
    client.post(f"/api/contacts/{c['id']}/notes", json={"body": "Call back Monday"})
    assert client.get(f"/api/contacts/{c['id']}").json()["notes"][0]["body"] == "Call back Monday"

    csv_text = client.get("/api/contacts.csv").text
    assert "jane@acme.test" in csv_text

    client.post(f"/api/contacts/{c['id']}/opt-out", json={"reason": "asked"})
    assert "jane@acme.test" not in client.get("/api/contacts.csv").text


def test_campaign_endpoints(client):
    c = client.post("/api/contacts", json={"first_name": "Jane", "email": "jane@acme.test"}).json()
    camp = client.post(
        "/api/campaigns",
        json={"name": "Q4", "from_email": "me@us.test", "subject_template": "Hi {{first_name}}"},
    ).json()
    assert client.post(f"/api/campaigns/{camp['id']}/recipients", json={}).json() == {"added": 1}
    assert client.get(f"/api/campaigns/{camp['id']}/preview/{c['id']}").json()["subject"] == "Hi Jane"
    sent = client.post(f"/api/campaigns/{camp['id']}/send").json()
    assert sent["sent"] == 1 and sent["mode"] == "OutboxSender"
    r = client.post(f"/api/campaigns/{camp['id']}/events", json={"contact_id": c["id"], "event": "replied"})
    assert r.json() == {"status": "replied"}
    assert client.get("/api/kpis").json()["campaigns"]["reply_rate"] == 100.0


def test_public_unsubscribe(client, ctx):
    from b2b_leads.models import Contact

    c = client.post("/api/contacts", json={"email": "x@acme.test"}).json()
    with ctx.sessions() as s:
        token = s.get(Contact, c["id"]).unsubscribe_token
    assert client.get(f"/unsubscribe/{token}").status_code == 200
    assert client.get(f"/api/contacts/{c['id']}").json()["opted_out"] is True
    assert client.get("/unsubscribe/not-a-token").status_code == 200


def test_scrape_job_endpoint(client):
    r = client.post(
        "/api/scrape-jobs",
        json={"urls": ["https://acme.test/"], "extractor": "basic", "name": "api"},
    )
    assert r.status_code == 202
    job_id = r.json()["id"]
    for _ in range(50):
        job = client.get(f"/api/scrape-jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed" and job["records"] == 2
    csv = client.get(f"/api/scrape-jobs/{job_id}/csv")
    assert csv.status_code == 200 and "sales@acme.test" in csv.text


def test_scrapegraph_extractor_needs_llm(client, ctx):
    ctx.settings.llm = {}
    r = client.post("/api/scrape-jobs", json={"urls": ["https://acme.test/"]})
    assert r.status_code == 422
