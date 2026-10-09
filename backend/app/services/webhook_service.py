"""Process durable Resend events outside the request; retries are idempotent."""
import httpx
from sqlalchemy import select
from app.models.models import Communication, SendLog, Lead, User, Followup
from app.core.config import get_settings
from app.services.communication_service import routed_lead, routed_sms_lead, addresses, classify_reply, suppress_contact, publish, draft_reply, is_suppressed
from app.services.memory_service import MemoryService


async def process_resend_event(db, mongo, event):
    if event.status == "completed":
        return
    payload, data = event.payload, event.payload["data"]
    kind = payload.get("type", "")
    if kind == "email.received":
        lead = await routed_lead(db, data.get("to", []))
        if not lead:
            event.status = "ignored"
            await db.commit()
            return
        # Fetch content from Resend: inbound webhook metadata intentionally has no body.
        email_id = data.get("email_id")
        if not email_id:
            raise ValueError("Missing received email ID")
        if event.provider == "simulation":
            received = data
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"https://api.resend.com/emails/receiving/{email_id}",
                    headers={"Authorization": f"Bearer {get_settings().resend_api_key}"})
                response.raise_for_status()
                received = response.json()
        sender = addresses(received.get("from", data.get("from", "")))
        # Correlate both the signed alias and the lead's sender address. Never attach an
        # arbitrary sender's private message to a business just because it knows an alias.
        if not set(sender).intersection(addresses(lead.emails)):
            event.status = "ignored"
            await db.commit()
            return
        body = received.get("text") or ""
        if not body and received.get("html"):
            from html.parser import HTMLParser
            class TextParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, content):
                    self.text.append(content)
            parser = TextParser()
            parser.feed(received["html"])
            body = "\n".join(parser.text)
        message = await db.scalar(select(Communication).where(Communication.event_key == event.event_key))
        if not message:
            message = Communication(user_id=lead.user_id, lead_id=lead.id, channel="email", direction="inbound",
                subject=str(received.get("subject") or data.get("subject") or "")[:500], body=body[:100000], event_key=event.event_key,
                provider_message_id=str(received.get("message_id") or "")[:500])
            db.add(message)
            await db.commit()
        # A real reply cancels stale reminders; new next steps can be scheduled after review.
        followups = (await db.scalars(select(Followup).where(Followup.user_id == lead.user_id,
            Followup.lead_id == lead.id, Followup.status == "scheduled"))).all()
        for followup in followups:
            followup.status = "cancelled"
        await db.commit()
        from app.services.scheduling_service import notify, propose_meeting
        await notify(db, lead.user_id, f"reply:{message.id}", "New email reply", lead.title, lead.id)
        memory = MemoryService(mongo)
        if message.status == "received":
            await classify_reply(db, memory, message)
        if message.status != "suppressed" and not await is_suppressed(db, lead):
            if message.classification in ("interested", "callback"):
                await notify(db, lead.user_id, f"interest:{message.id}", "Interested lead needs your attention", lead.title, lead.id)
            await propose_meeting(db, message, lead)
            if not message.draft_response and message.status != "sent":
                await draft_reply(db, memory, await db.get(User, lead.user_id), lead, message)
    else:
        log = await db.scalar(select(SendLog).where(SendLog.resend_id == data.get("email_id")))
        if log:
            lead = await db.get(Lead, log.lead_id)
            if kind in ("email.bounced", "email.complained", "email.suppressed"):
                log.status = kind.split(".")[1]
                await suppress_contact(db, lead, log.status)
            elif kind == "email.delivered" and log.status in ("pending", "sent"):
                log.status = "delivered"
            await publish(str(log.user_id), "delivery_updated", str(log.lead_id))
    event.status = "completed"
    await db.commit()


async def process_textbee_event(db, mongo, event):
    """Persist a signed Textbee SMS event, then classify it in the durable worker."""
    if event.status == "completed":
        return
    payload = event.payload
    if payload.get("webhookEvent") != "MESSAGE_RECEIVED":
        event.status = "completed"
        await db.commit()
        return
    lead = await routed_sms_lead(db, payload.get("sender"))
    if not lead:
        event.status = "ignored"
        await db.commit()
        return
    if not lead.opted_out:
        lead.status = "replied"
    message = await db.scalar(select(Communication).where(Communication.event_key == event.event_key))
    if not message:
        message = Communication(user_id=lead.user_id, lead_id=lead.id, channel="sms", direction="inbound",
            subject="SMS reply", body=str(payload.get("message") or "")[:10000], event_key=event.event_key)
        db.add(message)
        await db.commit()
    await classify_reply(db, MemoryService(mongo), message)
    event.status = "completed"
    await db.commit()
