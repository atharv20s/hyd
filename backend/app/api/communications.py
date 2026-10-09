"""Operator communication hub and authenticated WebSocket events."""
import asyncio
import json
import uuid
import hashlib
import hmac
import base64
import time
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db, get_mongo_db, get_redis, async_session
from app.core.security import get_operator_user, decode_token, create_access_token
from app.core.config import get_settings
from app.models.models import User, Lead, Communication, WebhookEvent, WorkItem, SendLog
from app.services.communication_service import suppress_contact, draft_reply, publish, routed_lead, routed_sms_lead
from app.services.memory_service import MemoryService

router = APIRouter(tags=["Communication Hub"])


class InboxAction(BaseModel):
    action: Literal["handled", "not_interested", "opted_out", "save_reply", "send_reply"]
    body: str = Field(default="", max_length=10000)
    feedback: str = Field(default="", max_length=1000)


class OutreachBatch(BaseModel):
    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


@router.post("/communications/outreach", status_code=202)
async def queue_approved_outreach(payload: OutreachBatch, user=Depends(get_operator_user), db=Depends(get_db)):
    """Queue only approved, selected businesses; never treat a list upload as approval."""
    if get_settings().email_provider == "resend" and not get_settings().email_enabled:
        raise HTTPException(409, "Email sending is disabled until the sender and reply inbox are configured.")
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    queued, skipped = [], []
    from app.services.communication_service import is_suppressed
    for lead_id in dict.fromkeys(payload.lead_ids):
        lead = await db.get(Lead, lead_id)
        reason = None
        if not lead or lead.user_id != user.id:
            reason = "Business not found."
        elif lead.status != "approved" or not lead.draft_body.strip():
            reason = "An approved outreach draft is required."
        elif not lead.email or await is_suppressed(db, lead):
            reason = "No email address or contact is suppressed."
        elif await db.scalar(select(SendLog.id).where(SendLog.lead_id == lead_id,
                SendLog.message_type == "outreach", SendLog.status != "failed").limit(1)):
            reason = "Outreach already reserved or sent."
        else:
            existing = (await db.scalars(select(WorkItem).where(WorkItem.user_id == user.id,
                WorkItem.kind == "outreach", WorkItem.status.in_(("pending", "running"))))).all()
            if any(item.payload.get("lead_id") == str(lead_id) for item in existing):
                reason = "Already queued."
        if reason:
            skipped.append({"lead_id": str(lead_id), "reason": reason})
            continue
        item = WorkItem(user_id=user.id, kind="outreach", payload={"lead_id": str(lead_id)})
        db.add(item)
        await db.flush()
        queued.append(str(item.id))
    await db.commit()
    return {"queued": len(queued), "task_ids": queued, "skipped": skipped}


@router.get("/communications/interested")
async def interested_businesses(user=Depends(get_operator_user), db=Depends(get_db)):
    """Actual interested responses only, scoped to the operator, not predicted conversions."""
    rows = (await db.execute(select(Communication, Lead).join(Lead, Lead.id == Communication.lead_id).where(
        Communication.user_id == user.id, Communication.direction == "inbound",
        Communication.classification.in_(("interested", "callback")), Lead.opted_out.is_(False))
        .order_by(Communication.created_at.desc()).limit(100))).all()
    return [{"lead_id": str(lead.id), "message_id": str(message.id), "business_name": lead.title,
        "address": lead.address, "latitude": lead.latitude, "longitude": lead.longitude,
        "classification": message.classification, "reply": message.body,
        "draft_response": message.draft_response, "status": message.status} for message, lead in rows]


@router.get("/communications")
async def inbox(user=Depends(get_operator_user), db=Depends(get_db)):
    rows = (await db.execute(select(Communication, Lead.title).join(Lead, Lead.id == Communication.lead_id).where(
        Communication.user_id == user.id).order_by(desc(Communication.created_at)).limit(100))).all()
    return [{"id": str(m.id), "lead_id": str(m.lead_id), "business_name": name, "channel": m.channel,
        "direction": m.direction, "subject": m.subject, "body": m.body, "classification": m.classification,
        "status": m.status, "draft_response": m.draft_response, "created_at": m.created_at} for m, name in rows]


