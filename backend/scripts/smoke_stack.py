"""Exercise the running API/worker using synthetic data; clean up only our test accounts."""
import asyncio
import json
import secrets
import sys
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from sqlalchemy import delete, select
from app.core.database import async_session, init_mongo, get_mongo_db, engine
from app.models.models import User, Lead, WorkItem, Job, Communication, SendLog, ContactSuppression


async def main():
    nonce = secrets.token_hex(5)
    accounts = []
    await init_mongo()
    mongo = await get_mongo_db()
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000/api/v1", timeout=90) as client:
            async def api(method, path, body=None, headers=None):
                response = await client.request(method, path, json=body, headers=headers)
                response.raise_for_status()
                return response.json()
            operator = await api("POST", "/auth/signup", {"email": f"hydps2-smoke-{nonce}@example.com", "password": secrets.token_urlsafe(24), "full_name": "Synthetic Stack Check"})
            accounts.append(operator["user"])
            headers = {"Authorization": f"Bearer {operator['access_token']}"}
            await api("POST", "/onboarding", {"service_description": "AI receptionist and appointment request intake; all bookings need staff confirmation", "target_clients": "Salons in Hyderabad"}, headers)
            status = await api("GET", "/system/status", headers=headers)
            assert status["dependencies"] == {"database": "online", "memory": "online", "cache": "online"}, status
            assert status["worker"] == "online", status
            from websockets.asyncio.client import connect
            from redis.asyncio import Redis
            from app.core.config import get_settings
            ticket = await api("POST", "/communications/ws-ticket", {}, headers)
            async with connect("ws://127.0.0.1:8000/api/v1/communications/ws", origin="http://localhost:3000", open_timeout=10) as socket:
                await socket.send(json.dumps(ticket))
                assert json.loads(await socket.recv())["type"] == "connected"
                cache = Redis.from_url(get_settings().redis_url)
                await cache.publish(f"hydps2:events:{operator['user']['id']}", '{"type":"smoke_event","id":"test"}')
                assert json.loads(await asyncio.wait_for(socket.recv(), 5))["type"] == "smoke_event"
                await cache.aclose()
            imported = await api("POST", "/leads/import", {"csv_text": f"title,address,category,emails\nHYD Synthetic Salon {nonce},Banjara Hills Hyderabad,Hair salon,smoke-{nonce}@example.com"}, headers)
            assert imported["imported"] == 1
            lead = (await api("GET", "/leads", headers=headers))[0]
            queued = await api("POST", "/leads/batch-draft", {"lead_ids": [lead["id"]]}, headers)
            assert queued["queued"] == 1
            deadline = asyncio.get_running_loop().time() + 150
            while asyncio.get_running_loop().time() < deadline:
                lead = await api("GET", f"/leads/{lead['id']}", headers=headers)
                if lead["outreach_status"] in ("drafted", "draft_failed"):
                    break
                await asyncio.sleep(2)
            assert lead["outreach_status"] == "drafted" and lead["draft_body"], lead
            approved = await api("POST", f"/leads/{lead['id']}/decision", {"action": "edited", "edited_body": "Hi team, can we discuss appointment intake? This is an AI-assisted draft.", "feedback_tag": "One short question", "send_email": False}, headers)
            assert approved["status"] == "approved"
            assert "No email" in approved["message"]
            assert "One short question" in (await api("GET", "/decisions/rules", headers=headers))["rules"]
            operator_voice = await api("POST", "/voice/session", {}, headers)
            assert operator_voice["conversation_token"]
            invitation = await api("POST", f"/leads/{lead['id']}/invite", {}, headers)
            invite = invitation["url"].split("invite=")[1]
            guest = await api("POST", "/auth/signup", {"email": f"hydps2-client-smoke-{nonce}@example.com", "password": secrets.token_urlsafe(24), "full_name": "Synthetic Client", "invite_token": invite})
            accounts.append(guest["user"])
            guest_headers = {"Authorization": f"Bearer {guest['access_token']}"}
            assert (await client.get("/leads", headers=guest_headers)).status_code == 403
            await api("POST", "/client/chat", {"message": "Please ask the human operator which Hyderabad area they serve."}, guest_headers)
            portal = (await api("GET", "/communications", headers=headers))[0]
            await api("POST", f"/communications/{portal['id']}/action", {"action": "send_reply", "body": "This is a test response from your operator. Which area do you need?"}, headers)
            history = await api("GET", "/client/history", headers=guest_headers)
            assert history[-1]["sender_type"] == "operator"
            intake = await api("POST", "/client/intake", {"service": "Appointment intake demo", "location": "Banjara Hills", "preferred_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "contact": guest["user"]["email"]}, guest_headers)
            await api("POST", f"/client-requests/{intake['request_id']}/decision", {"status": "confirmed"}, headers)
            assert (await api("GET", "/client/requests", headers=guest_headers))[0]["status"] == "confirmed"
            assert (await api("POST", "/voice/session", {}, guest_headers))["conversation_token"]
            async with async_session() as db:
                assert not (await db.scalars(select(SendLog).where(SendLog.user_id == uuid.UUID(operator["user"]["id"])))).all()
            print(json.dumps({"stack": "passed", "dependencies": status["dependencies"], "worker": "online",
                "queued_live_llm_draft": "passed", "approval_and_learning": "passed", "role_isolation": "passed",
                "client_chat_human_reply": "passed", "intake_confirmation": "passed", "voice_sessions": "both ready", "live_redis_websocket": "passed", "emails_sent": 0}))
    finally:
        if accounts:
            ids = [uuid.UUID(account["id"]) for account in accounts]
            operator_id = str(ids[0])
            async with async_session() as db:
                for model in (Communication, SendLog, ContactSuppression, WorkItem, Lead, Job):
                    await db.execute(delete(model).where(model.user_id.in_(ids)))
                await db.execute(delete(User).where(User.id.in_(ids)))
                await db.commit()
            for name, query in (
                ("human_decisions", {"operator_id": operator_id}), ("operator_rules", {"operator_id": operator_id}),
                ("intake_requests", {"operator_id": operator_id}), ("voice_sessions", {"operator_id": operator_id}),
                ("chat_transcripts", {"$or": [{"metadata.operator_id": operator_id}, {"session_id": f"operator:{operator_id}"}]}),
            ):
                await mongo[name].delete_many(query)
            print("Synthetic test accounts and owned test records cleaned up; existing accounts untouched.")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
