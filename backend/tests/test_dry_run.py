"""Local transport and simulator exercise the same durable reply workflow as Resend."""
import json
import uuid
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app import worker
from app.core.config import get_settings
from app.models.models import Lead, Communication, Meeting, Notification, SendLog, WorkItem
from app.services import communication_service, scheduling_service
from app.services.email_provider import MailHogEmailProvider
from conftest import signup


@pytest.mark.asyncio
async def test_dry_run_approved_batch_and_deduplication(harness, monkeypatch):
    client, factory, _ = harness
    auth, headers = await signup(client)
    monkeypatch.setattr(get_settings(), "email_provider", "smtp")
    monkeypatch.setattr(get_settings(), "email_enabled", False)
    transport = AsyncMock(return_value="dry-run:demo")
    monkeypatch.setattr(MailHogEmailProvider, "send", transport)
    async with factory() as db:
        approved = Lead(user_id=uuid.UUID(auth["user"]["id"]), title="Nearby salon", emails="salon@example.com",
            outreach_status="approved", draft_body="Could we discuss your appointment intake?", draft_subject="Appointment intake")
        unapproved = Lead(user_id=approved.user_id, title="Unapproved", emails="other@example.com")
        db.add_all([approved, unapproved]); await db.commit()
        ids = [str(approved.id), str(unapproved.id)]
    queued = await client.post("/api/v1/communications/outreach", headers=headers, json={"lead_ids": ids})
    assert queued.status_code == 202 and queued.json()["queued"] == 1
    assert (await client.post("/api/v1/communications/outreach", headers=headers, json={"lead_ids": ids})).json()["queued"] == 0
    for claim in await worker.claim_work(3):
        await worker.process_work(claim)
    assert transport.await_count == 1
    payload = transport.call_args.args[0]
    assert "List-Unsubscribe" in payload["headers"] and payload["reply_to"].endswith("@dry-run.local")
    async with factory() as db:
        assert (await db.scalar(select(SendLog))).resend_id == "dry-run:demo"


@pytest.mark.asyncio
async def test_simulated_interest_meeting_and_stop(harness, monkeypatch):
    client, factory, _ = harness
    auth, headers = await signup(client)
    _, other = await signup(client, "other@example.com")
    monkeypatch.setattr(get_settings(), "email_provider", "smtp")
    monkeypatch.setattr(get_settings(), "app_env", "development")
    async with factory() as db:
        lead = Lead(user_id=uuid.UUID(auth["user"]["id"]), title="Demo salon", emails="salon@example.com", outreach_status="contacted")
        db.add(lead); await db.commit(); lead_id = str(lead.id)
    routing = (await client.get(f"/api/v1/communications/simulation-address/{lead_id}", headers=headers)).json()
    async def reply_llm(system, message, strict=False):
        return json.dumps({"classification": "interested"}) if "Classify" in system else "Thank you. The operator will confirm a time."
    monkeypatch.setattr(communication_service, "call_llm", reply_llm)
    monkeypatch.setattr(scheduling_service, "call_llm", AsyncMock(return_value=json.dumps(
        {"requested": True, "source_quote": "Can we schedule a demo?", "starts_at": None, "location": ""})))
    payload = {"type": "email.received", "data": {"email_id": "demo-interest", "from": routing["from"],
        "to": [routing["to"]], "subject": "Interested", "text": "Interested. Can we schedule a demo?"}}
    assert (await client.post("/api/v1/communications/simulate-reply", json=payload)).status_code == 401
    assert (await client.post("/api/v1/communications/simulate-reply", headers=other, json=payload)).status_code == 404
    assert (await client.post("/api/v1/communications/simulate-reply", headers=headers, json=payload)).status_code == 202
    assert (await client.post("/api/v1/communications/simulate-reply", headers=headers, json=payload)).json()["status"] == "duplicate"
    for claim in await worker.claim_work(10):
        await worker.process_work(claim)
    interested = (await client.get("/api/v1/communications/interested", headers=headers)).json()
    assert len(interested) == 1 and interested[0]["draft_response"]
    async with factory() as db:
        meeting = await db.scalar(select(Meeting))
        assert meeting.status == "proposed" and meeting.starts_at is None
        assert len((await db.scalars(select(Notification))).all()) >= 2
        assert await db.scalar(select(SendLog)) is None  # Drafting is not sending.
    delivery = AsyncMock(return_value="dry-run:approved-reply")
    monkeypatch.setattr(MailHogEmailProvider, "send", delivery)
    message_id = interested[0]["message_id"]
    approved_reply = await client.post(f"/api/v1/communications/{message_id}/action", headers=headers,
        json={"action": "send_reply", "body": "Thank you. The operator will confirm a time."})
    assert approved_reply.status_code == 200 and approved_reply.json()["sent"]
    assert delivery.await_count == 1
    assert (await client.post(f"/api/v1/communications/{message_id}/action", headers=headers,
        json={"action": "send_reply", "body": "Thank you."})).status_code == 409
    payload["data"].update(email_id="demo-stop", text="STOP")
    await client.post("/api/v1/communications/simulate-reply", headers=headers, json=payload)
    for claim in await worker.claim_work(10):
        await worker.process_work(claim)
    assert (await client.get("/api/v1/communications/interested", headers=headers)).json() == []
    async with factory() as db:
        assert (await db.get(Lead, uuid.UUID(lead_id))).opted_out
        assert (await db.scalar(select(Communication).where(Communication.event_key == f"simulation:{auth['user']['id']}:demo-stop"))).status == "suppressed"