@router.get("/communications/history/{lead_id}")
async def conversation_history(lead_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    lead = await db.scalar(select(Lead).where(Lead.id == lead_id, Lead.user_id == user.id))
    if not lead:
        raise HTTPException(404, "Business not found.")
    from app.models.models import SendLog
    incoming = (await db.scalars(select(Communication).where(Communication.user_id == user.id,
        Communication.lead_id == lead_id, Communication.direction == "inbound").order_by(Communication.created_at.desc()).limit(50))).all()
    outgoing = (await db.scalars(select(SendLog).where(SendLog.user_id == user.id,
        SendLog.lead_id == lead_id).order_by(SendLog.sent_at.desc()).limit(50))).all()
    rows = [{"id": str(row.id), "direction": "inbound", "channel": row.channel, "body": row.body,
             "status": row.status, "created_at": row.created_at} for row in incoming]
    rows.extend({"id": str(row.id), "direction": "outbound", "channel": row.channel, "body": row.body,
                 "status": row.status, "created_at": row.sent_at} for row in outgoing)
    rows.sort(key=lambda row: row["created_at"])
    return rows


async def own_message(db, user, message_id):
    message = await db.scalar(select(Communication).where(Communication.id == message_id, Communication.user_id == user.id).with_for_update())
    if not message:
        raise HTTPException(404, "Message not found.")
    return message


@router.post("/communications/{message_id}/draft")
async def prepare_reply(message_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db), mongo=Depends(get_mongo_db)):
    message = await own_message(db, user, message_id)
    try:
        body = await draft_reply(db, MemoryService(mongo), user, await db.get(Lead, message.lead_id), message)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"body": body, "status": "drafted"}


@router.post("/communications/{message_id}/action")
async def resolve_message(message_id: uuid.UUID, payload: InboxAction, user=Depends(get_operator_user), db=Depends(get_db), mongo=Depends(get_mongo_db)):
    message = await own_message(db, user, message_id)
    if message.status == "sent" and payload.action in ("save_reply", "send_reply"):
        raise HTTPException(409, "This reply was already sent.")
    if payload.action in ("not_interested", "opted_out"):
        await suppress_contact(db, await db.get(Lead, message.lead_id), payload.action)
        message.classification = payload.action
        message.status = "suppressed"
        message.draft_response = ""
    elif payload.action in ("save_reply", "send_reply"):
        from app.services.communication_service import is_suppressed
        if await is_suppressed(db, await db.get(Lead, message.lead_id)):
            raise HTTPException(409, "This contact is suppressed. No reply is allowed.")
        if not payload.body.strip():
            raise HTTPException(422, "A reply body is required.")
        await MemoryService(mongo).record_decision(str(user.id), str(message.lead_id), "reply_escalation",
            {"draft_body": message.draft_response}, "edited" if payload.body != message.draft_response else "approved",
            {"body": payload.body}, payload.feedback or None)
        message.draft_response = payload.body
        message.status = "approved"
        await db.commit()
        if payload.action == "send_reply":
            if message.channel == "portal" and message.client_user_id:
                from datetime import datetime, timezone
                await mongo["chat_transcripts"].update_one({"_id": f"portal-reply:{message.id}"}, {"$setOnInsert": {
                    "session_id": f"client:{message.client_user_id}:{message.lead_id}", "sender_type": "operator", "role": "assistant",
                    "content": payload.body, "metadata": {"operator_id": str(user.id), "lead_id": str(message.lead_id), "client_id": str(message.client_user_id)},
                    "timestamp": datetime.now(timezone.utc), "recorded_at_ns": time.time_ns()}}, upsert=True)
                ok, detail = True, "Reply delivered to the client portal."
            elif message.channel == "sms":
                from app.services.sms_service import TextbeeSMSService
                ok, detail = await TextbeeSMSService(db).send_reply(user.id, await db.get(Lead, message.lead_id), payload.body, message.id)
            else:
                from html import escape
                from app.services.email_service import EmailService
                ok, detail = await EmailService(db).send_outreach_email(user.id, await db.get(Lead, message.lead_id),
                    "Re: " + message.subject.removeprefix("Re: "), escape(payload.body).replace(chr(10), "<br>"), payload.body, message.id)
            if not ok:
                return {"status": message.status, "message": detail, "sent": False}
            message.status = "sent"
    else:
        if message.status != "suppressed":
            message.status = "handled"
    await db.commit()
    await publish(str(user.id), "inbox_updated", str(message.id))
    return {"status": message.status, "sent": message.status == "sent", "message": "Reply sent." if message.status == "sent" else "Saved. No email has been sent."}


