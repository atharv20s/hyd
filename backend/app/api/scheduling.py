"""Operator notifications, meeting confirmation and scheduled follow-up drafting."""
import base64
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import get_operator_user
from app.models.models import Meeting, Lead, User, Followup, WorkItem, Notification, Communication
from app.services.communication_service import is_suppressed, publish
from app.services.scheduling_service import aware, calendar_text
from app.services.email_service import EmailService

router = APIRouter(tags=["Scheduling & Notifications"])


class MeetingConfirmation(BaseModel):
    starts_at: datetime
    duration_minutes: int = Field(default=30, ge=10, le=240)
    location: str = Field(min_length=1, max_length=1000)

    @field_validator("starts_at")
    @classmethod
    def future(cls, value):
        if value.tzinfo is None or value <= datetime.now(timezone.utc):
            raise ValueError("Choose a future time with a timezone.")
        return value


class FollowupRequest(BaseModel):
    lead_id: uuid.UUID
    due_at: datetime

    @field_validator("due_at")
    @classmethod
    def future(cls, value):
        if value.tzinfo is None or value <= datetime.now(timezone.utc):
            raise ValueError("Choose a future time with a timezone.")
        return value


async def own_meeting(db, user, meeting_id):
    row = await db.scalar(select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user.id).with_for_update())
    if not row:
        raise HTTPException(404, "Meeting not found.")
    return row


@router.get("/notifications")
async def notifications(user=Depends(get_operator_user), db=Depends(get_db)):
    rows = (await db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(50))).all()
    return [{"id": str(row.id), "lead_id": str(row.lead_id) if row.lead_id else None, "title": row.title,
        "body": row.body, "read": row.read, "created_at": row.created_at} for row in rows]


@router.post("/notifications/{notification_id}/read")
async def read_notification(notification_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    row = await db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if not row:
        raise HTTPException(404, "Notification not found.")
    row.read = True
    await db.commit()
    return {"read": True}


@router.get("/meetings")
async def meetings(user=Depends(get_operator_user), db=Depends(get_db)):
    rows = (await db.execute(select(Meeting, Lead.title).join(Lead, Lead.id == Meeting.lead_id)
        .where(Meeting.user_id == user.id).order_by(Meeting.created_at.desc()).limit(100))).all()
    return [{"id": str(row.id), "lead_id": str(row.lead_id), "message_id": str(row.message_id), "business_name": name,
        "title": row.title, "starts_at": row.starts_at, "duration_minutes": row.duration_minutes,
        "location": row.location, "source_quote": row.source_quote, "status": row.status} for row, name in rows]


@router.post("/meetings/{meeting_id}/confirm")
async def confirm(meeting_id: uuid.UUID, payload: MeetingConfirmation, user=Depends(get_operator_user), db=Depends(get_db)):
    # A single operator lock prevents concurrent confirmations from double-booking.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    row = await own_meeting(db, user, meeting_id)
    if row.status == "cancelled":
        raise HTTPException(409, "This request was declined or cancelled.")
    if row.status == "confirmed":
        raise HTTPException(409, "This meeting is already confirmed.")
    if await is_suppressed(db, await db.get(Lead, row.lead_id)):
        raise HTTPException(409, "This contact opted out.")
    end = payload.starts_at + timedelta(minutes=payload.duration_minutes)
    existing = (await db.scalars(select(Meeting).where(Meeting.user_id == user.id, Meeting.status == "confirmed", Meeting.id != row.id))).all()
    if any(aware(item.starts_at) < end and aware(item.starts_at) + timedelta(minutes=item.duration_minutes) > payload.starts_at for item in existing):
        raise HTTPException(409, "That time overlaps another confirmed meeting in this workspace.")
    row.starts_at, row.duration_minutes, row.location = payload.starts_at, payload.duration_minutes, payload.location.strip()
    row.status = "confirmed"
    await db.commit()
    await publish(str(user.id), "meeting_confirmed", str(row.id))
    return {"status": row.status, "message": "Meeting confirmed in your workspace. You can now send its calendar invitation."}


@router.post("/meetings/{meeting_id}/decline")
async def decline(meeting_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    row = await own_meeting(db, user, meeting_id)
    if row.status == "confirmed":
        raise HTTPException(409, "A confirmed meeting cannot be silently declined. Contact the attendee to arrange a change.")
    row.status = "cancelled"
    await db.commit()
    return {"status": row.status}


@router.get("/meetings/{meeting_id}/calendar")
async def download(meeting_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    row = await own_meeting(db, user, meeting_id)
    if row.status != "confirmed":
        raise HTTPException(409, "Confirm the meeting before creating its calendar file.")
    return {"filename": f"meeting-{row.id}.ics", "content": calendar_text(row)}


@router.post("/meetings/{meeting_id}/send-invitation")
async def invitation(meeting_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    row = await own_meeting(db, user, meeting_id)
    if row.status != "confirmed":
        raise HTTPException(409, "Confirm the meeting before sending its invitation.")
    lead = await db.get(Lead, row.lead_id)
    from zoneinfo import ZoneInfo
    time_label = aware(row.starts_at).astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
    body = f"Your meeting with {user.full_name or 'our team'} is confirmed for {time_label}.\nDuration: {row.duration_minutes} minutes.\nLocation: {row.location}\nPlease use the attached calendar file to add it to your calendar."
    ok, detail = await EmailService(db).send_outreach_email(user.id, lead, row.title,
        escape(body).replace("\n", "<br>"), body, f"meeting-{row.id}",
        attachments=[{"filename": "meeting.ics", "content": base64.b64encode(calendar_text(row, lead.email).encode()).decode()}])
    return {"sent": ok, "message": detail}


@router.get("/followups")
async def followups(user=Depends(get_operator_user), db=Depends(get_db)):
    rows = (await db.execute(select(Followup, Lead.title).join(Lead, Lead.id == Followup.lead_id)
        .where(Followup.user_id == user.id).order_by(Followup.due_at.desc()).limit(100))).all()
    return [{"id": str(row.id), "lead_id": str(row.lead_id), "business_name": name,
             "due_at": row.due_at, "status": row.status} for row, name in rows]


@router.post("/followups", status_code=202)
async def schedule(payload: FollowupRequest, user=Depends(get_operator_user), db=Depends(get_db)):
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    lead = await db.scalar(select(Lead).where(Lead.id == payload.lead_id, Lead.user_id == user.id))
    if not lead:
        raise HTTPException(404, "Business not found.")
    if await is_suppressed(db, lead):
        raise HTTPException(409, "This contact opted out.")
    existing = await db.scalar(select(Followup.id).where(Followup.user_id == user.id, Followup.lead_id == lead.id, Followup.status == "scheduled"))
    if existing:
        raise HTTPException(409, "This business already has a scheduled follow-up.")
    row = Followup(user_id=user.id, lead_id=lead.id, due_at=payload.due_at)
    db.add(row)
    await db.flush()
    db.add(WorkItem(user_id=user.id, kind="followup", payload={"followup_id": str(row.id)}, available_at=row.due_at))
    await db.commit()
    return {"id": str(row.id), "status": row.status}


@router.post("/followups/{followup_id}/cancel")
async def cancel(followup_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    row = await db.scalar(select(Followup).where(Followup.id == followup_id, Followup.user_id == user.id).with_for_update())
    if not row:
        raise HTTPException(404, "Follow-up not found.")
    row.status = "cancelled"
    await db.commit()
    return {"status": row.status}
