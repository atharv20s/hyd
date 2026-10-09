import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app.models.models import Lead, WorkItem
from app.agents import scout_agent, comms_agent
from app.services.memory_service import MemoryService
from app.services.elevenlabs_service import ElevenLabsService
from app.services.scraper_service import ScraperService
from app import worker
from conftest import signup


@pytest.mark.asyncio
async def test_auth_refresh_and_tenant_isolation(harness):
    client, factory, _ = harness
    auth, headers = await signup(client)
    other, other_headers = await signup(client, "other@example.com")
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": auth["refresh_token"]})
    assert refreshed.status_code == 200
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": auth["access_token"]})).status_code == 401
    assert (await client.get("/api/v1/leads")).status_code == 401
    imported = await client.post("/api/v1/leads/import", headers=headers, json={"csv_text": "title,address,emails\nSalon Test,Banjara Hills,test@example.com\nSalon Test,Banjara Hills,test@example.com"})
    assert imported.json()["imported"] == 1
    leads = (await client.get("/api/v1/leads?status=new&has_email=true", headers=headers)).json()
    assert len(leads) == 1
    assert (await client.get("/api/v1/leads", headers=other_headers)).json() == []
    assert (await client.get(f"/api/v1/leads/{leads[0]['id']}", headers=other_headers)).status_code == 404
    assert (await client.get(f"/api/v1/voice/demo-widget/{leads[0]['id']}", headers=other_headers)).status_code == 404