@router.post("/communications/ws-ticket")
async def ws_ticket(user=Depends(get_operator_user)):
    from datetime import timedelta
    return {"ticket": create_access_token({"sub": str(user.id), "purpose": "websocket"}, timedelta(minutes=2))}


@router.websocket("/communications/ws")
async def updates(socket: WebSocket):
    # Authenticate in the first frame (never put access tokens in URLs or proxy logs).
    if socket.headers.get("origin", "") not in [x.strip() for x in get_settings().cors_origins.split(",")]:
        await socket.close(code=1008)
        return
    await socket.accept()
    pubsub = None
    receiver = None
    try:
        data = await asyncio.wait_for(socket.receive_json(), 5)
        token = decode_token(data.get("ticket", ""))
        if token.get("purpose") != "websocket":
            raise ValueError("Invalid purpose")
        user_id = uuid.UUID(token["sub"])
        async with async_session() as db:
            user = await db.get(User, user_id)
            if not user or not user.is_active or user.account_role != "operator":
                raise ValueError("Invalid user")
        cache = await get_redis()
        if cache is None:
            await socket.send_json({"type": "fallback", "message": "Live events unavailable; use polling."})
            await socket.close(code=1013)
            return
        pubsub = cache.pubsub()
        await pubsub.subscribe(f"hydps2:events:{user_id}")
        await socket.send_json({"type": "connected"})
        receiver = asyncio.create_task(socket.receive())
        # Limit lifetime; refresh account authorization and ticket on each reconnect.
        deadline = time.monotonic() + 25 * 60
        while time.monotonic() < deadline:
            if receiver.done():
                received = receiver.result()
                if received["type"] == "websocket.disconnect":
                    break
                receiver = asyncio.create_task(socket.receive())
            event = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
            if event:
                await socket.send_text(event["data"])
            else:
                await asyncio.sleep(.1)
        await socket.close(code=1000)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        try:
            await socket.close(code=1008)
        except RuntimeError:
            pass
    finally:
        if receiver is not None:
            receiver.cancel()
        if pubsub is not None:
            await pubsub.aclose()


def verify_resend(body, headers):
    secret = get_settings().resend_webhook_secret
    if not secret:
        raise HTTPException(503, "Resend webhook signing secret is not configured.")
    event_id, timestamp = headers.get("svix-id", ""), headers.get("svix-timestamp", "")
    try:
        if not event_id or abs(time.time() - int(timestamp)) > 300:
            raise ValueError()
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
        signed = event_id.encode() + b"." + timestamp.encode() + b"." + body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        if not any(hmac.compare_digest(expected, part[3:]) for part in headers.get("svix-signature", "").split() if part.startswith("v1,")):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(401, "Invalid webhook signature or timestamp.")
    return event_id


def verify_textbee(body, headers):
    secret = get_settings().textbee_webhook_secret
    signature = headers.get("x-signature", "")
    if not secret:
        raise HTTPException(503, "Textbee webhook signing secret is not configured.")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid Textbee webhook signature.")


@router.post("/webhooks/resend", status_code=202)
async def receive_resend(request: Request, db=Depends(get_db)):
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(413, "Webhook payload is too large.")
    event_id = verify_resend(body, request.headers)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Invalid webhook payload.")
    return await enqueue_resend_event(db, payload, event_id)


