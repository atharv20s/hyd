import uuid
import json
import hmac
import base64
import hashlib
import time
from unittest.mock import AsyncMock
from sqlalchemy import select
import httpx
import pytest
from conftest import signup
from app import worker
from app.models.models import Lead, Communication, WorkItem, SendLog, ContactSuppression
from app.services import communication_service
from app.services.email_service import EmailService
from app.services.sms_service import TextbeeSMSService
from app.core.config import get_settings


def signed(payload, event_id="msg-test"):
    raw = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    key = base64.b64decode(get_settings().resend_webhook_secret.removeprefix("whsec_"))
    signature = base64.b64encode(hmac.new(key, event_id.encode() + b"." + timestamp.encode() + b"." + raw, hashlib.sha256).digest()).decode()
    return raw, {"svix-id": event_id, "svix-timestamp": timestamp, "svix-signature": "v1," + signature, "content-type": "application/json"}


@pytest.mark.asyncio
async def test_signed_reply_stops_reimported_contact_and_deduplicates(harness, monkeypatch):
    client, factory, mongo = harness
    _, headers = await signup(client)
    _, other = await signup(client, "other@example.com")
    monkeypatch.setattr(get_settings(), "receiving_domain", "inbox.example.com")
    monkeypatch.setattr(get_settings(), "resend_webhook_secret", "whsec_" + base64.b64encode(b"test-secret").decode())
    await client.post("/api/v1/leads/import", headers=headers, json={"csv_text": "title,address,emails\nSalon Test,Hyderabad,test@example.com\nNew Branch,Jubilee Hills,test@example.com"})
    async with factory() as db:
        lead = await db.scalar(select(Lead).where(Lead.title == "Salon Test"))
        alias = communication_service.reply_address(lead)
    payload = {"type": "email.received", "data": {"email_id": "received-email", "from": "test@example.com", "to": [alias], "subject": "Re: Hello"}}
    raw, webhook_headers = signed(payload)
    bad = await client.post("/api/v1/webhooks/resend", content=raw, headers={**webhook_headers, "svix-signature": "v1,invalid"})
    assert bad.status_code == 401
    response = await client.post("/api/v1/webhooks/resend", content=raw, headers=webhook_headers)
    assert response.status_code == 202, response.text
    assert (await client.post("/api/v1/webhooks/resend", content=raw, headers=webhook_headers)).json()["status"] == "duplicate"
    original = httpx.AsyncClient
    def remote(request):
        assert request.url.path == "/emails/receiving/received-email"
        return httpx.Response(200, json={"from": "test@example.com", "subject": "Re: Hello", "text": "Not interested. Please don't contact me again."})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(remote), **kwargs))
    claims = await worker.claim_work(3)
    assert len(claims) == 1
    await worker.process_work(claims[0])
    inbox = (await client.get("/api/v1/communications", headers=headers)).json()
    assert len(inbox) == 1 and inbox[0]["status"] == "suppressed", inbox
    assert inbox[0]["classification"] == "not_interested"
    assert (await client.get("/api/v1/communications", headers=other)).json() == []
    assert (await client.post(f"/api/v1/communications/{inbox[0]['id']}/draft", headers=headers)).status_code == 409
    assert (await client.post(f"/api/v1/communications/{inbox[0]['id']}/action", headers=other, json={"action": "handled"})).status_code == 404
    async with factory() as db:
        leads = (await db.scalars(select(Lead))).all()
        assert all(lead.opted_out for lead in leads)
        assert len((await db.scalars(select(ContactSuppression))).all()) == 1
        new = Lead(user_id=leads[0].user_id, title="Another Reimport", emails="test@example.com")
        db.add(new); await db.commit()
        allowed, _ = await EmailService(db).can_send(new.user_id, new.id)
        assert not allowed
    assert (await client.get("/api/v1/tasks", headers=headers)).json()[0]["status"] == "completed"
    await client.post("/api/v1/leads/import", headers=headers, json={"csv_text": "title,address,emails\nReimported brand,New Street,test@example.com"})
    reimport = next(item for item in (await client.get("/api/v1/leads", headers=headers)).json() if item["title"] == "Reimported brand")
    assert reimport["outreach_status"] == "opted_out"


