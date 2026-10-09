"""Optional installation-level alert; disabled unless explicitly configured."""
import httpx
from app.core.config import get_settings


async def send_operator_alert(title: str) -> str:
    settings = get_settings()
    if not settings.telegram_enabled:
        return "dry_run"
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise ValueError("Telegram alert destination is not configured")
    # Never forward business reply bodies or tenant identifiers to a shared chat.
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": "HYD PS2: " + title[:200] + ". Open your workspace to review."})
        response.raise_for_status()
        if response.json().get("ok") is not True:
            raise ValueError("Telegram did not accept the alert")
    return "sent"