@pytest.mark.asyncio
async def test_simulator_disabled_in_live_mode(harness):
    client, _, _ = harness
    _, headers = await signup(client)
    assert (await client.post("/api/v1/communications/simulate-reply", headers=headers, json={})).status_code == 403


@pytest.mark.asyncio
async def test_mailhog_mime_contract(monkeypatch):
    messages = []
    class SMTP:
        def __init__(self, host, port, timeout):
            assert host == "mailhog" and port == 1025
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send_message(self, message): messages.append(message)
    monkeypatch.setattr("smtplib.SMTP", SMTP)
    result = await MailHogEmailProvider().send({"from": "demo@example.com", "to": ["lead@example.com"],
        "subject": "Demo", "text": "Hello", "html": "<p>Hello</p>", "headers": {"List-Unsubscribe": "<http://localhost/unsubscribe>"}}, "demo-key")
    assert result.startswith("dry-run:") and messages[0]["X-HYDPS2-Dry-Run"] == "true"
    with pytest.raises(ValueError): MailHogEmailProvider("smtp.external.example", 587)


@pytest.mark.asyncio
async def test_telegram_alert_dry_run_and_mock_contract(monkeypatch):
    import httpx
    from app.services.telegram_service import send_operator_alert
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_enabled", False)
    assert await send_operator_alert("Interested lead") == "dry_run"
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "mock-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "mock-chat")
    original = httpx.AsyncClient
    def remote(request):
        assert request.url.path == "/botmock-token/sendMessage"
        payload = json.loads(request.content)
        assert payload["chat_id"] == "mock-chat" and "Interested lead" in payload["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(remote), **kwargs))
    assert await send_operator_alert("Interested lead") == "sent"


@pytest.mark.asyncio
async def test_webhook_failure_backoff_then_terminal_review(harness, monkeypatch):
    from app.models.models import WebhookEvent
    client, factory, _ = harness
    auth, _ = await signup(client)
    async with factory() as db:
        event = WebhookEvent(provider="resend", event_key="retry-test", payload={"type": "email.received", "data": {}})
        db.add(event); await db.flush()
        task = WorkItem(user_id=uuid.UUID(auth["user"]["id"]), kind="resend_event", payload={"event_id": str(event.id)})
        db.add(task); await db.commit(); task_id = task.id
    monkeypatch.setattr(worker, "process_resend_event", AsyncMock(side_effect=RuntimeError("Provider offline")))
    claims = await worker.claim_work(1)
    assert claims == [task_id]
    await worker.process_work(task_id)
    assert await worker.claim_work(1) == []  # Backoff prevents a busy retry loop.
    async with factory() as db:
        task = await db.get(WorkItem, task_id)
        assert task.status == "pending" and task.attempts == 1
        task.attempts = 3; task.status = "running"; await db.commit()
    await worker.process_work(task_id)
    async with factory() as db:
        assert (await db.get(WorkItem, task_id)).status == "failed"