@pytest.mark.asyncio
async def test_approved_email_real_contract_idempotency_and_oneclick(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    monkeypatch.setattr(get_settings(), "email_enabled", True)
    monkeypatch.setattr(get_settings(), "resend_api_key", "test-key")
    await client.post("/api/v1/leads/import", headers=headers, json={"csv_text": "title,emails\nSalon Test,test@example.com"})
    lead_id = (await client.get("/api/v1/leads", headers=headers)).json()[0]["id"]
    calls = []
    original = httpx.AsyncClient
    def remote(request):
        calls.append(request)
        assert request.headers["Idempotency-Key"].startswith("outreach/")
        data = json.loads(request.content)
        assert "AI assistance" in data["text"]
        assert data["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        return httpx.Response(200, json={"id": "sent-id"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(remote), **kwargs))
    async with factory() as db:
        lead = await db.get(Lead, uuid.UUID(lead_id))
        lead.status = "approved"
        await db.commit()
        assert (await EmailService(db).send_outreach_email(lead.user_id, lead, "Hello", "Hello", "Hello"))[0]
        assert not (await EmailService(db).send_outreach_email(lead.user_id, lead, "Hello", "Hello", "Hello"))[0]
        log = await db.scalar(select(SendLog))
        assert log.status == "sent" and log.resend_id == "sent-id"
    assert len(calls) == 1
    link = json.loads(calls[0].content)["headers"]["List-Unsubscribe"].strip("<>")
    url = "/api/v1" + link.split("/api/v1")[1]
    assert (await client.post(url)).status_code == 200
    assert (await client.get(url.split("?token=")[0])).status_code == 401
    assert (await client.get(f"/api/v1/leads/{lead_id}", headers=headers)).json()["outreach_status"] == "opted_out"


@pytest.mark.asyncio
async def test_portal_human_reply_and_learning(harness, monkeypatch):
    client, factory, mongo = harness
    _, headers = await signup(client)
    await client.post("/api/v1/leads/import", headers=headers, json={"csv_text": "title,address\nSalon Test,Hyderabad"})
    lead = (await client.get("/api/v1/leads", headers=headers)).json()[0]
    invite = (await client.post(f"/api/v1/leads/{lead['id']}/invite", headers=headers)).json()["url"].split("invite=")[1]
    _, client_headers = await signup(client, "client@example.com", invite)
    from app.services import assistant_service
    monkeypatch.setattr(assistant_service, "call_llm", AsyncMock(return_value="Please tell me when and where."))
    await client.post("/api/v1/client/chat", headers=client_headers, json={"message": "Can a human help me?"})
    message = (await client.get("/api/v1/communications", headers=headers)).json()[0]
    assert message["channel"] == "portal"
    monkeypatch.setattr(communication_service, "call_llm", AsyncMock(return_value="A team member can help you."))
    drafted = await client.post(f"/api/v1/communications/{message['id']}/draft", headers=headers)
    assert drafted.status_code == 200
    response = await client.post(f"/api/v1/communications/{message['id']}/action", headers=headers,
        json={"action": "send_reply", "body": "I can help. Which area?", "feedback": "Ask one question at a time"})
    assert response.status_code == 200 and response.json()["sent"]
    history = (await client.get("/api/v1/client/history", headers=client_headers)).json()
    assert history[-1]["content"] == "I can help. Which area?"
    assert "Ask one question at a time" in (await client.get("/api/v1/decisions/rules", headers=headers)).json()["rules"]
    response = await client.post("/api/v1/client/chat", headers=client_headers, json={"message": "STOP"})
    assert response.status_code == 200
    assert "No further outreach" in response.json()["reply"]
    assert (await client.get(f"/api/v1/leads/{lead['id']}", headers=headers)).json()["outreach_status"] == "opted_out"


@pytest.mark.parametrize("text", ["STOP", "unsubscribe", "No thanks.", "Not interested.", "Please stop emailing me", "Remove me from your list."])
def test_stop_phrases(text):
    assert communication_service.stop_reason(text)


def test_quoted_optout_is_not_sender_request():
    assert communication_service.stop_reason("Thanks, please explain more.\nOn Friday, team wrote:\nReply STOP to opt out.") is None


@pytest.mark.asyncio
async def test_signed_textbee_sms_stops_phone_contact_and_deduplicates(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    monkeypatch.setattr(get_settings(), "textbee_webhook_secret", "textbee-test-signing-secret")
    await client.post("/api/v1/leads/import", headers=headers,
        json={"csv_text": "title,phone\nSalon Test,9876543210\nOther Salon,9123456789"})
    payload = {"smsId": "sms-1", "message": "STOP", "deviceId": "device-1", "webhookSubscriptionId": "hook-1",
        "webhookEvent": "MESSAGE_RECEIVED", "idempotencyKey": "sms-received-1", "sender": "+919876543210", "receivedAt": "2026-10-10T10:00:00Z"}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(get_settings().textbee_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    bad = await client.post("/api/v1/webhooks/textbee", content=raw, headers={"x-signature": "bad", "content-type": "application/json"})
    assert bad.status_code == 401
    response = await client.post("/api/v1/webhooks/textbee", content=raw, headers={"x-signature": signature, "content-type": "application/json"})
    assert response.status_code == 202, response.text
    assert (await client.post("/api/v1/webhooks/textbee", content=raw, headers={"x-signature": signature, "content-type": "application/json"})).json()["status"] == "duplicate"
    claims = await worker.claim_work(3)
    assert len(claims) == 1
    await worker.process_work(claims[0])
    inbox = (await client.get("/api/v1/communications", headers=headers)).json()
    assert inbox[0]["channel"] == "sms" and inbox[0]["status"] == "suppressed"
    assert inbox[0]["classification"] == "opted_out"
    async with factory() as db:
        leads = (await db.scalars(select(Lead))).all()
        stopped = next(lead for lead in leads if lead.title == "Salon Test")
        assert stopped.opted_out
        assert (await db.scalar(select(ContactSuppression).where(ContactSuppression.channel == "sms"))) is not None


@pytest.mark.asyncio
async def test_textbee_reviewed_reply_uses_phone_and_does_not_retry_timeout(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    monkeypatch.setattr(get_settings(), "sms_enabled", True)
    monkeypatch.setattr(get_settings(), "textbee_api_key", "textbee-test-key")
    original = httpx.AsyncClient
    sent = []
    def remote(request):
        sent.append(request)
        assert request.headers["x-api-key"] == "textbee-test-key"
        data = json.loads(request.content)
        assert data["recipients"] == ["+919876543210"]
        assert data["message"].endswith("Reply STOP to stop messages.")
        return httpx.Response(200, json={"data": {"success": True, "smsBatchId": "batch-1"}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(remote), **kwargs))
    async with factory() as db:
        lead = Lead(user_id=uuid.UUID(auth["user"]["id"]), title="Salon", phone="9876543210", outreach_status="replied")
        db.add(lead); await db.flush()
        message = Communication(user_id=lead.user_id, lead_id=lead.id, channel="sms", body="Please share details", event_key="sms-reply-test", status="approved")
        db.add(message); await db.commit()
        assert (await TextbeeSMSService(db).send_reply(lead.user_id, lead, "Sure, when can we speak?", message.id))[0]
        assert not (await TextbeeSMSService(db).send_reply(lead.user_id, lead, "Sure, when can we speak?", message.id))[0]
        log = await db.scalar(select(SendLog).where(SendLog.channel == "sms"))
        assert log.status == "sent" and log.resend_id == "batch-1"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_reply_draft_edit_then_suppression_blocks_send(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    async with factory() as db:
        lead = Lead(user_id=uuid.UUID(auth["user"]["id"]), title="Salon", emails="lead@example.com", outreach_status="replied")
        db.add(lead); await db.flush()
        message = Communication(user_id=lead.user_id, lead_id=lead.id, body="Tell me more", subject="Hello", event_key="test-event", status="needs_review")
        db.add(message); await db.commit()
        message_id = message.id
    monkeypatch.setattr(communication_service, "call_llm", AsyncMock(return_value="Could we discuss the service?"))
    assert (await client.post(f"/api/v1/communications/{message_id}/draft", headers=headers)).status_code == 200
    saved = await client.post(f"/api/v1/communications/{message_id}/action", headers=headers, json={"action": "save_reply", "body": "May I explain?", "feedback": "One question only"})
    assert saved.json()["status"] == "approved" and not saved.json()["sent"]
    assert (await client.post(f"/api/v1/communications/{message_id}/action", headers=headers, json={"action": "not_interested"})).json()["status"] == "suppressed"
    assert (await client.post(f"/api/v1/communications/{message_id}/action", headers=headers, json={"action": "send_reply", "body": "Hello again"})).status_code == 409


@pytest.mark.asyncio
async def test_uncertain_provider_send_does_not_retry(harness, monkeypatch):
    client, factory, mongo = harness
    auth, headers = await signup(client)
    monkeypatch.setattr(get_settings(), "email_enabled", True)
    monkeypatch.setattr(get_settings(), "resend_api_key", "test-key")
    original = httpx.AsyncClient
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("Network interrupted", request=request)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(timeout), **kwargs))
    async with factory() as db:
        lead = Lead(user_id=uuid.UUID(auth["user"]["id"]), title="Salon", emails="lead@example.com", outreach_status="approved")
        db.add(lead); await db.commit()
        ok, detail = await EmailService(db).send_outreach_email(lead.user_id, lead, "Hello", "Hello", "Hello")
        assert not ok and "uncertain" in detail
        assert not (await EmailService(db).send_outreach_email(lead.user_id, lead, "Hello", "Hello", "Hello"))[0]
        assert (await db.scalar(select(SendLog))).status == "pending"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_websocket_auth_and_live_tenant_channel(harness, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import communications
    client, factory, mongo = harness
    auth, headers = await signup(client)
    ticket = (await client.post("/api/v1/communications/ws-ticket", headers=headers)).json()["ticket"]
    assert (await client.get("/api/v1/leads", headers={"Authorization": f"Bearer {ticket}"})).status_code == 401
    assert (await client.post("/api/v1/communications/ws-ticket")).status_code == 401
    channel = []
    class Pubsub:
        async def subscribe(self, name):
            channel.append(name)
        async def get_message(self, **kwargs):
            import asyncio
            await asyncio.sleep(.01)
            return {"data": '{"type":"inbox_updated","id":"test"}'}
        async def aclose(self):
            pass
    class Cache:
        def pubsub(self):
            return Pubsub()
    monkeypatch.setattr(communications, "async_session", factory)
    monkeypatch.setattr(communications, "get_redis", AsyncMock(return_value=Cache()))
    browser = TestClient(app)
    with browser.websocket_connect("/api/v1/communications/ws", headers={"Origin": "http://localhost:3000"}) as socket:
        socket.send_json({"ticket": ticket})
        assert socket.receive_json()["type"] == "connected"
        assert socket.receive_json()["type"] == "inbox_updated"
    assert channel == [f"hydps2:events:{auth['user']['id']}"]
    with browser.websocket_connect("/api/v1/communications/ws", headers={"Origin": "http://localhost:3000"}) as socket:
        socket.send_json({"ticket": auth["access_token"]})
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
