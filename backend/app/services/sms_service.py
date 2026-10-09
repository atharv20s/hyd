"""Approval-only SMS replies sent through the operator's Textbee Android gateway."""
from datetime import datetime, timezone
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.models import Lead, SendLog, User
from app.services.communication_service import is_suppressed, normalize_phone


class TextbeeSMSService:
    """Send one reviewed reply at a time; never used for initial outreach."""

    def __init__(self, db_session: AsyncSession):
        self.session = db_session
        self.settings = get_settings()

    async def _sent_today(self, user_id: uuid.UUID) -> int:
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return (await self.session.scalar(select(func.count(SendLog.id)).where(
            SendLog.user_id == user_id,
            SendLog.channel == "sms",
            SendLog.status != "failed",
            SendLog.sent_at >= day_start,
        ))) or 0

    async def send_reply(self, user_id: uuid.UUID, lead: Lead, body: str, message_id: uuid.UUID) -> tuple[bool, str]:
        """Queue a human-approved reply. A timeout is never retried automatically."""
        if not self.settings.sms_enabled:
            return False, "SMS is disabled. Pair the Android Textbee device, configure its webhook, then enable SMS_ENABLED."
        if not self.settings.textbee_api_key:
            return False, "TEXTBEE_API_KEY is not configured."
        recipient = normalize_phone(lead.phone)
        if not recipient:
            return False, "This business has no valid phone number in international format."

        # Lock by operator to make the cap, the opt-out check and reservation race-safe.
        await self.session.execute(select(User.id).where(User.id == user_id).with_for_update())
        await self.session.refresh(lead)
        if lead.user_id != user_id:
            return False, "Lead not found."
        if await is_suppressed(self.session, lead):
            return False, "This contact opted out. No SMS reply is allowed."
        if await self._sent_today(user_id) >= self.settings.daily_sms_cap:
            return False, f"Daily SMS limit of {self.settings.daily_sms_cap} reached."

        key = f"sms-reply/{user_id}/{message_id}/v1"
        existing = await self.session.scalar(select(SendLog).where(SendLog.idempotency_key == key))
        if existing:
            return False, "This SMS reply was already queued or sent. Its delivery state must be checked before another attempt."

        # Brief disclosure/opt-out wording is included for a durable, traceable conversation.
        full_body = body.strip() + "\n\nReply STOP to stop messages."
        log = SendLog(user_id=user_id, lead_id=lead.id, channel="sms", recipient=recipient,
            message_type="reply", body=full_body, body_preview=full_body[:500], status="pending", idempotency_key=key)
        self.session.add(log)
        await self.session.commit()

        request_body = {"recipients": [recipient], "message": full_body}
        if self.settings.textbee_device_id:
            request_body["deviceId"] = self.settings.textbee_device_id
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.post(
                    f"{self.settings.textbee_base_url.rstrip('/')}/gateway/send-sms",
                    headers={"x-api-key": self.settings.textbee_api_key, "Content-Type": "application/json"},
                    json=request_body,
                )
            if response.status_code == 200 and response.json().get("data", {}).get("success") is True:
                data = response.json()["data"]
                log.status = "sent"  # Textbee accepted the queue; a carrier delivery report may arrive later.
                log.resend_id = str(data.get("smsBatchId") or "")[:255]
                await self.session.commit()
                return True, "SMS queued on your Textbee Android phone."
            log.status = "failed"
            log.error_message = "Textbee rejected the SMS. Check that the phone is paired, online, and within its plan limit."
            await self.session.commit()
            return False, log.error_message
        except (httpx.HTTPError, ValueError):
            # The provider may have accepted the request before the connection failed.
            log.error_message = "SMS delivery state is uncertain. Check Textbee before retrying; no duplicate will be sent automatically."
            await self.session.commit()
            return False, log.error_message
