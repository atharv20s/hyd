"""Email transports: isolated MailHog by default, explicit Resend live mode."""
import asyncio
import hashlib
import smtplib
from email.message import EmailMessage
from typing import Protocol
import httpx


class EmailProvider(Protocol):
    async def send(self, payload: dict, idempotency_key: str) -> str: ...


class ResendEmailProvider:
    def __init__(self, api_key):
        self.api_key = api_key

    async def send(self, payload, idempotency_key):
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://api.resend.com/emails", json=payload,
                headers={"Authorization": f"Bearer {self.api_key}", "Idempotency-Key": idempotency_key})
            response.raise_for_status()
            return response.json()["id"]


class MailHogEmailProvider:
    def __init__(self, host="mailhog", port=1025):
        if host not in ("mailhog", "localhost", "127.0.0.1") or port != 1025:
            raise ValueError("Dry-run SMTP is restricted to the local MailHog inbox")
        self.host, self.port = host, port

    async def send(self, payload, idempotency_key):
        message_id = hashlib.sha256(idempotency_key.encode()).hexdigest() + "@dry-run.local"
        message = EmailMessage()
        message["From"], message["To"] = payload["from"], ", ".join(payload["to"])
        message["Subject"], message["Message-ID"] = payload["subject"], f"<{message_id}>"
        message["X-HYDPS2-Dry-Run"] = "true"
        if payload.get("reply_to"):
            message["Reply-To"] = payload["reply_to"]
        for name, value in payload.get("headers", {}).items():
            message[name] = value
        message.set_content(payload["text"])
        message.add_alternative(payload["html"], subtype="html")
        import base64
        for attachment in payload.get("attachments", []):
            message.add_attachment(base64.b64decode(attachment["content"]), maintype="application",
                subtype="octet-stream", filename=attachment["filename"])
        def deliver():
            with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.send_message(message)
        await asyncio.to_thread(deliver)
        return "dry-run:" + message_id
