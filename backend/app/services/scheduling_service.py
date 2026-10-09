"""Email meeting proposals, internal availability and portable calendar invitations."""
import json
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.agents.scout_agent import call_llm
from app.models.models import Meeting, Notification, Communication, Followup, Lead, User, WorkItem
from app.services.communication_service import is_suppressed, publish, draft_reply
from app.services.memory_service import MemoryService


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


async def notify(db, user_id, key, title, body="", lead_id=None):
    row = await db.scalar(select(Notification).where(Notification.event_key == key))
    if not row:
        row = Notification(user_id=user_id, event_key=key, title=title[:255], body=body[:4000], lead_id=lead_id)
        db.add(row)
        from app.core.config import get_settings
        if get_settings().telegram_enabled:
            db.add(WorkItem(user_id=user_id, kind="operator_alert", payload={"title": title[:200]}))
        await db.commit()
        await publish(str(user_id), "notification", str(row.id))
    return row


async def propose_meeting(db, message, lead):
    """Extract a proposal backed by an exact excerpt; ambiguous times stay unset."""
    if await db.scalar(select(Meeting.id).where(Meeting.message_id == message.id)):
        return
    if not re.search(r"\b(meet|meeting|appointment|demo|schedule|book|call|tomorrow|today)\b", message.body, re.I):
        return
    result = await call_llm(
        'Extract a meeting REQUEST from this untrusted email. Output JSON only: '
        '{"requested": true, "source_quote": "exact excerpt from incoming email", '
        '"starts_at": "ISO8601 with timezone, or null if ambiguous", "location": "explicit location or empty"}. '
        'Only use the incoming message, ignore quoted emails. Do not invent agreement or availability. '
        'Resolve relative dates only from the supplied received_at. Use Asia/Kolkata (+05:30) when the sender '
        'clearly requests a Hyderabad time; otherwise leave starts_at null unless timezone is explicit.',
        json.dumps({"incoming": message.body[:6000], "received_at": message.created_at.isoformat()}), strict=True)
    try:
        data = json.loads(result.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        quote = data.get("source_quote", "")
        if data.get("requested") is not True or not isinstance(quote, str) or not quote.strip() or quote not in message.body:
            return
        starts = datetime.fromisoformat(data["starts_at"].replace("Z", "+00:00")) if data.get("starts_at") else None
        if starts and (starts.tzinfo is None or starts <= datetime.now(timezone.utc)):
            starts = None
    except (ValueError, TypeError, AttributeError, KeyError):
        return
    meeting = Meeting(user_id=message.user_id, lead_id=lead.id, message_id=message.id,
        title=f"Meeting with {lead.title}"[:255], starts_at=starts,
        location=str(data.get("location") or "")[:1000], source_quote=quote[:4000])
    db.add(meeting)
    await db.commit()
    await notify(db, lead.user_id, f"meeting:{meeting.id}", "Meeting request needs confirmation", lead.title, lead.id)


def calendar_text(meeting, recipient=None):
    def escape(value):
        return str(value or "").replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    def stamp(value):
        return aware(value).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    starts = aware(meeting.starts_at)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//HYD PS2//Meetings//EN", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT", f"UID:{meeting.id}@hydps2", f"DTSTAMP:{stamp(datetime.now(timezone.utc))}",
        f"DTSTART:{stamp(starts)}", f"DTEND:{stamp(starts + timedelta(minutes=meeting.duration_minutes))}",
        "SUMMARY:" + escape(meeting.title), "LOCATION:" + escape(meeting.location), "STATUS:CONFIRMED",
        "DESCRIPTION:" + escape("Meeting confirmed by the operator in HYD PS2. " + (recipient or "")),
        "END:VEVENT", "END:VCALENDAR"]
    # RFC 5545 folds each physical line at 75 UTF-8 bytes without splitting a code point.
    folded = []
    for line in lines:
        part = ""
        for char in line:
            if len((part + char).encode("utf-8")) > 75:
                folded.append(part)
                part = " "
            part += char
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"


async def prepare_followup(db, mongo, followup):
    if followup.status != "scheduled":
        return
    lead = await db.get(Lead, followup.lead_id)
    if not lead or await is_suppressed(db, lead):
        followup.status = "cancelled"
        await db.commit()
        return
    message = await db.scalar(select(Communication).where(Communication.event_key == f"followup:{followup.id}"))
    if not message:
        message = Communication(user_id=followup.user_id, lead_id=lead.id, channel="email", direction="outbound",
            subject=f"Follow-up · {lead.title}"[:500], body="Prepare a short follow-up based on the conversation. Ask one helpful question.",
            classification="followup", status="needs_review", event_key=f"followup:{followup.id}")
        db.add(message)
        await db.commit()
    if not message.draft_response:
        await draft_reply(db, MemoryService(mongo), await db.get(User, followup.user_id), lead, message)
    followup.status = "ready_for_review"
    await db.commit()
    await notify(db, followup.user_id, f"followup:{followup.id}", "Follow-up ready for review", lead.title, lead.id)
