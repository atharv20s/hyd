"""Outreach email service using Resend with mandatory opt-out links, idempotency checks, and daily send caps."""

from datetime import datetime, timezone
import os
import uuid
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import TypeAdapter, EmailStr, ValidationError
from app.models.models import SendLog, Lead, User, Communication, Job, Campaign
from app.core.config import get_settings
from app.services.communication_service import is_suppressed, reply_address

settings = get_settings()


class EmailService:
    def __init__(self, db_session: AsyncSession):
        self.session = db_session
        self.api_key = settings.resend_api_key
        self.from_email = settings.email_from
        self.daily_cap = settings.daily_send_cap

    async def get_sends_today_count(self, user_id: uuid.UUID) -> int:
        """Count how many emails the user has already sent today (UTC)."""
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        query = select(func.count(SendLog.id)).where(
            SendLog.user_id == user_id,
            SendLog.channel == "email",
            SendLog.status != "failed",
            SendLog.sent_at >= today_start
        )
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def can_send(self, user_id: uuid.UUID, lead_id: uuid.UUID, message_id=None) -> tuple[bool, str]:
        """Check idempotency and daily limits before sending."""
        # 1. Check daily send cap
        sent_today = await self.get_sends_today_count(user_id)
        if sent_today >= self.daily_cap:
            return False, f"Daily send limit of {self.daily_cap} reached for today ({sent_today} sent)."

        # 2. Check lead status & unsubscribe
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return False, "Lead not found."
        if lead.user_id != user_id:
            return False, "Lead not found."
        if await is_suppressed(self.session, lead):
            return False, f"Lead {lead.title} has opted out/unsubscribed."
        if not lead.email:
            return False, "Lead has no verified email address."

        # 3. Check idempotency: already sent?
        checks = [SendLog.lead_id == lead_id, SendLog.status != "failed"]
        if message_id:
            checks.append(SendLog.idempotency_key == f"reply/{user_id}/{message_id}/v1")
        else:
            if lead.status != "approved":
                return False, "Approve the outreach draft before sending."
            if lead.status == "replied":
                return False, "This business has replied. Continue only through the communication inbox."
            checks.append(SendLog.message_type == "outreach")
        existing = await self.session.scalar(select(SendLog.id).where(*checks).limit(1))
        if existing:
            return False, "Outreach already sent to this lead (idempotency guard)."

        return True, "OK"

    async def send_outreach_email(
        self,
        user_id: uuid.UUID,
        lead: Lead,
        subject: str,
        body_html: str,
        body_text: str,
        message_id=None,
        attachments: list[dict] | None = None,
    ) -> tuple[bool, str]:
        """Sends email via Resend with mandatory opt-out footer and registers send log."""
        if settings.email_provider == "resend" and not settings.email_enabled:
            return False, "Email sending is disabled. Verify a Resend sending domain and enable EMAIL_ENABLED first."
        if settings.email_provider == "resend" and not self.api_key:
            return False, "RESEND_API_KEY is not configured."
        try:
            TypeAdapter(EmailStr).validate_python(lead.email)
        except ValidationError:
            return False, "The lead's email address is invalid."
        # Serialize cap checks and dispatch reservations per operator across replicas.
        await self.session.execute(select(User.id).where(User.id == user_id).with_for_update())
        # Re-read after acquiring the operator lock so a prior opt-out cannot be missed.
        await self.session.refresh(lead)
        job = await self.session.get(Job, lead.job_id) if lead.job_id else None
        campaign = await self.session.get(Campaign, job.campaign_id) if job and job.campaign_id else None
        if campaign and campaign.paused:
            return False, "This campaign is paused. Resume it before sending."
        can_proceed, reason = await self.can_send(user_id, lead.id, message_id)
        if not can_proceed:
            return False, reason

        # Mandated Anti-Spam / Opt-out footer
        import hmac, hashlib
        token = hmac.new(settings.jwt_secret.encode(), f"optout:{lead.id}".encode(), hashlib.sha256).hexdigest()
        opt_out_link = f"{settings.public_base_url.rstrip('/')}/api/v1/leads/{lead.id}/opt-out?token={token}"
        compliance_footer = (
            f"\n\n---\n"
            f"This message was prepared with AI assistance and approved by the sender.\n"
            f"If you'd rather not hear from us, you can opt out instantly here: {opt_out_link}\n"
            f"Sent to {lead.email}."
        )
        full_text = body_text + compliance_footer
        full_html = body_html + f'<p style="font-size:12px;color:#888;margin-top:30px;">If you do not wish to receive further emails, <a href="{opt_out_link}">click here to unsubscribe</a>.</p>'

        idempotency_key = f"reply/{user_id}/{message_id}/v1" if message_id else f"outreach/{user_id}/{lead.id}/v1"
        thread_headers = {}
        if isinstance(message_id, uuid.UUID):
            incoming = await self.session.get(Communication, message_id)
            if incoming and incoming.user_id == user_id and incoming.provider_message_id:
                value = incoming.provider_message_id
                if len(value) <= 500 and "\r" not in value and "\n" not in value:
                    thread_headers = {"In-Reply-To": value, "References": value}
        log_entry = await self.session.scalar(select(SendLog).where(SendLog.idempotency_key == idempotency_key))
        if log_entry is None:
            log_entry = SendLog(user_id=user_id, lead_id=lead.id, channel="email", recipient=lead.email, message_type="reply" if message_id else "outreach",
                subject=subject, body=full_text, status="pending", idempotency_key=idempotency_key)
            self.session.add(log_entry)
        else:
            log_entry.status = "pending"
            log_entry.subject = subject
            log_entry.body = full_text
            log_entry.error_message = None
        log_entry.sent_at = datetime.now(timezone.utc)
        await self.session.commit()
        resend_id = None
        send_status = "sent"
        error_msg = None

        try:
            resend_payload = {
                "from": self.from_email, "to": [lead.email], "subject": subject,
                "html": full_html, "text": full_text,
                "headers": {"List-Unsubscribe": f"<{opt_out_link}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click", **thread_headers},
                **({"reply_to": reply_address(lead)} if reply_address(lead) else {}),
                **({"attachments": attachments} if attachments else {}),
            }
            from app.services.email_provider import MailHogEmailProvider, ResendEmailProvider
            provider = (MailHogEmailProvider(settings.smtp_host, settings.smtp_port)
                if settings.email_provider == "smtp" else ResendEmailProvider(self.api_key))
            resend_id = await provider.send(resend_payload, idempotency_key)
        except httpx.HTTPStatusError:
            send_status = "failed"
            error_msg = "Resend rejected the email. Check the verified sender and provider logs."
        except Exception:
            send_status = "pending"
            error_msg = "Delivery status is uncertain. Check the provider before retrying to prevent duplicate outreach."

        # Record in SendLog
        log_entry.status = send_status
        log_entry.resend_id = resend_id
        log_entry.error_message = error_msg

        if send_status == "sent" and not message_id:
            lead.status = "contacted"
            lead.contacted_at = datetime.now(timezone.utc)

        await self.session.commit()
        return send_status == "sent", error_msg or f"Email sent successfully (ID: {resend_id})"

    async def send_calendar_invite_email(
        self,
        user_id: uuid.UUID,
        lead: Lead,
        event,
        notes: str = "",
        message_id=None,
    ) -> tuple[bool, str]:
        """Dispatches an email with an RFC 5545 ICS calendar invite attached via Resend."""
        from app.services.calendar_service import generate_ics, format_slot_ist
        from html import escape

        ics_text, b64_ics = generate_ics(
            title=event.title,
            start_time=event.start_time,
            end_time=event.end_time,
            organizer_email=self.from_email,
            attendee_email=lead.email,
            description=event.description or notes,
            location=event.location or "Online Meeting",
            uid=event.ics_uid,
        )

        formatted_time = format_slot_ist(event.start_time)
        subject = f"Confirmed: {event.title} on {formatted_time}"
        
        body_text = (
            f"Hi,\n\n"
            f"Your session has been scheduled:\n\n"
            f"• Title: {event.title}\n"
            f"• When: {formatted_time}\n"
            f"• Location/Link: {event.location or event.meeting_link or 'Details sent via email'}\n\n"
            f"{notes or 'We have attached a calendar invitation (.ics) to this email so you can add it to your calendar with one click.'}\n\n"
            f"Best regards,\nGrowth Desk"
        )

        body_html = (
            f"<div style='font-family:sans-serif;line-height:1.6;color:#222;'>"
            f"<h2 style='color:#1a365d;'>Meeting Confirmed</h2>"
            f"<p>Your session has been scheduled:</p>"
            f"<div style='background:#f7fafc;border-left:4px solid #3182ce;padding:12px 16px;margin:16px 0;'>"
            f"<strong>{escape(event.title)}</strong><br>"
            f"<span>📅 {formatted_time}</span><br>"
            f"<span>📍 {escape(event.location or 'Online Meeting')}</span>"
            f"</div>"
            f"<p>{escape(notes or 'An interactive calendar invitation (.ics) is attached to this email.')}</p>"
            f"<p style='color:#718096;font-size:13px;'>Click 'Add to Calendar' in your email client to save this event.</p>"
            f"</div>"
        )

        attachments = [
            {
                "filename": f"invite-{str(event.id)[:8]}.ics",
                "content": b64_ics,
            }
        ]

        return await self.send_outreach_email(
            user_id=user_id,
            lead=lead,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            message_id=message_id,
            attachments=attachments,
        )