@pytest.mark.asyncio
async def test_queue_draft_approval_memory_and_no_unapproved_send(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    await client.post("/api/v1/onboarding", headers=headers, json={"service_description": "AI receptionist", "target_clients": "Salons in Hyderabad"})
    await client.post("/api/v1/leads/import", headers=headers, json={"csv_text": "title,address,emails\nSalon Test,Hyderabad,test@example.com"})
    lead = (await client.get("/api/v1/leads", headers=headers)).json()[0]
    assert (await client.post(f"/api/v1/leads/{lead['id']}/decision", headers=headers, json={"action": "approved"})).status_code == 409
    async def llm(system, message, strict=False):
        if "strongest" in system:
            return json.dumps({"index": 0, "critique": "Specific and realistic"})
        return json.dumps([{"hook_name": "Call intake", "problem": "Missed calls", "pitch": "Help staff capture appointment requests"}])
    monkeypatch.setattr(scout_agent, "call_llm", llm)
    monkeypatch.setattr(comms_agent, "call_llm", AsyncMock(return_value="SUBJECT: Hello Salon\nBODY:\nHi Salon Test, try our reception demo."))
    response = await client.post("/api/v1/leads/batch-draft", headers=headers, json={"lead_ids": [lead["id"]]})
    assert response.status_code == 202
    assert (await client.post("/api/v1/leads/batch-draft", headers=headers, json={"lead_ids": [lead["id"]]})).json()["queued"] == 0
    claims = await worker.claim_work(3)
    assert len(claims) == 1
    await worker.process_work(claims[0])
    drafted = (await client.get(f"/api/v1/leads/{lead['id']}", headers=headers)).json()
    assert drafted["outreach_status"] == "drafted"
    assert drafted["draft_body"].startswith("Hi Salon")
    approved = await client.post(f"/api/v1/leads/{lead['id']}/decision", headers=headers,
        json={"action": "edited", "edited_body": "Hi team, can we discuss your front desk?", "feedback_tag": "Keep it short"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert "No email" in approved.json()["message"]
    assert await mongo["human_decisions"].count_documents({}) == 1
    assert (await client.get("/api/v1/decisions/rules", headers=headers)).json()["rules"] == ["Keep it short"]


@pytest.mark.asyncio
async def test_client_invite_chat_intake_and_role_boundary(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    await client.post("/api/v1/leads/import", headers=headers, json={"csv_text": "title,address\nSalon Test,Jubilee Hills"})
    lead = (await client.get("/api/v1/leads", headers=headers)).json()[0]
    invite = (await client.post(f"/api/v1/leads/{lead['id']}/invite", headers=headers)).json()["url"].split("invite=")[1]
    client_auth, client_headers = await signup(client, "client@example.com", invite)
    assert client_auth["user"]["account_role"] == "client"
    assert (await client.get("/api/v1/leads", headers=client_headers)).status_code == 403
    assert (await client.get("/api/v1/client/profile", headers=client_headers)).json()["business_name"] == "Salon Test"
    from app.services import assistant_service
    monkeypatch.setattr(assistant_service, "call_llm", AsyncMock(return_value="Which service, where, and when would you prefer?"))
    assert (await client.post("/api/v1/client/chat", headers=client_headers, json={"message": "I want a haircut"})).status_code == 200
    assert len((await client.get("/api/v1/client/history", headers=client_headers)).json()) == 2
    payload = {"service": "Haircut", "location": "Jubilee Hills", "preferred_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "contact": "client@example.com"}
    intake = await client.post("/api/v1/client/intake", headers=client_headers, json=payload)
    assert intake.status_code == 201
    assert intake.json()["status"] == "pending_confirmation"
    inbox = (await client.get("/api/v1/client-requests", headers=headers)).json()
    assert len(inbox) == 1
    _, other = await signup(client, "other@example.com")
    assert (await client.get("/api/v1/client-requests", headers=other)).json() == []


@pytest.mark.asyncio
async def test_recent_chat_and_voice_transcript_isolation(harness, monkeypatch):
    client, factory, mongo = harness
    _, headers = await signup(client)
    memory = MemoryService(mongo)
    for index in range(8):
        await memory.save_chat_message("session", "operator", "user", str(index))
    assert [doc["content"] for doc in await memory.get_chat_history("session", 3)] == ["5", "6", "7"]
    from app.api import voice
    monkeypatch.setattr(voice.settings, "elevenlabs_operator_agent_id", "test-operator-agent")
    monkeypatch.setattr(ElevenLabsService, "get_conversation_token", AsyncMock(return_value={"token": "secret-session", "conversation_id": "conversation-test"}))
    session = await client.post("/api/v1/voice/session", headers=headers, json={})
    assert session.status_code == 200, session.text
    assert "api_key" not in session.text
    message = {"role": "user", "content": "Hello", "event_id": "event-1"}
    for _ in range(2):
        assert (await client.post("/api/v1/voice/sessions/conversation-test/messages", headers=headers, json=message)).status_code == 200
    assert await mongo["chat_transcripts"].count_documents({"metadata.conversation_id": "conversation-test"}) == 1
    _, other = await signup(client, "other@example.com")
    assert (await client.post("/api/v1/voice/sessions/conversation-test/messages", headers=other, json=message)).status_code == 404
    assert (await client.post("/api/v1/voice/webhook", json={})).status_code in (401, 503)


@pytest.mark.asyncio
async def test_scraper_dispatch_and_completed_import(harness, monkeypatch):
    client, factory, mongo = harness
    _, headers = await signup(client)
    response = await client.post("/api/v1/jobs", headers=headers, json={"keywords": ["hair salon"], "city": "Hyderabad", "latitude": "17.3850", "longitude": "78.4867"})
    assert response.status_code == 202, response.text
    import httpx
    requests = []
    original_client = httpx.AsyncClient
    def remote(request):
        requests.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["email"] is True
            return httpx.Response(201, json={"id": "scrape-id"})
        if request.url.path.endswith("download"):
            return httpx.Response(200, text="title,address,latitude,longitude\nImported Salon,Hyderabad,17.385,78.4867")
        return httpx.Response(200, json={"Status": "ok"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(remote), **kwargs))
    claims = await worker.claim_work(3)
    await worker.process_work(claims[0])
    jobs = (await client.get("/api/v1/jobs", headers=headers)).json()
    assert jobs[0]["status"] == "completed", jobs
    assert jobs[0]["lead_count"] == 1
    assert len((await client.get("/api/v1/leads", headers=headers)).json()) == 1


@pytest.mark.asyncio
async def test_scheduled_work_and_provider_failure(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    await client.post("/api/v1/jobs", headers=headers, json={"keywords": ["clinic"], "city": "Hyderabad", "scheduled_at": future.isoformat()})
    assert await worker.claim_work(3) == []
    from app.services import assistant_service
    monkeypatch.setattr(assistant_service, "call_llm", AsyncMock(side_effect=scout_agent.ProviderError("Reasoning provider unavailable")))
    response = await client.post("/api/v1/voice/operator-call-assistant", headers=headers, json={"message": "How many leads?"})
    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]