@router.post("/communications/simulate-reply", status_code=202)
async def simulate_reply(payload: dict, user=Depends(get_operator_user), db=Depends(get_db)):
    """Accept Resend-shaped inbound events in dry-run only, with tenant ownership checks."""
    if get_settings().email_provider != "smtp" or get_settings().app_env != "development":
        raise HTTPException(403, "Reply simulation is available only in development dry-run mode.")
    data = payload.get("data")
    if payload.get("type") != "email.received" or not isinstance(data, dict):
        raise HTTPException(422, "Expected an email.received event with a data object.")
    if (not isinstance(data.get("from"), str) or not isinstance(data.get("to"), list)
            or not all(isinstance(value, str) for value in data["to"])
            or len(data["to"]) > 10 or not isinstance(data.get("subject", ""), str)):
        raise HTTPException(422, "Provide a sender string, recipient string list and subject string.")
    lead = await routed_lead(db, data.get("to", []))
    from app.services.communication_service import addresses
    if not lead or lead.user_id != user.id or not set(addresses(data.get("from"))).intersection(addresses(lead.emails)):
        raise HTTPException(404, "No matching business in your workspace.")
    if not isinstance(data.get("text"), str) or not 0 < len(data["text"].strip()) <= 10000:
        raise HTTPException(422, "A reply text of 1–10000 characters is required.")
    if not isinstance(data.get("email_id"), str) or not 0 < len(data["email_id"]) <= 100:
        raise HTTPException(422, "Provide a stable email_id for deduplication.")
    safe_payload = {"type": "email.received", "data": {key: data.get(key) for key in
        ("email_id", "from", "to", "subject", "text", "message_id")}}
    return await enqueue_resend_event(db, safe_payload, f"{user.id}:{data['email_id']}", provider="simulation")


@router.get("/communications/simulation-address/{lead_id}")
async def simulation_address(lead_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    lead = await db.get(Lead, lead_id)
    if not lead or lead.user_id != user.id:
        raise HTTPException(404, "Business not found.")
    from app.services.communication_service import reply_address
    return {"to": reply_address(lead), "from": lead.email}


async def enqueue_resend_event(db, payload, event_id, provider="resend"):
    """One durable ingestion path for authenticated simulation and signed live events."""
    event_key = f"{provider}:{event_id}"
    existing = await db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == event_key))
    if existing:
        return {"status": "duplicate"}
    data = payload["data"]
    lead = await routed_lead(db, data.get("to", [])) if payload.get("type") == "email.received" else None
    if lead:
        # Pause outgoing activity as soon as the authenticated reply arrives, before classification.
        if not lead.opted_out:
            lead.status = "replied"
        user_id = lead.user_id
    else:
        from app.models.models import SendLog
        log = await db.scalar(select(SendLog).where(SendLog.resend_id == data.get("email_id"))) if data.get("email_id") else None
        user_id = log.user_id if log else None
    if user_id is None:
        return {"status": "ignored", "reason": "No matching inbox or outgoing email."}
    try:
        event = WebhookEvent(provider=provider, event_key=event_key, payload=payload)
        db.add(event)
        await db.flush()
        db.add(WorkItem(user_id=user_id, kind="resend_event", payload={"event_id": str(event.id)}))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return {"status": "duplicate"}
    await publish(str(user_id), "reply_received", str(lead.id) if lead else "")
    return {"status": "queued"}


@router.post("/webhooks/textbee", status_code=202)
async def receive_textbee(request: Request, db=Depends(get_db)):
    """Receive only signed SMS events; content is classified later by the worker."""
    body = await request.body()
    if len(body) > 100_000:
        raise HTTPException(413, "Webhook payload is too large.")
    verify_textbee(body, request.headers)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("idempotencyKey"), str):
            raise ValueError()
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(422, "Invalid Textbee webhook payload.")
    event_key = f"textbee:{payload['idempotencyKey']}"
    existing = await db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == event_key))
    if existing:
        return {"status": "duplicate"}
    if payload.get("webhookEvent") != "MESSAGE_RECEIVED":
        return {"status": "ignored"}
    lead = await routed_sms_lead(db, payload.get("sender"))
    if not lead:
        # Return success: retrying cannot make an unrecognised sender safe to route.
        return {"status": "ignored", "reason": "No unique business phone match."}
    if not lead.opted_out:
        lead.status = "replied"  # Stop drafting/outreach before the worker classifies the content.
    try:
        event = WebhookEvent(provider="textbee", event_key=event_key, payload=payload)
        db.add(event)
        await db.flush()
        db.add(WorkItem(user_id=lead.user_id, kind="textbee_event", payload={"event_id": str(event.id)}))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return {"status": "duplicate"}
    await publish(str(lead.user_id), "reply_received", str(lead.id))
    return {"status": "queued"}
